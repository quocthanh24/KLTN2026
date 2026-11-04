"""Acceptance safety tests only. No real GPT answers or network calls."""
import copy
import hashlib
import io
import tempfile
import time
import unittest
from pathlib import Path
from app.core.exceptions import AppError
from app.services.ai_chat_v2_budget import BudgetLedger
from app.services.ai_chat_v2_pilot_guard import PilotLedger,PilotBoundary,validate_plan
from app.services.ai_observability import v2_usage_sink


class PilotPreflightTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        import base64
        self.folder=tempfile.TemporaryDirectory()
        self.ledger=PilotLedger(Path(self.folder.name)/'offline-only.sqlite3')
        self.ledger.approve('OFFLINE_ONLY','channel','gpt-6-luna',1_000_000,1_000_000,1,time.time()+600,600_000,2_000_000)
        image=io.BytesIO();Image.new('RGB',(30,30),'blue').save(image,format='JPEG')
        self.plan={'model':'gpt-6-luna','model_input_ceiling':1_050_000,'cap_microusd':1_000_000,
            'max_provider_calls':30,'max_images_per_call':2,'max_output_tokens':4096,'max_resume_steps':0,
            'input_rate_microusd_per_million':600_000,'output_rate_microusd_per_million':2_000_000,
            'channel_id':'channel','budget_approval_id':'OFFLINE_ONLY','owner_approval_reference':None,
            'gold_owner_confirmed':False,'channel_owner_confirmed':False,
            'images':[{'sha256':hashlib.sha256(image.getvalue()).hexdigest(),'physical_page':7}]}
        self.messages=[{'role':'user','content':[{'type':'input_text','text':'Untrusted original image unit'},
            {'type':'input_image','detail':'high','image_url':'data:image/jpeg;base64,'+base64.b64encode(image.getvalue()).decode()}]}]
        self.kwargs={'model':'gpt-6-luna','service_tier':'default','store':False,'stream':True,'max_output_tokens':4096,'input':self.messages}
        self.boundary=PilotBoundary(self.plan,self.ledger,Path(self.folder.name)/'trace.jsonl')

    def tearDown(self):self.folder.cleanup()

    def reserve(self):return self.ledger.reserve('OFFLINE_ONLY','channel','gpt-6-luna','job',PilotLedger.input_upper_bound(self.messages),4096)

    def test_new_development_grant_cap_does_not_expand_historical_approval(self):
        plan=copy.deepcopy(self.plan);plan['cap_microusd']=2_000_000
        with self.assertRaises(AppError):validate_plan(plan)
        plan.update(grant_kind='development_manual_v2',max_provider_calls=60,max_images_per_call=6,
            automated_cap_microusd=1_000_000,max_automated_calls=40,expires_at_epoch=time.time()+3600,
            approved_documents={'doc':'revision'},owner_approval_reference='NEW_USER_2USD',channel_owner_confirmed=True)
        validate_plan(plan,approved=True,owner_reference='NEW_USER_2USD')
        plan['cap_microusd']=2_000_001
        with self.assertRaises(AppError):validate_plan(plan)

    def test_automated_spending_ceiling_rejects_before_creating_reservation(self):
        self.ledger.spending_cap=10
        with self.assertRaises(AppError):self.reserve()
        self.assertEqual(self.ledger.snapshot('OFFLINE_ONLY'),[])

    def test_local_native_tools_allowed_but_hosted_tools_denied_and_canary_cap_bounded(self):
        from app.services.ai_chat_v2_tool_contract import tool_schemas
        scoped=copy.deepcopy(self.plan);scoped['max_provider_calls']=4
        validate_plan(scoped)
        scoped['max_provider_calls']=31
        with self.assertRaises(AppError):validate_plan(scoped)
        self.reserve();token=v2_usage_sink.set([])
        try:
            bad={**self.kwargs,'tools':[{'type':'web_search'}]}
            with self.assertRaises(AppError):self.boundary.before_sdk(bad)
            good={**self.kwargs,'tools':tool_schemas(),'tool_choice':'auto','parallel_tool_calls':False}
            record=self.boundary.before_sdk(good)
            self.assertEqual(record['ordinal'],1)
        finally:v2_usage_sink.reset(token)

    def test_documented_full_context_reservation_fits_one_dollar_without_modifying_base_ledger(self):
        self.assertGreater(BudgetLedger.input_upper_bound(self.messages),4_000_000)
        self.assertEqual(PilotLedger.input_upper_bound(self.messages),1_050_000)
        self.reserve()
        self.assertEqual(self.ledger.snapshot('OFFLINE_ONLY')[0]['amount'],638192)

    def test_approval_and_case_channel_confirmation_required(self):
        for owner in ('','old-smoke-approval'):
            with self.assertRaises(AppError):validate_plan(self.plan,approved=True,owner_reference=owner)

    def test_remote_file_and_unknown_content_remain_rejected(self):
        with self.assertRaises(AppError):PilotLedger.input_upper_bound([{'content':[{'type':'input_file','file_url':'https://private'}]}])

    def test_legacy_or_unfunded_dispatch_never_reaches_sdk(self):
        with self.assertRaises(AppError):self.boundary.before_sdk(self.kwargs)
        token=v2_usage_sink.set([])
        try:
            with self.assertRaises(AppError):self.boundary.before_sdk(self.kwargs)
        finally:v2_usage_sink.reset(token)

    def test_hash_mode_model_and_image_count_are_frozen(self):
        self.reserve();token=v2_usage_sink.set([])
        try:
            for field,value in [('model','another-model'),('service_tier','priority'),('max_output_tokens',4097)]:
                bad={**self.kwargs,field:value}
                with self.assertRaises(AppError):self.boundary.before_sdk(bad)
            bad=copy.deepcopy(self.kwargs);bad['input'][0]['content'][1]['detail']='low'
            with self.assertRaises(AppError):self.boundary.before_sdk(bad)
            bad=copy.deepcopy(self.kwargs);bad['input'][0]['content']*=3
            with self.assertRaises(AppError):self.boundary.before_sdk(bad)
            bad_plan=copy.deepcopy(self.plan);bad_plan['images'][0]['sha256']='wrong'
            with self.assertRaises(AppError):PilotBoundary(bad_plan,self.ledger,Path(self.folder.name)/'bad').before_sdk(self.kwargs)
        finally:v2_usage_sink.reset(token)

    def test_successful_boundary_has_only_hash_metadata_and_blocks_sdk_retry(self):
        self.reserve();token=v2_usage_sink.set([])
        try:
            result=self.boundary.before_sdk(self.kwargs)
            self.assertEqual(result['original_images'][0]['physical_page'],7)
            with self.assertRaises(AppError) as e:self.boundary.before_sdk(self.kwargs)
            self.assertEqual(e.exception.code,'PILOT_RETRY_DISABLED')
            self.assertNotIn('base64',self.boundary.trace_path.read_text())
        finally:v2_usage_sink.reset(token)

    def test_unknown_usage_preserves_full_reservation_and_blocks_next_call(self):
        identity=self.reserve();self.ledger.settle(identity)
        self.ledger.recover_unknown(0)
        self.assertEqual(self.ledger.snapshot('OFFLINE_ONLY')[0]['amount'],638192)
        self.assertEqual(self.ledger.snapshot('OFFLINE_ONLY')[0]['state'],'unknown')
        with self.assertRaises(AppError):self.reserve()

    def test_total_attempt_cap_persists_in_ledger(self):
        token=v2_usage_sink.set([])
        try:
            for _ in range(30):
                identity=self.reserve();self.boundary.before_sdk(self.kwargs)
                self.ledger.settle(identity,{'model':'gpt-6-luna','input_tokens':1,'output_tokens':1})
            self.reserve()
            with self.assertRaises(AppError) as e:self.boundary.before_sdk(self.kwargs)
            self.assertEqual(e.exception.code,'PILOT_CALL_CAP')
        finally:v2_usage_sink.reset(token)
