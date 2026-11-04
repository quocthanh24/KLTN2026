"""Read-only original->real SDK serialization proof using MockTransport, NOT GPT.

Uses the real stored original, normal session/Channel/conversation authorization,
existing V2 orchestration and native SDK. No paid endpoint is contacted. No raw
original, question, gold, fake answer or credential is written into public output.
"""
import asyncio
import base64
import copy
import hashlib
import json
import sys
import tempfile
from datetime import UTC,datetime
from pathlib import Path
from uuid import UUID
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))


async def run(plan_path):
    import httpx2
    import openai
    from pydantic import SecretStr
    from sqlalchemy import select
    from app.core.config import get_settings
    from app.db.session import session_factory,engine
    from app.models.learning_document import LearningDocument
    from app.models.auth_session import AuthSession
    from app.services.auth_service import AuthService
    from app.services.ai_conversation_service import AIConversationService
    from app.services.ai_chat_v2_tools import DocumentTools
    from app.services.ai_chat_v2 import orchestrate,REVIEW,model_call
    from app.services.ai_chat_v2_budget import BudgetedProvider
    from app.services.ai_chat_v2_pilot_guard import PilotLedger,PilotBoundary
    from app.services import openai_provider
    plan=json.loads(Path(plan_path).read_text(encoding='utf8'))
    questions=json.loads(Path(plan['questions_path']).read_text(encoding='utf8'))
    question=next(q['question'] for q in questions if q['id']=='T3')
    target=Path(plan_path).parent
    observed=[]
    factory=openai.AsyncOpenAI
    try:
        async with session_factory() as db:
            async with db.begin():
                doc=await db.get(LearningDocument,UUID(plan['document_id']))
                if doc is None or doc.checksum!=plan['source_sha256']:raise RuntimeError('Original changed')
                sessions=await db.scalar(select(AuthSession).where(AuthSession.user_id==doc.uploader_id,
                    AuthSession.revoked_at.is_(None),AuthSession.expires_at>datetime.now(UTC)).order_by(AuthSession.created_at.desc()))
                if sessions is None:raise RuntimeError('Existing authorized session required')
                principal=await AuthService(db).load_principal(doc.uploader_id,sessions.id)
            service=AIConversationService(db)
            async with service.transaction(principal):await service.owned(principal,UUID(plan['channel_id']),UUID(plan['conversation_id']))
            with tempfile.TemporaryDirectory(prefix='offline-acceptance-') as folder:
                local=copy.deepcopy(plan);local['budget_approval_id']='OFFLINE_ONLY'
                ledger_path=Path(folder)/'offline-budget.sqlite3'
                ledger=PilotLedger(ledger_path)
                ledger.approve('OFFLINE_ONLY',plan['channel_id'],plan['model'],1_000_000,1_000_000,1,time_now()+600,600_000,2_000_000)
                boundary=PilotBoundary(local,ledger,Path(folder)/'trace.jsonl')
                settings=get_settings().model_copy(update={'ai_chat_v2_vision_ready':True,'ai_chat_v2_original_first':True,
                    'ai_chat_v2_live_approved':True,'openai_api_key':SecretStr('OFFLINE_NOT_A_REAL_KEY'),
                    'ai_chat_v2_budget_approval_id':'OFFLINE_ONLY','ai_chat_v2_budget_ledger':str(ledger_path)})
                def handler(request):
                    body=json.loads(request.content)
                    if 'PRIVATE_GOLD_CANARY' in request.content.decode('utf8'):raise AssertionError('Gold leaked')
                    images=[]
                    for message in body['input']:
                        for part in message.get('content',[]) if isinstance(message.get('content'),list) else []:
                            if part.get('type')=='input_image':
                                images.append(hashlib.sha256(base64.b64decode(part['image_url'].split(',',1)[1])).hexdigest())
                    review=body['input'][0]['content']==REVIEW
                    observed.append({'boundary':'review' if review else 'generation','input_mode':'image' if images else 'text',
                        'original_image_sha256':images,'model_requested':body['model'],'sdk_store':body['store'],
                        'transport':'MockTransport; no network or real model response'})
                    if len(observed)==1:
                        text='<tool_call>'+json.dumps({'tool':'read_original_units','arguments':{
                            'document_id':plan['document_id'],'revision':plan['source_sha256'],'units':[7]}})+'</tool_call>'
                    elif review:
                        text=json.dumps({'source_claims':[],'all_document_claims_reviewed':True,
                            'required_scope':'general','scope_resolved':True,'source_task_complete':True})
                    else:text='[OFFLINE_PROTOCOL_ONLY; NOT A GPT ANSWER]'
                    response={'id':'resp_OFFLINE_ONLY','object':'response','created_at':0,'model':plan['model'],'status':'completed',
                        'output':[{'type':'message','id':'msg_OFFLINE_ONLY','status':'completed','role':'assistant','content':[
                            {'type':'output_text','text':text,'annotations':[]}]}],
                        'usage':{'input_tokens':100,'output_tokens':50,'total_tokens':150}}
                    return httpx2.Response(200,headers={'content-type':'text/event-stream','x-request-id':'OFFLINE_NOT_PROVIDER'},
                        content='data: '+json.dumps({'type':'response.completed','response':response,'sequence_number':1})+'\n\n')
                def client(**kwargs):
                    actual=factory(**kwargs,http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
                    create=actual.responses.create
                    async def guarded(**request):
                        boundary.before_sdk(request)
                        return await create(**request)
                    actual.responses.create=guarded
                    return actual
                tools=DocumentTools(service,principal,UUID(plan['channel_id']),UUID(plan['conversation_id']),settings=settings)
                with patch('app.services.ai_chat_v2_budget.BudgetLedger',PilotLedger),\
                     patch('app.services.openai_provider.get_settings',return_value=settings),\
                     patch('app.services.openai_provider.AsyncOpenAI',side_effect=client):
                    provider=BudgetedProvider(openai_provider.complete,settings,plan['channel_id'],'OFFLINE_ONLY_JOB')
                    await orchestrate(question,[],tools,provider)
                    before=len(observed)
                    try:await model_call(openai_provider.complete,[{'role':'user','content':'Legacy forbidden'}],natural=False)
                    except Exception:pass
                    else:raise AssertionError('Unbudgeted legacy reached SDK')
                    assert len(observed)==before
                expected=next(i['sha256'] for i in plan['images'] if i['physical_page']==7)
                assert observed[1]['original_image_sha256']==[expected]
                assert observed[2]['original_image_sha256']==[expected]
                proof={'classification':'OBSERVED_OFFLINE_SERIALIZATION; NOT LIVE GPT','document_id':plan['document_id'],
                    'source_sha256':plan['source_sha256'],'physical_page':7,'sdk_requests':observed,
                    'no_gold_or_answer_key_injected':True,'unbudgeted_legacy_denied_before_transport':True,
                    'model_api_calls':0,'real_provider_request_ids':[],
                    'max_simulated_reservation_microusd':max(r['amount'] for r in ledger.snapshot('OFFLINE_ONLY')),
                    'runtime_flags_changed':False,'user_data_mutated':False}
                (target/'original-to-sdk-preflight.json').write_text(json.dumps(proof,indent=2),encoding='utf8')
                print(json.dumps(proof,indent=2))
    finally:await openai_provider.close_clients();await engine.dispose()


def time_now():
    import time
    return time.time()


if __name__=='__main__':asyncio.run(run(sys.argv[1]))
