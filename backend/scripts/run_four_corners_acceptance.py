"""Capped acceptance runner. Default PREVIEW; never creates a monetary grant.

--main-app serves the normal app on port 8000 with the existing scoped grant.
Otherwise an isolated server is used. --execute requires owner approval,
an existing private ledger and frozen code; --resume-grant never resets usage.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC,datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',required=True)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--resume-grant',action='store_true')
    parser.add_argument('--main-app',action='store_true',help='Run the normal application on port 8000 with this existing scoped grant')
    parser.add_argument('--owner-approval-reference',default='')
    args=parser.parse_args()
    plan=json.loads(Path(args.plan).read_text(encoding='utf8'))
    development=plan.get('grant_kind')=='development_manual_v2'
    if args.resume_grant and not development:raise RuntimeError('Only development grants may resume without resetting their ledger')
    from app.services.ai_chat_v2_pilot_guard import validate_plan,PilotLedger,PilotBoundary
    validate_plan(plan,approved=args.execute,owner_reference=args.owner_approval_reference)
    if not args.execute:
        print(json.dumps({'mode':'PREVIEW_ONLY','paid_calls':0,'model':plan['model'],
            'channel_id':plan['channel_id'],'document_id':plan['document_id'],'cap_usd':plan['cap_microusd']/1e6,
            'max_calls':plan['max_provider_calls'],'case_ids':plan['case_ids'],
            'owner_approval':plan['owner_approval_reference'],'gold_owner_confirmed':plan['gold_owner_confirmed']}))
        return
    ledger_path=Path(os.environ.get('V2_PILOT_LEDGER','')).resolve()
    if not os.environ.get('V2_PILOT_LEDGER') or not ledger_path.is_file() or ROOT in ledger_path.parents:
        raise RuntimeError('Fresh approved existing ledger OUTSIDE repository required')
    private=Path(plan['private_output_path']).resolve()
    if ROOT==private or ROOT in private.parents or (private.exists() and any(private.iterdir()) and not args.resume_grant):
        raise RuntimeError('New empty private output directory outside repository required')
    for name,digest in plan['source_files'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest:raise RuntimeError('Frozen source changed')
    questions_path=Path(plan['questions_path'])
    if hashlib.sha256(questions_path.read_bytes()).hexdigest()!=plan['questions_sha256']:
        raise RuntimeError('Frozen questions changed')
    if hashlib.sha256(Path(plan['gold_path']).read_bytes()).hexdigest()!=plan['gold_sha256']:
        raise RuntimeError('Frozen independent candidate gold changed')
    questions=json.loads(questions_path.read_text(encoding='utf8'))
    # Gold is hash-checked ONLY; never loaded into model messages.
    if [q['id'] for q in questions]!=plan['case_ids']:raise RuntimeError('Case sequence changed')
    ledger=PilotLedger(ledger_path)
    with ledger.connection() as db:
        grant=db.execute('SELECT * FROM approvals WHERE id=?',(plan['budget_approval_id'],)).fetchone()
    if (grant is None or grant['cap']>plan['cap_microusd'] or grant['channel']!=plan['channel_id']
            or grant['model']!=plan['model'] or grant['expires']<=time.time() or grant['frozen']
            or grant['concurrency']!=1 or grant['input_rate']!=600_000 or grant['output_rate']!=2_000_000
            or (development and grant['expires']!=plan['expires_at_epoch'])
            or (ledger.snapshot(plan['budget_approval_id']) and not args.resume_grant)):
        raise RuntimeError('Fresh immutable scoped ledger approval required; old grants not accepted')
    os.environ.update(AI_CHAT_BILLING_MODE='capped',AI_CHAT_V2_LIVE_APPROVED='true',AI_CHAT_V2_VISION_READY='true',AI_CHAT_V2_ORIGINAL_FIRST='true',
        AI_CHAT_V2_BUDGET_APPROVAL_ID=plan['budget_approval_id'],AI_CHAT_V2_BUDGET_LEDGER=str(ledger_path),
        AI_CHAT_V2_MAX_IMAGES=str(plan['max_images_per_call']))
    from app.core.config import get_settings
    get_settings.cache_clear();settings=get_settings()
    from sqlalchemy.engine import make_url
    if (settings.ai_provider!='openai' or settings.openai_model!=plan['model']
            or settings.app_env not in {'development','test'}
            or make_url(str(settings.database_url)).host not in {'localhost','127.0.0.1'}):
        raise RuntimeError('Unchanged model/provider and approved local DB required')
    private.mkdir(parents=True,exist_ok=True)
    (private/f'startup-plan-{time.time_ns()}.json').write_text(json.dumps(plan,indent=2),encoding='utf8')
    boundary=PilotBoundary(plan,ledger,private/'provider-boundary.jsonl')
    def mode():
        path=private/'operator-mode.json'
        return json.loads(path.read_text()) if path.exists() else {'paused':False,'manual':False}
    def set_mode(**values):
        state=mode();state.update(values)
        (private/'operator-mode.json').write_text(json.dumps(state),encoding='utf8')
    if development:
        def spending_ceiling(self):
            with self.connection() as conn:
                calls=conn.execute('SELECT COUNT(*) FROM pilot_dispatches WHERE approval=?',(plan['budget_approval_id'],)).fetchone()[0] if conn.execute("SELECT 1 FROM sqlite_master WHERE name='pilot_dispatches'").fetchone() else 0
            if calls>=plan['max_provider_calls']:
                raise AppError(429,'PILOT_CALL_CAP','Total call cap reached before reservation.')
            if mode()['manual']:return plan['cap_microusd']
            if calls>=plan['max_automated_calls']:
                raise AppError(429,'PILOT_MANUAL_RESERVE','Automated call allowance reached before reservation; manual capacity preserved.')
            return plan['automated_cap_microusd']
        PilotLedger.spending_cap=property(spending_ceiling)
    from app.services import ai_chat_v2_budget,openai_provider
    ai_chat_v2_budget.BudgetLedger=PilotLedger
    original_client=openai_provider.get_client
    class Responses:
        def __init__(self,inner):self.inner=inner
        async def create(self,**kwargs):
            if mode()['paused']:
                raise RuntimeError('Pilot paused; no paid dispatch allowed')
            if development and not mode()['manual']:
                with ledger.connection() as db:
                    spent=db.execute('SELECT COALESCE(SUM(COALESCE(actual,amount)),0) FROM reservations WHERE approval=?',(plan['budget_approval_id'],)).fetchone()[0]
                    calls=db.execute('SELECT COUNT(*) FROM pilot_dispatches WHERE approval=?',(plan['budget_approval_id'],)).fetchone()[0] if db.execute("SELECT 1 FROM sqlite_master WHERE name='pilot_dispatches'").fetchone() else 0
                if spent>plan['automated_cap_microusd'] or calls>=plan['max_automated_calls']:
                    raise AppError(429,'PILOT_MANUAL_RESERVE','Automated allowance reached; manual capacity preserved.')
            record=boundary.before_sdk(kwargs)
            stream=await self.inner.create(**kwargs)
            response=getattr(stream,'response',None)
            if response is not None:
                receipt={**record,'kind':'PROVIDER_HTTP_STREAM_OPENED','http_status':response.status_code,
                    'provider_request_id':response.headers.get('x-request-id')}
                with (private/'provider-boundary.jsonl').open('a',encoding='utf8') as f:f.write(json.dumps(receipt)+'\n')
            class RecordedStream:
                @property
                def response(self):return response
                async def __aenter__(self):await stream.__aenter__();return self
                async def __aexit__(self,*a):return await stream.__aexit__(*a)
                async def __aiter__(self):
                    async for event in stream:
                        if event.type in {'response.completed','response.incomplete','response.failed'}:
                            receipt={'reservation_id':record['reservation_id'],'event':event.type,
                                'provider_request_id':response.headers.get('x-request-id') if response is not None else None,
                                'response':event.response.model_dump(mode='json')}
                            with (private/'provider-responses.jsonl').open('a',encoding='utf8') as f:
                                f.write(json.dumps(receipt,ensure_ascii=False)+'\n')
                        yield event
            return RecordedStream()
    from types import SimpleNamespace
    def client(s,key):return SimpleNamespace(responses=Responses(original_client(s,key).responses))
    openai_provider.get_client=client
    # No sync OCR/other paid route is available in this process.
    import openai
    def no_sync(*a,**k):raise RuntimeError('Paid OCR disabled in acceptance process')
    openai.OpenAI=no_sync
    from uuid import UUID
    from sqlalchemy import select
    from app.models.learning_document import LearningDocument
    from app.services.ai_conversation_service import AIConversationService
    from app.services.ai_chat_v2_tools import DocumentTools
    from app.core.exceptions import AppError
    original_ask=AIConversationService.ask
    async def ask(service,principal,channel,conversation,question,request_id,**kwargs):
        if str(channel)!=plan['channel_id'] or (not args.main_app and str(conversation)!=plan['conversation_id']) or (kwargs.get('resume_v2') and not development):
            raise AppError(403,'PILOT_SCOPE','Channel này chưa được cấp ngân sách GPT. Không gọi model hoặc chuyển sang AI khác.')
        state=mode()
        if state['paused']:raise AppError(429,'PILOT_PAUSED','Pilot paused; results retained. No API call.')
        case=next((q for q in questions if q['question']==question),None)
        if state['manual']:case={'id':'MANUAL-'+str(request_id)}
        elif development:case={'id':'DEV-'+str(request_id)}
        if case is None:raise AppError(422,'PILOT_QUESTION','Initial phase accepts only frozen questions; manual phase uses same remaining cap.')
        async with service.transaction(principal):
            await service.owned(principal,channel,conversation)
            doc=await service.db.get(LearningDocument,UUID(plan['document_id']),populate_existing=True)
            if (doc is None or str(doc.uploader_id)!=str(principal.user.id) or str(doc.channel_id)!=str(channel)
                    or doc.deleted_at is not None or doc.checksum!=plan['source_sha256']):
                raise AppError(403,'PILOT_PRINCIPAL','Authorized owner/source revision required.')
        with ledger.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS pilot_cases (case_id TEXT PRIMARY KEY,request_id TEXT NOT NULL)')
            prior=db.execute('SELECT request_id FROM pilot_cases WHERE case_id=?',(case['id'],)).fetchone()
            count=db.execute('SELECT COUNT(*) FROM pilot_cases').fetchone()[0]
            if prior is not None and prior['request_id']!=str(request_id):
                raise AppError(409,'PILOT_CASE_RETRY','No new paid retry for a completed/attempted case.')
            if prior is None:
                if not development and not state['manual'] and (count>=len(questions) or questions[count]['id']!=case['id']):
                    raise AppError(409,'PILOT_CASE_ORDER','Use frozen order, including T5 immediately after T3.')
                db.execute('INSERT INTO pilot_cases VALUES (?,?)',(case['id'],str(request_id)))
            db.commit()
        started=time.monotonic()
        from app.services.ai_observability import trace
        metadata={};trace_token=trace.set(metadata)
        try:
            result=await original_ask(service,principal,channel,conversation,question,request_id,**kwargs)
        except BaseException as error:
            set_mode(paused=True)
            (private/(case['id']+'-error.json')).write_text(json.dumps({'case_id':case['id'],
                'error_code':getattr(error,'code',type(error).__name__),'latency_seconds':time.monotonic()-started,
                'ledger':ledger.snapshot(plan['budget_approval_id']),'trace':metadata},default=str),encoding='utf8')
            raise
        finally:trace.reset(trace_token)
        transcript={'case_id':case['id'],'question':question,'response':result,
            'latency_seconds':time.monotonic()-started,'ledger':ledger.snapshot(plan['budget_approval_id']),
            'trace':metadata,
            'charged_usd':'UNVERIFIED; ledger contains reserved/settled upper-bound estimates'}
        target=private/(case['id']+(f'-{time.time_ns()}' if development else '')+'-response.json')
        if not target.exists():target.write_text(json.dumps(transcript,default=str,ensure_ascii=False),encoding='utf8')
        if any(r['state'] in {'unknown','reserved'} for r in ledger.snapshot(plan['budget_approval_id'])):
            set_mode(paused=True)
            raise AppError(429,'PILOT_UNKNOWN_USAGE','Stop pilot; operator receipt reconciliation required.')
        if not development and case['id']==questions[-1]['id']:set_mode(paused=True)
        return result
    AIConversationService.ask=ask
    original_tool=DocumentTools.call
    async def tool(self,name,args):
        values=dict(args) if isinstance(args,dict) else args
        if name=='begin_whole_document':raise AppError(403,'PILOT_NO_WHOLE_BOOK','Whole-book reading is outside approval.')
        if development:
            if isinstance(values,dict):
                ids=([values['document_id']] if values.get('document_id') else [])+values.get('document_ids',[])
                if any(identity not in plan['approved_documents'] for identity in ids):
                    raise AppError(403,'PILOT_DOCUMENT_SCOPE','Source outside the frozen authorized Channel corpus.')
            result=await original_tool(self,name,values)
            if name=='list_available_documents':result['documents']=[d for d in result['documents'] if d['document_id'] in plan['approved_documents']]
            for passage in result.get('passages',[]):
                if passage.get('authority')=='original' and passage.get('representation',{}).get('kind')=='image':
                    if plan['approved_documents'].get(passage['document_id'])!=passage['revision']:
                        raise AppError(409,'PILOT_SOURCE_CHANGED','Source revision differs from authorized corpus.')
                    image={'sha256':passage['representation_sha256'],'physical_page':passage['unit'],'document_id':passage['document_id']}
                    if image not in boundary.plan['images']:boundary.plan['images'].append(image)
            return result
        if isinstance(values,dict):
            if values.get('document_id') not in {None,plan['document_id']} or any(i!=plan['document_id'] for i in values.get('document_ids',[])):
                raise AppError(403,'PILOT_DOCUMENT_SCOPE','Only approved original document allowed.')
            if name in {'search_documents','read_document_segments'}:values['document_id']=plan['document_id']
            approved={v['physical_page'] for v in plan['images']}
            if any(i not in approved for i in values.get('units',[])+values.get('pages',[])) or ('page' in values and values['page'] not in approved):
                raise AppError(403,'PILOT_PAGE_SCOPE','Only frozen physical pages approved.')
        result=await original_tool(self,name,values)
        if name=='list_available_documents':result['documents']=[d for d in result['documents'] if d['document_id']==plan['document_id']]
        return result
    DocumentTools.call=tool
    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from app.api.v1.router import router
    from app.core.exception_handlers import app_error_handler
    from app.db.session import engine
    @asynccontextmanager
    async def lifespan(app):
        # No ingestion, scheduled, background or legacy model worker starts.
        try:yield
        finally:
            os.environ.update(AI_CHAT_V2_LIVE_APPROVED='false',AI_CHAT_V2_VISION_READY='false')
            get_settings.cache_clear();await openai_provider.close_clients();await engine.dispose()
    if args.main_app:
        # Keep the normal routes, auth and workers. Paid OCR remains disabled.
        os.environ['DOCUMENT_VISION_ENABLED']='false'
        get_settings.cache_clear()
        from app.main import app
    else:
        app=FastAPI(title='Scoped owner-approved V2 acceptance',lifespan=lifespan)
    app.add_exception_handler(AppError,app_error_handler)
    app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:3032','http://localhost:3000','http://127.0.0.1:3000'],allow_credentials=True,
        allow_methods=['GET','POST','PATCH','PUT','DELETE'],allow_headers=['Authorization','Content-Type'])
    @app.middleware('http')
    async def writes(request,call_next):
        from fastapi.responses import JSONResponse
        path=request.url.path
        if args.main_app:
            # Ordinary application operations retain their normal RBAC. The ask
            # guard above limits all paid chat to the immutable grant scope.
            return await call_next(request)
        allowed_prefix=f"/api/v1/channels/{plan['channel_id']}/ai/conversations/{plan['conversation_id']}"
        if request.method not in {'GET','OPTIONS'} and not (path in {'/api/v1/auth/login','/api/v1/auth/refresh',f"/api/v1/channels/{plan['channel_id']}/ai/pilot/manual",f"/api/v1/channels/{plan['channel_id']}/ai/pilot/pause"} or
                (path.startswith(allowed_prefix) and (path.endswith('/questions') or path.endswith('/cancel') or (development and path.endswith('/resume'))))):
            return JSONResponse({'error':{'code':'PILOT_WRITE_SCOPE','message':'Outside frozen pilot scope','fields':None}},status_code=403)
        return await call_next(request)
    from app.api.dependencies import CurrentPrincipal,DbSession
    async def scoped(principal,db,channel):
        if str(channel)!=plan['channel_id']:raise AppError(404,'PILOT_CHANNEL','No pilot on this Channel.')
        service=AIConversationService(db)
        async with service.transaction(principal):await service.owned(principal,UUID(channel),UUID(plan['conversation_id']))
    @app.get('/api/v1/channels/{channel}/ai/pilot')
    async def status(channel:str,p:CurrentPrincipal,db:DbSession):
        await scoped(p,db,channel)
        rows=ledger.snapshot(plan['budget_approval_id'])
        spent=sum(r['actual'] if r['actual'] is not None else r['amount'] for r in rows)
        with ledger.connection() as conn:
            grant=conn.execute('SELECT * FROM approvals WHERE id=?',(plan['budget_approval_id'],)).fetchone()
            calls=conn.execute('SELECT COUNT(*) FROM pilot_dispatches WHERE approval=?',(grant['id'],)).fetchone()[0] if rows else 0
        return {'data':{'engine':'AI Chat V2 Original-First','model':plan['model'],'channel_id':channel,
            'conversation_id':plan['conversation_id'],'cap_usd':plan['cap_microusd']/1e6,
            'remaining_usd':max(0,plan['cap_microusd']-spent)/1e6,'calls':calls,'max_calls':plan['max_provider_calls'],
            'expires_at':datetime.fromtimestamp(grant['expires'],UTC).isoformat(),
            'source_scope':'Authorized documents in this Channel; bounded original reads' if development else 'Frozen original pages',
            'blocked':bool(grant['frozen'] or grant['expires']<=time.time() or spent>=grant['cap'] or calls>=plan['max_provider_calls'] or any(r['state'] in {'reserved','unknown'} for r in rows)),
            **mode()}}
    @app.post('/api/v1/channels/{channel}/ai/pilot/manual')
    async def manual(channel:str,p:CurrentPrincipal,db:DbSession):
        await scoped(p,db,channel)
        if plan.get('allow_manual_chat',True) is not True:
            raise AppError(403,'PILOT_CANARY_ONLY','This approval permits one frozen canary only, no manual expansion.')
        current=(await status(channel,p,db))['data']
        if current['blocked']:raise AppError(429,'PILOT_BUDGET_CAP','No funded capacity; new approval required.')
        set_mode(paused=False,manual=True)
        return await status(channel,p,db)
    @app.post('/api/v1/channels/{channel}/ai/pilot/pause')
    async def pause(channel:str,p:CurrentPrincipal,db:DbSession):
        await scoped(p,db,channel);set_mode(paused=True)
        return await status(channel,p,db)
    if not args.main_app:app.include_router(router)
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8000 if args.main_app else 8032,access_log=False)


if __name__=='__main__':
    try:main()
    except Exception as error:
        # Never print SDK/credential/data-bearing exceptions.
        print('Pilot stopped before further dispatch: '+getattr(error,'code',type(error).__name__),file=sys.stderr)
        sys.exit(2)
