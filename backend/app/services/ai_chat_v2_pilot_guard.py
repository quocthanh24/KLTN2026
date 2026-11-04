"""Opt-in acceptance-process guards. Never imported by normal application startup.

No model/provider/prompt changes. Keep the production ledger untouched. The pilot
uses the documented full model context as a conservative INPUT ceiling, not an
estimate of image understanding or a guessed patch multiplier.
"""
import base64
import hashlib
import io
import json
import time
from pathlib import Path
from app.core.exceptions import AppError
from app.services.ai_chat_v2_budget import BudgetLedger
from app.services.ai_observability import v2_usage_sink

CONTEXT_CEILINGS={'gpt-6-luna':1_050_000}
CONTEXT_SOURCE='https://developers.openai.com/api/docs/models/gpt-6-luna'


class PilotLedger(BudgetLedger):
    """Only installed inside the isolated, approved acceptance process."""
    @staticmethod
    def input_upper_bound(messages):
        # First run the existing validator (reject URLs/files/unknown parts).
        # Successful requests cannot exceed the documented whole-model context.
        # Reserve that ENTIRE context if the old pixel bound is larger.
        return min(BudgetLedger.input_upper_bound(messages),CONTEXT_CEILINGS['gpt-6-luna'])


def validate_plan(plan, *, approved=False, owner_reference=''):
    development=plan.get('grant_kind')=='development_manual_v2'
    if (plan.get('model')!='gpt-6-luna' or plan.get('model_input_ceiling')!=1_050_000
            or type(plan.get('cap_microusd')) is not int or not 0<plan['cap_microusd']<=(2_000_000 if development else 1_000_000)
            or type(plan.get('max_provider_calls')) is not int or not 1<=plan['max_provider_calls']<=(60 if development else 30) or plan.get('max_images_per_call')!=(6 if development else 2)
            or plan.get('max_output_tokens')!=4096 or plan.get('max_resume_steps')!=0
            or plan.get('input_rate_microusd_per_million')!=600_000
            or plan.get('output_rate_microusd_per_million')!=2_000_000):
        raise AppError(422,'PILOT_PLAN_INVALID','Reviewed model/rates/limits must remain frozen.')
    if development and (plan.get('automated_cap_microusd')!=1_000_000
            or type(plan.get('max_automated_calls')) is not int or not 1<=plan['max_automated_calls']<=45
            or plan['max_provider_calls']-plan['max_automated_calls']<15
            or not isinstance(plan.get('expires_at_epoch'),(int,float))
            or not isinstance(plan.get('approved_documents'),dict) or not plan['approved_documents']):
        raise AppError(422,'PILOT_PLAN_INVALID','Development/manual grant requires frozen scope, expiry and manual reserve.')
    if approved and (not owner_reference or owner_reference!=plan.get('owner_approval_reference')
            or (not development and plan.get('gold_owner_confirmed') is not True) or plan.get('channel_owner_confirmed') is not True):
        raise AppError(403,'PILOT_OWNER_APPROVAL_REQUIRED','New owner cost, Channel and frozen-case approval required.')


class PilotBoundary:
    def __init__(self,plan,ledger,trace_path):
        validate_plan(plan)
        self.plan,self.ledger,self.trace_path=plan,ledger,Path(trace_path)

    def before_sdk(self,kwargs):
        """Runs immediately before SDK dispatch; reject legacy, unbudgeted, altered input."""
        p=self.plan
        if v2_usage_sink.get() is None:
            raise AppError(403,'PILOT_UNBUDGETED','Legacy/unbudgeted calls disabled in acceptance process.')
        from app.services.ai_chat_v2_tool_contract import tool_schemas
        supplied_tools=kwargs.get('tools')
        if supplied_tools and (supplied_tools!=tool_schemas() or kwargs.get('tool_choice')!='auto' or kwargs.get('parallel_tool_calls') is not False):
            raise AppError(422,'PILOT_REQUEST_INVALID','Only declared local function tools, no hosted paid tools.')
        if (kwargs.get('model')!=p['model'] or kwargs.get('service_tier')!='default'
                or kwargs.get('store') is not False or kwargs.get('stream') is not True
                or type(kwargs.get('max_output_tokens')) is not int
                or not 1<=kwargs['max_output_tokens']<=p['max_output_tokens']
                or kwargs.get('previous_response_id')):
            raise AppError(422,'PILOT_REQUEST_INVALID','Provider request differs from frozen pilot limits.')
        messages=kwargs.get('input')
        PilotLedger.input_upper_bound(messages)
        images=[]
        from PIL import Image
        allowed={v['sha256']:v['physical_page'] for v in p['images']}
        for message in messages:
            if not isinstance(message.get('content'),list):continue
            for part in message['content']:
                if part.get('type')!='input_image':continue
                try:
                    raw=base64.b64decode(part['image_url'].split(',',1)[1],validate=True)
                    with Image.open(io.BytesIO(raw)) as image:
                        if image.format!='JPEG' or image.width*image.height>4_000_000:raise ValueError()
                    digest=hashlib.sha256(raw).hexdigest()
                    if len(raw)>8*1024*1024 or digest not in allowed or part.get('detail')!='high':raise ValueError()
                except Exception:
                    raise AppError(422,'PILOT_SOURCE_INVALID','Only frozen original-page JPEG hashes may reach the model.') from None
                images.append({'physical_page':allowed[digest],'sha256':digest,'bytes':len(raw)})
        if len(images)>p['max_images_per_call']:
            raise AppError(422,'PILOT_IMAGE_CAP','Too many original image parts in one request.')
        with self.ledger.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            grant=db.execute('SELECT * FROM approvals WHERE id=?',(p['budget_approval_id'],)).fetchone()
            if (grant is None or grant['frozen'] or grant['expires']<=time.time() or grant['model']!=p['model']
                    or grant['channel']!=p['channel_id'] or grant['cap']>p['cap_microusd']
                    or grant['job_cap']>p['cap_microusd'] or grant['concurrency']!=1
                    or grant['input_rate']!=p['input_rate_microusd_per_million']
                    or grant['output_rate']!=p['output_rate_microusd_per_million']):
                raise AppError(403,'PILOT_BUDGET_INVALID','Fresh scoped immutable pilot grant required.')
            active=db.execute("SELECT id FROM reservations WHERE approval=? AND state='reserved'",(grant['id'],)).fetchall()
            if len(active)!=1:raise AppError(403,'PILOT_RESERVATION_REQUIRED','Exactly one funded dispatch required.')
            db.execute('CREATE TABLE IF NOT EXISTS pilot_dispatches (reservation TEXT PRIMARY KEY,approval TEXT NOT NULL,created REAL NOT NULL)')
            count=db.execute('SELECT COUNT(*) FROM pilot_dispatches WHERE approval=?',(grant['id'],)).fetchone()[0]
            if count>=p['max_provider_calls']:raise AppError(429,'PILOT_CALL_CAP','Pilot total call cap reached.')
            if db.execute('SELECT 1 FROM pilot_dispatches WHERE reservation=?',(active[0]['id'],)).fetchone():
                raise AppError(429,'PILOT_RETRY_DISABLED','No SDK retries or repeated dispatch for one reservation.')
            db.execute('INSERT INTO pilot_dispatches VALUES (?,?,?)',(active[0]['id'],grant['id'],time.time()))
            db.commit()
        record={'kind':'SDK_DISPATCH_PREPARED','model_configured':p['model'],'reservation_id':active[0]['id'],
            'ordinal':count+1,'input_mode':'image' if images else 'text','original_images':images,
            'max_output_tokens':kwargs['max_output_tokens'],'reserved_input_ceiling':PilotLedger.input_upper_bound(messages),
            'actual_model_received':'NOT_PROVEN_BY_THIS_PRE_DISPATCH_RECORD'}
        self.trace_path.parent.mkdir(parents=True,exist_ok=True)
        with self.trace_path.open('a',encoding='utf8') as file:file.write(json.dumps(record)+'\n')
        return record
