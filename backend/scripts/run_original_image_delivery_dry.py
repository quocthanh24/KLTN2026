"""READ-ONLY actual-MinIO/browser -> V2 -> real SDK serialization; no GPT inference.

MockTransport returns protocol actions only. OS socket guard denies external
connections, credentials are fake in this process, no paid grant is loaded.
"""
import asyncio,base64,hashlib,ipaddress,json,os,socket,sys
from contextvars import ContextVar
from pathlib import Path
from uuid import UUID,uuid4
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
PLAN=json.loads((ROOT/'evidence/ai-v2-four-corners-original-live-20261102/pilot-plan.json').read_text())
OUT=ROOT/'evidence/ai-v2-original-image-delivery-fix-20261102';OUT.mkdir(parents=True,exist_ok=True)
os.environ.update(OPENAI_API_KEY='OFFLINE_DRY_ONLY',AI_CHAT_V2_LIVE_APPROVED='false',AI_CHAT_V2_VISION_READY='false')
connect=socket.socket.connect
def local_only(sock,address):
    if isinstance(address,tuple) and not ipaddress.ip_address(address[0]).is_loopback:raise RuntimeError('EXTERNAL_NETWORK_FORBIDDEN')
    return connect(sock,address)
socket.socket.connect=local_only
import httpx2,openai
from app.services import openai_provider
from app.services.ai_chat_v2 import orchestrate,REVIEW
from app.services.ai_chat_v2_tool_contract import payload_metadata,require_original_images,expected_images
from app.services.ai_chat_v2_tools import DocumentTools
from app.services.ai_conversation_service import AIConversationService
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.exception_handlers import app_error_handler
from app.api.dependencies import CurrentPrincipal,DbSession
from app.api.v1.router import router
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field
case=ContextVar('dry_case',default=None)
factory=openai.AsyncOpenAI

def transport(request):
    body=json.loads(request.content);state=case.get()
    if state is None:raise RuntimeError('UNSCOPED_SDK_FORBIDDEN')
    require_original_images(body['input'],expected_images(body['input']))
    metadata=payload_metadata(body['input'],body.get('tools'))
    review=body['input'][0]['content']==REVIEW
    stage='review' if review else 'generation'
    request_id='OFFLINE_SDK_SERIALIZATION_'+str(uuid4())
    for image in metadata['images']:
        if image['sha256']!=state['expected_sha256']:raise AssertionError('Serialized SDK image differs from actual original')
    state['dispatches'].append({'stage':stage,'request_id':request_id,'model_requested':body['model'],
        'document_id':PLAN['document_id'],'revision':PLAN['source_sha256'],'target_physical_page':state['page'],
        'source_page_sent':state['page'] if metadata['images'] else None,
        'transport':'MockTransport; NO NETWORK, NO GPT RESPONSE',**metadata})
    if not metadata['images']:
        if not body.get('tools') or not any(t['name']=='read_original_units' for t in body['tools']):raise AssertionError('Missing native schemas')
        output=[{'type':'function_call','id':'fc_OFFLINE','call_id':'call_OFFLINE','name':'read_original_units','status':'completed',
            'arguments':json.dumps({'document_id':PLAN['document_id'],'revision':PLAN['source_sha256'],'units':[state['page']]})}]
    else:
        value=json.dumps({'source_claims':[],'all_document_claims_reviewed':True,'required_scope':'general','scope_resolved':True,'source_task_complete':True}) if review else 'OFFLINE_PROTOCOL_ONLY; NOT A GPT ANSWER.'
        output=[{'type':'message','id':'msg_OFFLINE','status':'completed','role':'assistant','content':[{'type':'output_text','text':value,'annotations':[]}]}]
    response={'id':'resp_OFFLINE_ONLY','object':'response','created_at':0,'model':PLAN['model'],'status':'completed','output':output,
        'usage':{'input_tokens':100,'output_tokens':10,'total_tokens':110}}
    return httpx2.Response(200,headers={'content-type':'text/event-stream','x-request-id':request_id},content='data: '+json.dumps({'type':'response.completed','response':response,'sequence_number':1})+'\n\n')

def client(**kw):return factory(**kw,http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(transport)))
openai_provider.AsyncOpenAI=client
def no_sync(*a,**kw):raise RuntimeError('SYNC_PAID_CLIENT_FORBIDDEN')
openai.OpenAI=no_sync
app=FastAPI(title='V2 original image delivery OFFLINE DRY RUN')
app.add_exception_handler(AppError,app_error_handler)
app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:3033'],allow_credentials=True,allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type'])
@app.middleware('http')
async def no_writes(request,call_next):
    if request.method not in {'GET','OPTIONS'} and request.url.path!=f"/api/v1/channels/{PLAN['channel_id']}/ai/original-delivery-dry-run":
        return JSONResponse({'error':{'code':'DRY_RUN_ONLY','message':'Dry run: no inference or business writes allowed.','fields':None}},status_code=403)
    return await call_next(request)

class Input(BaseModel):page:int=Field(ge=5,le=7)
@app.post('/api/v1/channels/{channel}/ai/original-delivery-dry-run')
async def dry(channel:str,body:Input,p:CurrentPrincipal,db:DbSession):
    if channel!=PLAN['channel_id']:raise AppError(403,'DRY_CHANNEL_SCOPE','Outside authorized dry Channel.')
    service=AIConversationService(db)
    async with service.transaction(p):await service.owned(p,UUID(channel),UUID(PLAN['conversation_id']))
    settings=get_settings().model_copy(update={'ai_chat_v2_enabled':True,'ai_chat_v2_channel_ids':channel,'ai_chat_v2_original_first':True,'ai_chat_v2_vision_ready':True})
    tools=DocumentTools(service,p,UUID(channel),UUID(PLAN['conversation_id']),settings=settings)
    state={'page':body.page,'expected_sha256':next(v['sha256'] for v in PLAN['images'] if v['physical_page']==body.page),'dispatches':[]}
    token=case.set(state)
    try:await orchestrate('Read the requested original physical page '+str(body.page)+'. Do not substitute index text.',[],tools,openai_provider.complete)
    finally:case.reset(token)
    if not any(d['images'] for d in state['dispatches']):raise AssertionError('No image in final serialized SDK request')
    from sqlalchemy import select,func
    from app.models.learning_document import LearningDocument,DocumentChunk
    from app.models.document_derivation import DocumentDerivation
    async with service.transaction(p):
        doc=await db.get(LearningDocument,UUID(PLAN['document_id']),populate_existing=True)
        count=await db.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id==doc.id))
        derivation=await db.get(DocumentDerivation,doc.id)
        source_state={'legacy_index_status':doc.status.value,'chunks':count,'derived_summary':derivation.state.get('summary') if derivation else None}
    proof={'classification':'OFFLINE_BROWSER_SDK_TRANSPORT; NOT LIVE GPT','engine':'V2 Original-First','paid_inference_calls':0,
        'channel_id':channel,'document_id':PLAN['document_id'],'source_revision':PLAN['source_sha256'],'physical_page':body.page,
        'dispatches':state['dispatches'],'reader_called':tools.original_requested,'images_prepared':tools.image_count,
        'no_user_data_mutated':True,'real_gpt_understanding':'NOT_VERIFIED',**source_state}
    (OUT/f'browser-sdk-page-{body.page}.json').write_text(json.dumps(proof,indent=2))
    return {'data':proof}
app.include_router(router)
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8033,access_log=False)
