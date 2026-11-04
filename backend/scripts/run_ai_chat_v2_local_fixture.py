"""Isolated local-only test server: real API/PG/MinIO/E5, FAKE model.

Never run on the application DB. No workers, no paid SDK clients, no deletion.
Secrets/test credentials are written only to a private temp file, never evidence.
"""
import asyncio
import hashlib
import io
import json
import os
import secrets
import socket
import sys
import time
from contextlib import asynccontextmanager
from datetime import UTC,datetime,timedelta
from pathlib import Path
from uuid import uuid4

BACKEND=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BACKEND))
from dotenv import dotenv_values
from sqlalchemy.engine import make_url
url=os.environ.get('TEST_DATABASE_URL') or dotenv_values(BACKEND/'.env').get('TEST_DATABASE_URL','')
if not url or make_url(url).database!='kltn_test':raise RuntimeError('Dedicated kltn_test required')
os.environ.update(DATABASE_URL=url,OPENAI_API_KEY='',GEMINI_API_KEY='',DOCUMENT_VISION_ENABLED='false',
    APP_ENV='test',REFRESH_COOKIE_NAME='v2_fixture_refresh',
    DOCUMENT_PROGRESSIVE_ENABLED='true' if os.environ.get('V2_FIXTURE_ORIGINAL_FIRST')=='1' else 'false')
from app.core.config import get_settings
get_settings.cache_clear()
from app.db.session import session_factory,engine,get_db
from app.models import User,Workspace,WorkspaceMember,Channel
from app.models.enums import WorkspaceRole,ChannelType
from app.models.learning_document import LearningDocument,DocumentChunk,DocumentStatus
from app.core.security import hash_password
from app.services.learning_document_service import LearningDocumentService
from app.services.document_pipeline import run_pipeline
from app.services.ai_chat_v2_budget import BudgetLedger
from tests.ai_chat_v2_fixture_support import native_pdf,DeterministicProvider
from starlette.datastructures import UploadFile,Headers

ORIGINAL_FIRST=os.environ.get('V2_FIXTURE_ORIGINAL_FIRST')=='1'
STATE=Path(os.environ.get('V2_FIXTURE_STATE','D:/Temp/kltn-v2-local-fixture.json'))
CONTROL=secrets.token_urlsafe(32)


async def seed():
    from app.models.auth_session import AuthSession
    from app.services.auth_service import Principal
    password=secrets.token_urlsafe(20)
    async with session_factory() as db:
        async with db.begin():
            user=User(full_name='Synthetic V2 Integration Owner',email=f'v2-{uuid4().hex}@example.com',password_hash=await hash_password(password))
            outsider=User(full_name='Synthetic V2 Outsider',email=f'v2-out-{uuid4().hex}@example.com',password_hash=await hash_password(password))
            workspace=Workspace(name='V2 local synthetic fixture '+uuid4().hex[:8])
            db.add_all([user,outsider,workspace]);await db.flush()
            channel=Channel(workspace_id=workspace.id,name='v2-synthetic-study',type=ChannelType.TEXT,is_default=True)
            db.add_all([channel,WorkspaceMember(workspace_id=workspace.id,user_id=user.id,role=WorkspaceRole.OWNER)])
            session=AuthSession(user_id=user.id,refresh_token_hash=uuid4().hex+uuid4().hex,expires_at=datetime.now(UTC)+timedelta(hours=2))
            db.add(session);await db.flush()
        principal=Principal(user=user,session=session)
        raw=native_pdf(tuple(f'Synthetic lesson page {i+1}: distinct skill {i+1}, review the supporting passage.' for i in range(3 if ORIGINAL_FIRST else 20)))
        doc=await LearningDocumentService(db).upload(principal,channel.id,
            UploadFile(io.BytesIO(raw),filename='synthetic-curriculum.pdf',headers=Headers({'content-type':'application/pdf'})),
            name='Synthetic curriculum - deterministic provider ONLY')
        if ORIGINAL_FIRST:
            from app.services.document_processing import process_pending
            from tests.test_original_first_v2 import image_pdf
            from pypdf import PdfWriter
            await process_pending(db,document_id=doc.id)
            writer=PdfWriter();writer.append(io.BytesIO(native_pdf(('Native navigation text.',))))
            writer.append(io.BytesIO(image_pdf('Scan remains available without OCR.')))
            mixed=io.BytesIO();writer.write(mixed)
            partial=await LearningDocumentService(db).upload(principal,channel.id,
                UploadFile(io.BytesIO(mixed.getvalue()),filename='mixed-partial.pdf',headers=Headers({'content-type':'application/pdf'})),
                name='Synthetic mixed partial - original preserved')
            await process_pending(db,document_id=partial.id)
        else:
            chunks=await asyncio.to_thread(run_pipeline,raw,'.pdf',get_settings().rag_model_dir,hashlib.sha256(raw).hexdigest())
            async with db.begin():
                row=await db.get(LearningDocument,doc.id);row.status=DocumentStatus.READY;row.processed_at=datetime.now(UTC)
                for ordinal,chunk in enumerate(chunks):db.add(DocumentChunk(document_id=doc.id,ordinal=ordinal,**chunk))
    os.environ.update(AI_CHAT_BILLING_MODE='capped',AI_CHAT_V2_LIVE_APPROVED='true',
        AI_CHAT_V2_ORIGINAL_FIRST='true' if ORIGINAL_FIRST else 'false',
        AI_CHAT_V2_VISION_READY='true' if ORIGINAL_FIRST else 'false',
        AI_CHAT_V2_BUDGET_APPROVAL_ID='FAKE-LOCAL-ONLY-'+uuid4().hex,
        AI_CHAT_V2_BUDGET_LEDGER=str(STATE.with_suffix('.budget.sqlite3')))
    get_settings.cache_clear()
    s=get_settings()
    BudgetLedger(s.ai_chat_v2_budget_ledger).approve(s.ai_chat_v2_budget_approval_id,channel.id,s.openai_model,
        100_000_000,10_000_000,4,time.time()+7200,1000,1000)
    data={'api':'http://127.0.0.1:8031','channel_id':str(channel.id),'workspace_id':str(workspace.id),
        'document_id':str(doc.id),'original_first':ORIGINAL_FIRST,'email':user.email,'password':password,'outsider_email':outsider.email,
        'control':CONTROL,'model':'DETERMINISTIC FAKE; no GPT calls','created_at':datetime.now(UTC).isoformat()}
    STATE.parent.mkdir(parents=True,exist_ok=True);STATE.write_text(json.dumps(data),encoding='utf8')
    await engine.dispose() # seed() and Uvicorn own different event loops.


asyncio.run(seed())
from app.services import ai_conversation_service as conversations
from tests.original_first_fixture_provider import OriginalFixtureProvider
fake=(OriginalFixtureProvider if ORIGINAL_FIRST else DeterministicProvider)(get_settings().openai_model)
async def provider(messages):
    await asyncio.sleep(float(os.environ.get('V2_FIXTURE_MODEL_DELAY','0.06'))) # Allows pending-request/cancellation tests.
    return await fake(messages)
conversations.complete=provider

# A hard last line of defence, also covering provider aliases imported later.
original_dns=socket.getaddrinfo
def local_dns(host,*args,**kwargs):
    if host not in {'127.0.0.1','localhost','::1',None}:raise RuntimeError('External services forbidden in fixture')
    return original_dns(host,*args,**kwargs)
socket.getaddrinfo=local_dns
import openai
def no_paid_client(*args,**kwargs):raise RuntimeError('Paid API disabled in fixture process')
openai.OpenAI=openai.AsyncOpenAI=no_paid_client

from fastapi import FastAPI,Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from app.api.v1.router import router
from app.core.exceptions import AppError
from app.core.exception_handlers import app_error_handler,http_error_handler,unexpected_error_handler,validation_error_handler

@asynccontextmanager
async def lifespan(app):
    yield
    await engine.dispose()
app=FastAPI(title='V2 LOCAL SYNTHETIC TEST - NO PAID MODEL',lifespan=lifespan)
for error,handler in [(AppError,app_error_handler),(RequestValidationError,validation_error_handler),
        (HTTPException,http_error_handler),(Exception,unexpected_error_handler)]:app.add_exception_handler(error,handler)
app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:3000','http://127.0.0.1:3000',
    'http://localhost:3031','http://127.0.0.1:3031'],allow_credentials=True,allow_methods=['*'],allow_headers=['*'])
app.include_router(router)

@app.post('/__v2_fixture__/funding/{value}')
async def funding(value:str,x_v2_fixture_control:str=Header('')):
    if not secrets.compare_digest(x_v2_fixture_control,CONTROL):raise AppError(403,'TEST_DENIED','Denied')
    if value not in {'on','off'}:raise AppError(422,'TEST_INVALID','Invalid flag')
    os.environ['AI_CHAT_V2_LIVE_APPROVED']='true' if value=='on' else 'false';get_settings.cache_clear()
    return {'v2':value,'model':'fake-only'}

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8031,access_log=False)
