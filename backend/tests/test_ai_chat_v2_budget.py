"""Real temporary SQLite, parallel threads and fake upstream faults; no network."""
import asyncio
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from app.core.exceptions import AppError
from app.services.ai_chat_v2_budget import BudgetLedger,BudgetedProvider
from app.services.ai_observability import v2_usage_sink


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.ledger=BudgetLedger(Path(self.temp.name)/'budget.sqlite3')
        self.ledger.approve('test','channel','configured-model',100,60,1,time.time()+600,1_000_000,1_000_000)

    def tearDown(self): self.temp.cleanup()

    def reserve(self,job='job',tokens=30):
        return self.ledger.reserve('test','channel','configured-model',job,tokens,0)

    def test_atomic_concurrent_reservations(self):
        def reserve(_):
            try: return self.reserve()
            except AppError: return None
        with ThreadPoolExecutor(max_workers=8) as pool: identities=list(pool.map(reserve,range(16)))
        self.assertEqual(sum(i is not None for i in identities),1)
        self.assertEqual(len(self.ledger.snapshot('test')),1)

    def test_settlement_rounding_ids_and_idempotency(self):
        identity=self.reserve()
        self.ledger.settle(identity,{'model':'configured-model','input_tokens':3,'output_tokens':2,
            'request_id':'req-test','response_id':'resp-test'})
        self.ledger.settle(identity)
        row=self.ledger.snapshot('test')[0]
        self.assertEqual((row['actual'],row['state'],row['request_id'],row['response_id']),(5,'settled','req-test','resp-test'))

    def test_unknown_recovery_never_releases_money_or_concurrency(self):
        identity=self.reserve()
        self.ledger.recover_unknown(0)
        row=self.ledger.snapshot('test')[0]
        self.assertEqual((row['amount'],row['actual'],row['state']),(30,None,'unknown'))
        with self.assertRaises(AppError):self.reserve('other-job')

    def test_job_and_global_cap(self):
        identity=self.reserve(tokens=50)
        self.ledger.settle(identity,{'model':'configured-model','input_tokens':50,'output_tokens':0})
        with self.assertRaises(AppError):self.reserve(tokens=11)
        identity=self.reserve('other-job',40)
        self.ledger.settle(identity,{'model':'configured-model','input_tokens':40,'output_tokens':0})
        with self.assertRaises(AppError):self.reserve('third-job',11)

    def test_no_approval_reuse_wrong_channel_model_or_expired(self):
        for approval,channel,model in [('absent','channel','configured-model'),('test','other','configured-model'),('test','channel','other')]:
            with self.assertRaises(AppError):self.ledger.reserve(approval,channel,model,'job',1,1)
        with self.ledger.connection() as db:db.execute('UPDATE approvals SET expires=0')
        with self.assertRaises(AppError):self.reserve()

    def test_operator_id_cannot_reset_cap(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.approve('test','channel','configured-model',100,60,1,time.time()+600,1,1)

    def test_unknown_requires_verified_terminal_receipt_for_operator_reconciliation(self):
        identity=self.reserve();self.ledger.settle(identity)
        with self.assertRaises(ValueError):self.ledger.reconcile_unknown(identity,{},'')
        receipt={'model':'configured-model','input_tokens':7,'output_tokens':1,'request_id':'req-confirmed','response_id':'resp-confirmed'}
        self.ledger.reconcile_unknown(identity,receipt,'owner-verified-terminal-receipt')
        self.assertEqual(self.ledger.snapshot('test')[0]['actual'],8)
        with self.assertRaises(ValueError):self.ledger.reconcile_unknown(identity,receipt,'again')
        self.assertIsNotNone(self.reserve('other-job'))

    def test_overrun_freezes_approval(self):
        identity=self.reserve(tokens=1)
        self.ledger.settle(identity,{'model':'configured-model','input_tokens':2,'output_tokens':0})
        with self.assertRaises(AppError):self.reserve()

    def test_upper_bound_text_image_and_reject_remote_file(self):
        text=BudgetLedger.input_upper_bound([{'role':'user','content':'中文'}])
        image=BudgetLedger.input_upper_bound([{'role':'user','content':[{'type':'input_image','image_url':'data:image/jpeg;base64,ABC'}]}])
        self.assertGreaterEqual(text,len('中文'.encode('utf8')))
        self.assertGreaterEqual(image,4_000_000)
        with self.assertRaises(AppError):BudgetLedger.input_upper_bound([{'content':[{'type':'input_file','file_url':'https://private'}]}])


class ProviderBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_metered_chat_needs_no_grant_and_keeps_usage_without_ledger(self):
        settings=SimpleNamespace(ai_chat_billing_mode='metered',ai_provider='openai',ai_chat_v2_live_approved=False)
        called=[]
        async def upstream(messages):
            called.append(messages)
            v2_usage_sink.get().append({'model':'gpt-6-luna','input_tokens':10,'output_tokens':2})
            return 'Answer'
        from app.services.ai_observability import trace
        recorded={};token=trace.set(recorded)
        try:
            provider=BudgetedProvider(upstream,settings,'any-authorized-channel','job')
            self.assertIsNone(provider.ledger)
            self.assertEqual(await provider([{'role':'user','content':'Question'}]),'Answer')
            self.assertEqual(len(called),1)
            self.assertEqual(recorded['v2_billing'][0]['mode'],'metered')
            self.assertTrue(recorded['v2_billing'][0]['usage_confirmed'])
        finally:trace.reset(token)

    async def test_metered_failure_propagates_once_without_retry_or_fallback(self):
        settings=SimpleNamespace(ai_chat_billing_mode='metered',ai_provider='openai')
        from unittest.mock import AsyncMock
        upstream=AsyncMock(side_effect=AppError(503,'AI_PROVIDER_ERROR','Failure'))
        with self.assertRaises(AppError) as error:
            await BudgetedProvider(upstream,settings,'channel','job')([])
        self.assertEqual(error.exception.code,'AI_PROVIDER_ERROR')
        upstream.assert_awaited_once()

    async def test_timeout_cancellation_rate_limit_and_missing_usage_reserve_unknown(self):
        for fault in [TimeoutError(),asyncio.CancelledError(),AppError(429,'TEST_RATE','rate'),None]:
            with self.subTest(fault=type(fault).__name__),tempfile.TemporaryDirectory() as folder:
                settings=SimpleNamespace(ai_chat_v2_live_approved=True,ai_provider='openai',openai_model='unchanged',
                    ai_chat_v2_budget_ledger=str(Path(folder)/'ledger.db'),ai_chat_v2_budget_approval_id='test',ai_chat_v2_budget_topology='single_host')
                ledger=BudgetLedger(settings.ai_chat_v2_budget_ledger)
                ledger.approve('test','channel','unchanged',100000,100000,1,time.time()+600,1000,1000)
                async def upstream(messages):
                    self.assertEqual(len(ledger.snapshot('test')),1) # reservation BEFORE dispatch
                    if fault is not None:raise fault
                    return {'answer':'fake'}
                provider=BudgetedProvider(upstream,settings,'channel','job')
                try:await provider([{'role':'user','content':'test'}])
                except BaseException:pass
                self.assertEqual(ledger.snapshot('test')[0]['state'],'unknown')

    async def test_success_usage_settles_and_zero_cap_stops_before_upstream(self):
        with tempfile.TemporaryDirectory() as folder:
            settings=SimpleNamespace(ai_chat_v2_live_approved=True,ai_provider='openai',openai_model='unchanged',
                ai_chat_v2_budget_ledger=str(Path(folder)/'ledger.db'),ai_chat_v2_budget_approval_id='test',ai_chat_v2_budget_topology='single_host')
            ledger=BudgetLedger(settings.ai_chat_v2_budget_ledger)
            called=[]
            async def upstream(messages):
                called.append(True)
                v2_usage_sink.get().append({'model':'unchanged','input_tokens':1,'output_tokens':1,'response_id':'fake'})
                return {}
            provider=BudgetedProvider(upstream,settings,'channel','job')
            with self.assertRaises(AppError):await provider([])
            self.assertFalse(called)
            ledger.approve('test','channel','unchanged',100000,100000,1,time.time()+600,1000,1000)
            await provider([])
            self.assertEqual(ledger.snapshot('test')[0]['state'],'settled')
