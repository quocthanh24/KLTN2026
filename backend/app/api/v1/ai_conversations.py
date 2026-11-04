from uuid import UUID
from typing import Literal
import unicodedata
from fastapi import APIRouter, Query, Depends, Response, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.api.dependencies import CurrentPrincipal, DbSession
from app.services.ai_conversation_service import AIConversationService
from app.core.exceptions import AppError


def private(response:Response): response.headers['Cache-Control']='private, no-store'
router=APIRouter(prefix='/channels/{channel_id}/ai/conversations',tags=['ai'],dependencies=[Depends(private)])


class Title(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=1,max_length=120)
    @field_validator('title')
    @classmethod
    def clean(cls,v):
        v=v.strip()
        if not v or any(unicodedata.category(c)=='Cc' for c in v): raise ValueError('Tên không hợp lệ')
        return v


class SourceSelection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    document_id:UUID
    page_numbering:Literal['unspecified','physical','printed']='unspecified'


class Question(BaseModel):
    model_config=ConfigDict(extra='forbid')
    question:str=Field(min_length=1,max_length=2000)
    request_id:UUID
    source_selection:SourceSelection | None=None
    @field_validator('question')
    @classmethod
    def clean(cls,v):
        if not v.strip(): raise ValueError('Câu hỏi không được trống')
        return v.strip()


@router.get('')
async def listing(channel_id:UUID,p:CurrentPrincipal,db:DbSession,offset:int=Query(0,ge=0,le=100000)):
    return {'data':await AIConversationService(db).listing(p,channel_id,offset)}


@router.post('',status_code=201)
async def create(channel_id:UUID,p:CurrentPrincipal,db:DbSession):
    return {'data':await AIConversationService(db).create(p,channel_id)}


@router.get('/{conversation_id}')
async def detail(channel_id:UUID,conversation_id:UUID,p:CurrentPrincipal,db:DbSession,offset:int=Query(0,ge=0,le=100000)):
    return {'data':await AIConversationService(db).detail(p,channel_id,conversation_id,offset)}


@router.patch('/{conversation_id}')
async def rename(channel_id:UUID,conversation_id:UUID,body:Title,p:CurrentPrincipal,db:DbSession):
    return {'data':await AIConversationService(db).rename(p,channel_id,conversation_id,body.title)}


@router.delete('/{conversation_id}',status_code=204)
async def remove(channel_id:UUID,conversation_id:UUID,p:CurrentPrincipal,db:DbSession):
    await AIConversationService(db).remove(p,channel_id,conversation_id)
    return Response(status_code=204,headers={'Cache-Control':'private, no-store'})


@router.post('/{conversation_id}/questions')
async def ask(channel_id:UUID,conversation_id:UUID,body:Question,p:CurrentPrincipal,db:DbSession,request:Request):
    data=await AIConversationService(db).ask(p,channel_id,conversation_id,body.question,body.request_id,
        source_selection=body.source_selection.model_dump(mode='json') if body.source_selection else None)
    data.setdefault('timings_ms',{})['authentication_ms']=getattr(request.state,'ai_auth_ms',0)
    return {'data':data}


@router.get('/{conversation_id}/requests/{request_id}/progress')
async def request_progress(channel_id:UUID,conversation_id:UUID,request_id:UUID,p:CurrentPrincipal,db:DbSession):
    return {'data':await AIConversationService(db).request_progress(p,channel_id,conversation_id,request_id)}


@router.get('/{conversation_id}/turns/{turn_id}/sources/{index}')
async def source(channel_id:UUID,conversation_id:UUID,turn_id:UUID,index:int,p:CurrentPrincipal,db:DbSession):
    return {'data':await AIConversationService(db).source(p,channel_id,conversation_id,turn_id,index)}


def v2_channel(channel_id):
    from app.core.config import get_settings
    from app.services.ai_chat_v2 import enabled
    if not enabled(get_settings(),channel_id):
        raise AppError(404,'AI_V2_DISABLED','V2 không bật cho Channel này.')


@router.get('/{conversation_id}/v2/tasks/{job_id}')
async def v2_task(channel_id:UUID,conversation_id:UUID,job_id:UUID,p:CurrentPrincipal,db:DbSession):
    v2_channel(channel_id)
    from app.services.ai_chat_v2_jobs import JobStore
    return {'data':await JobStore(AIConversationService(db),p,channel_id,conversation_id).inspect(job_id)}


@router.post('/{conversation_id}/v2/tasks/{job_id}/cancel')
async def v2_cancel(channel_id:UUID,conversation_id:UUID,job_id:UUID,p:CurrentPrincipal,db:DbSession):
    v2_channel(channel_id)
    from app.services.ai_chat_v2_jobs import JobStore
    return {'data':await JobStore(AIConversationService(db),p,channel_id,conversation_id).cancel(job_id)}


@router.post('/{conversation_id}/v2/tasks/{job_id}/resume')
async def v2_resume(channel_id:UUID,conversation_id:UUID,job_id:UUID,p:CurrentPrincipal,db:DbSession):
    v2_channel(channel_id)
    from app.services.ai_chat_v2_jobs import JobStore
    service=AIConversationService(db)
    question,request_id=await JobStore(service,p,channel_id,conversation_id).resume_request(job_id)
    return {'data':await service.ask(p,channel_id,conversation_id,question,request_id,resume_v2=True)}
