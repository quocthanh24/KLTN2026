"""Atomic single-host monetary ledger. Unknown calls keep their reservation.

Rates are operator-approved upper bounds, in micro-USD per million tokens.
The ledger never stores prompts, evidence, images, credentials or user answers.
"""
import sqlite3
import json
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
from app.core.exceptions import AppError
from app.services.ai_observability import v2_usage_sink


class BudgetLedger:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY, channel TEXT NOT NULL, model TEXT NOT NULL,
                    cap INTEGER NOT NULL, job_cap INTEGER NOT NULL, concurrency INTEGER NOT NULL,
                    expires REAL NOT NULL, input_rate INTEGER NOT NULL, output_rate INTEGER NOT NULL,
                    frozen INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS reservations (
                    id TEXT PRIMARY KEY, approval TEXT NOT NULL, job TEXT NOT NULL,
                    amount INTEGER NOT NULL, actual INTEGER, state TEXT NOT NULL,
                    created REAL NOT NULL, request_id TEXT, response_id TEXT,
                    input_tokens INTEGER, output_tokens INTEGER,
                    FOREIGN KEY(approval) REFERENCES approvals(id));
                CREATE INDEX IF NOT EXISTS ix_v2_reservation_approval ON reservations(approval);
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
        finally:
            db.close()

    def approve(self, identity, channel, model, cap, job_cap, concurrency, expires, input_rate, output_rate):
        """Operator CLI only; HTTP never provisions an approval. IDs are immutable."""
        if (any(type(v) is not int or v <= 0 for v in (cap,job_cap,concurrency,input_rate,output_rate))
                or job_cap > cap or expires <= time.time() or not model or not channel
                or not isinstance(identity,str) or not identity.strip() or len(identity)>120):
            raise ValueError('Invalid approval limits')
        with self.connection() as db:
            db.execute('INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,0)',
                (identity,str(channel),model,cap,job_cap,concurrency,expires,input_rate,output_rate))

    @staticmethod
    def input_upper_bound(messages):
        """UTF-8 byte text upper bound plus framing; only bounded inline images.

        An image is reserved as 4M tokens conservatively (tool limits to 4MP).
        Unsupported file/URL inputs fail closed, not silently priced as text.
        """
        size = 0
        for message in messages:
            if message.get('type') in {'function_call','reasoning','message'}:
                # Replayed Responses output includes arguments/reasoning/output
                # text outside message.content. Reserve all serialized bytes.
                size += len(json.dumps(message,ensure_ascii=False).encode('utf8'))
                continue
            if message.get('type')=='function_call_output':
                output=message.get('output')
                if not isinstance(output,str):
                    raise AppError(422,'AI_V2_BUDGET_INPUT','Unsupported tool output type.')
                size += len(json.dumps(message,ensure_ascii=False).encode('utf8'))
                continue
            content = message.get('content','')
            if isinstance(content,str):
                size += len(content.encode('utf8'))
            elif isinstance(content,list):
                for part in content:
                    if part.get('type') == 'input_text':
                        size += len(part.get('text','').encode('utf8'))
                    elif part.get('type') == 'input_image' and part.get('image_url','').startswith('data:image/jpeg;base64,'):
                        size += 4_000_000
                    else:
                        raise AppError(422,'AI_V2_BUDGET_INPUT','Unsupported budget input type.')
            else:
                raise AppError(422,'AI_V2_BUDGET_INPUT','Unsupported message content.')
        return size+2048+len(messages)*256

    def reserve(self, approval, channel, model, job, input_bound, output_bound):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM approvals WHERE id=?',(approval,)).fetchone()
            if (row is None or row['frozen'] or row['expires'] <= time.time()
                    or row['channel'] != str(channel) or row['model'] != model):
                raise AppError(503,'AI_V2_BUDGET_APPROVAL','V2 has no valid scoped budget approval.')
            if any(type(v) is not int or v < 0 for v in (input_bound,output_bound)):
                raise ValueError('Invalid token bounds')
            amount = (input_bound*row['input_rate']+output_bound*row['output_rate']+999999)//1_000_000
            spent,active = db.execute('SELECT COALESCE(SUM(COALESCE(actual,amount)),0), '
                "SUM(CASE WHEN state IN ('reserved','unknown') THEN 1 ELSE 0 END) FROM reservations WHERE approval=?",(approval,)).fetchone()
            job_spent = db.execute('SELECT COALESCE(SUM(COALESCE(actual,amount)),0) FROM reservations '
                'WHERE approval=? AND job=?',(approval,str(job))).fetchone()[0]
            spending_cap=min(row['cap'],getattr(self,'spending_cap',row['cap']))
            if spent+amount > spending_cap or job_spent+amount > row['job_cap'] or (active or 0) >= row['concurrency']:
                raise AppError(429,'AI_V2_BUDGET_CAP','V2 budget or concurrency limit reached; progress is retained.')
            identity = str(uuid4())
            db.execute('INSERT INTO reservations(id,approval,job,amount,state,created) VALUES (?,?,?,?,?,?)',
                (identity,approval,str(job),amount,'reserved',time.time()))
            db.commit()
            return identity

    def settle(self, identity, usage=None):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT r.*,a.model,a.input_rate,a.output_rate FROM reservations r '
                'JOIN approvals a ON a.id=r.approval WHERE r.id=?',(identity,)).fetchone()
            if row is None: raise ValueError('Unknown reservation')
            if row['state'] != 'reserved': return
            valid = (isinstance(usage,dict) and usage.get('model') == row['model'] and
                all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('input_tokens','output_tokens')))
            if not valid:
                db.execute("UPDATE reservations SET state='unknown' WHERE id=?",(identity,))
                if isinstance(usage,dict) and usage.get('model') != row['model']:
                    db.execute('UPDATE approvals SET frozen=1 WHERE id=?',(row['approval'],))
            else:
                cost = (usage['input_tokens']*row['input_rate']+usage['output_tokens']*row['output_rate']+999999)//1_000_000
                db.execute("UPDATE reservations SET actual=?,state='settled',request_id=?,response_id=?,"
                    'input_tokens=?,output_tokens=? WHERE id=?',(cost,usage.get('request_id'),usage.get('response_id'),
                    usage['input_tokens'],usage['output_tokens'],identity))
                if cost > row['amount']:
                    db.execute('UPDATE approvals SET frozen=1 WHERE id=?',(row['approval'],))
            db.commit()

    def recover_unknown(self, older_than_seconds=600):
        """Orphan cleanup preserves money; never assumes cancellation was free."""
        with self.connection() as db:
            db.execute("UPDATE reservations SET state='unknown' WHERE state='reserved' AND created<=?",
                (time.time()-older_than_seconds,))

    def reconcile_unknown(self, identity, usage, receipt_reference):
        """Operator-only verified terminal receipt. Never called by HTTP/retry/TTL.

        No terminal receipt means no release. Settlement cannot be rewritten to
        replenish a completed approval. Request/response IDs are audit linkage.
        """
        if not isinstance(receipt_reference,str) or not receipt_reference.strip() or not isinstance(usage,dict):
            raise ValueError('A verified terminal receipt reference is required')
        if not usage.get('response_id') or not usage.get('request_id'):
            raise ValueError('Upstream terminal request and response IDs are required')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT r.*,a.model,a.input_rate,a.output_rate FROM reservations r '
                'JOIN approvals a ON a.id=r.approval WHERE r.id=?',(identity,)).fetchone()
            if row is None or row['state']!='unknown':raise ValueError('Only unknown reservations may be reconciled')
            if usage.get('model')!=row['model'] or any(type(usage.get(k)) is not int or usage[k]<0 for k in ('input_tokens','output_tokens')):
                raise ValueError('Receipt model/usage does not match approval')
            cost=(usage['input_tokens']*row['input_rate']+usage['output_tokens']*row['output_rate']+999999)//1_000_000
            db.execute("UPDATE reservations SET state='settled',actual=?,request_id=?,response_id=?,input_tokens=?,output_tokens=? WHERE id=?",
                (cost,usage['request_id'],usage['response_id'],usage['input_tokens'],usage['output_tokens'],identity))
            db.execute('CREATE TABLE IF NOT EXISTS reconciliations(reservation TEXT PRIMARY KEY, receipt_reference TEXT NOT NULL, reconciled REAL NOT NULL)')
            db.execute('INSERT INTO reconciliations VALUES (?,?,?)',(identity,receipt_reference[:200],time.time()))
            if cost>row['amount']:db.execute('UPDATE approvals SET frozen=1 WHERE id=?',(row['approval'],))
            db.commit()

    def snapshot(self, approval):
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT id,job,amount,actual,state,request_id,response_id,input_tokens,output_tokens '
                'FROM reservations WHERE approval=? ORDER BY created,id',(approval,))]


class BudgetedProvider:
    def __init__(self, upstream, settings, channel, job):
        self.upstream,self.settings,self.channel,self.job = upstream,settings,channel,job
        self.ledger = (BudgetLedger(settings.ai_chat_v2_budget_ledger)
            if getattr(settings,'ai_chat_billing_mode','capped') == 'capped' else None)

    async def __call__(self, messages):
        settings = self.settings
        if getattr(settings,'ai_chat_billing_mode','capped') == 'metered':
            if settings.ai_provider != 'openai':
                raise AppError(503,'AI_PROVIDER_CONFIGURATION','AI Chat yêu cầu OpenAI.')
            # Preserve input safety and usage collection without creating or
            # extending a historical acceptance grant. Per-turn limits remain
            # enforced by ai_provider and the V2 job/orchestration boundaries.
            BudgetLedger.input_upper_bound(messages)
            usage=[]
            token=v2_usage_sink.set(usage)
            try:
                return await self.upstream(messages)
            finally:
                v2_usage_sink.reset(token)
                from app.services.ai_observability import trace
                current=trace.get()
                if current is not None:
                    current.setdefault('v2_billing',[]).append({'mode':'metered','usage':usage,
                        'usage_confirmed':len(usage)==1})
        if not settings.ai_chat_v2_live_approved or settings.ai_provider != 'openai':
            raise AppError(503,'AI_V2_LIVE_BLOCKED','V2 live model calls are not approved.')
        if settings.ai_chat_v2_budget_topology != 'single_host':
            raise AppError(503,'AI_V2_BUDGET_TOPOLOGY','A shared multi-host budget backend is required.')
        from app.services.ai_observability import output_token_limit
        from app.services.ai_observability import v2_tool_schemas,output_format
        schemas=v2_tool_schemas.get()
        budget_messages=messages+([{'role':'system','content':json.dumps(schemas)}] if schemas else [])
        if output_format.get():budget_messages=budget_messages+[{'role':'system','content':json.dumps(output_format.get())}]
        reservation = self.ledger.reserve(settings.ai_chat_v2_budget_approval_id,self.channel,
            settings.openai_model,self.job,self.ledger.input_upper_bound(budget_messages),min(4096,max(512,output_token_limit.get())))
        usage = []
        token = v2_usage_sink.set(usage)
        try:
            return await self.upstream(messages)
        finally:
            # Timeout, cancellation, 429 and malformed response retain unknown cost.
            v2_usage_sink.reset(token)
            self.ledger.settle(reservation,usage[-1] if len(usage)==1 else None)
            from app.services.ai_observability import trace
            current=trace.get()
            if current is not None:
                record=next(r for r in self.ledger.snapshot(settings.ai_chat_v2_budget_approval_id) if r['id']==reservation)
                current.setdefault('v2_budget',[]).append({'reservation_id':reservation,'job_id':str(self.job),
                    'reserved_microusd':record['amount'],'settled_upper_bound_microusd':record['actual'],
                    'state':record['state'],'request_id':record['request_id'],'response_id':record['response_id']})
