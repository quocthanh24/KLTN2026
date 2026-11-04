import asyncio
import hmac
import json
import time
import logging
from collections import OrderedDict
from uuid import UUID
from typing import Annotated,Literal
from fastapi import APIRouter,Request,Response,HTTPException,Depends
from sqlalchemy.exc import TimeoutError as PoolTimeout,DBAPIError
from app.db.media_authority import get_media_pools
from pydantic import BaseModel,ConfigDict,Field
from app.api.dependencies import CurrentPrincipal,DbSession
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.models.media_grant import MediaRevocation
from app.services.media_authority import allowed,sign,response_proof,public_operation
from app.services.administration_service import AdministrationService
router=APIRouter()
_nonces=OrderedDict()

class Binding(BaseModel):
    model_config=ConfigDict(extra='forbid')
    grant:UUID
    identity:UUID
    room:str=Field(max_length=100)
    purpose:Literal['join','lease','refresh']='join'

@router.post('/internal/media/authorize')
async def authorize(request:Request,pools:Annotated[dict,Depends(get_media_pools)]):
    if not get_settings().media_authority_enabled:raise HTTPException(503)
    raw=b''
    async for chunk in request.stream():
        if len(raw)+len(chunk)>1024:raise HTTPException(413)
        raw+=chunk
    stamp,nonce,mac=(request.headers.get(k,'') for k in ('X-Media-Time','X-Media-Nonce','X-Media-MAC'))
    try:
        fresh=abs(time.time()*1000-int(stamp))<=5000 and len(nonce)==32
        valid=fresh and hmac.compare_digest(mac,sign(raw,stamp,nonce))
    except (ValueError,OSError):valid=False
    if not valid:raise HTTPException(403)
    now=time.monotonic()
    while _nonces and next(iter(_nonces.values()))<now-10:_nonces.popitem(last=False)
    if nonce in _nonces or len(_nonces)>100000:raise HTTPException(403)
    _nonces[nonce]=now  # atomic until next await; one configured backend worker
    try: binding=Binding.model_validate_json(raw)
    except ValueError:raise HTTPException(422) from None
    started=time.perf_counter()
    try:
        async with asyncio.timeout(.35):
            async with pools[binding.purpose]() as db:
                async with db.begin(): decision=await allowed(db,binding.grant,binding.identity,binding.room)
    except (TimeoutError,PoolTimeout,DBAPIError):
        logging.getLogger(__name__).warning('Media authority unavailable purpose=%s elapsed_ms=%d',binding.purpose,(time.perf_counter()-started)*1000)
        raise HTTPException(503) from None
    if not decision or time.perf_counter()-started>.1:
        logging.getLogger(__name__).warning('Media authority decision purpose=%s allowed=%s elapsed_ms=%d',binding.purpose,decision,(time.perf_counter()-started)*1000)
    body=json.dumps({'allow':decision},separators=(',',':')).encode()
    return Response(body,media_type='application/json',headers={'X-Media-Decision-MAC':response_proof(body,nonce),'Cache-Control':'no-store'})

@router.get('/admin/media-revocations/{identity}')
async def operation(identity:UUID,principal:CurrentPrincipal,db:DbSession,response:Response):
    async with AdministrationService(db).admin_transaction(principal):
        row=await db.get(MediaRevocation,identity)
        if row is None:raise AppError(404,'MEDIA_OPERATION_NOT_FOUND','Không tìm thấy thao tác thu hồi')
        response.headers['Cache-Control']='private, no-store'
        return {'data':public_operation(row)}
