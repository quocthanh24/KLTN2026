"""Server authority and durable outbox. No proof => no COMPLETE."""
import asyncio
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import time
from datetime import timedelta
from functools import lru_cache
from uuid import UUID
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
import httpx
from sqlalchemy import select,update,func,text
from app.core.config import get_settings
from app.models.media_grant import MediaGrant,MediaRevocation


class SFUProof(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    operation:str=Field(min_length=36,max_length=36)
    boot_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    pending:Literal[False]
    barrier:Literal['transport-closed-v1']
    fenced_grants:list[str]
    closed_sids:list[str]
    closed_at_ms:int=Field(gt=0)


@lru_cache(maxsize=1)
def service_key():
    key=Path(get_settings().media_service_key_file).read_bytes()
    if len(key)<32: raise ValueError('Media service key unavailable')
    return key


def sign(raw,stamp,nonce):
    return hmac.new(service_key(),(stamp+'\n'+nonce+'\n').encode()+raw,hashlib.sha256).hexdigest()


def response_proof(raw,nonce):
    return hmac.new(service_key(),nonce.encode()+b'\n'+raw,hashlib.sha256).hexdigest()


async def revoke_in_transaction(db,scope,scope_id,actor=None):
    """Caller owns policy/status transaction and appropriate scope row lock."""
    if not get_settings().media_authority_enabled:return None
    field={'USER':MediaGrant.user_id,'WORKSPACE':MediaGrant.workspace_id,'SESSION':MediaGrant.auth_session_id,
           'CHANNEL':MediaGrant.channel_id,'MEMBERSHIP':MediaGrant.membership_id}[scope]
    ids=list(await db.scalars(select(MediaGrant.id).where(field==scope_id)))
    await db.execute(update(MediaGrant).where(field==scope_id,MediaGrant.revoked_at.is_(None)).values(revoked_at=func.clock_timestamp()))
    # Empty sets still need the SFU barrier; no synthetic completion.
    row=MediaRevocation(scope=scope,scope_id=scope_id,actor_id=actor,grant_ids=[str(i) for i in ids])
    db.add(row);await db.flush()
    return row.id


AUTHORITY_QUERY=text('''SELECT EXISTS (
 SELECT 1 FROM media_grants g
 JOIN users u ON u.id=g.user_id
 JOIN auth_sessions s ON s.id=g.auth_session_id AND s.user_id=g.user_id
 JOIN workspaces w ON w.id=g.workspace_id
 JOIN channels c ON c.id=g.channel_id AND c.workspace_id=g.workspace_id
 JOIN workspace_members m ON m.id=g.membership_id AND m.user_id=g.user_id AND m.workspace_id=g.workspace_id
 WHERE g.id=:grant AND g.user_id=:identity AND g.channel_id=:channel
 AND g.revoked_at IS NULL AND g.expires_at>clock_timestamp()
 AND u.status='ACTIVE' AND u.admin_version=g.user_version AND w.admin_version=g.workspace_version
 AND w.disabled_at IS NULL AND w.deleted_at IS NULL AND c.type='STUDY_ROOM'
 AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp())''')

async def allowed(db,grant,identity,room):
    # Fixed prepared read query avoids ORM compilation/hydration on the renewal
    # hot path. All policy predicates are explicit (no implicit ORM filters).
    if not room.startswith('study-'):return False
    try: channel=UUID(room[6:])
    except ValueError:return False
    return bool(await db.scalar(AUTHORITY_QUERY,{'grant':grant,'identity':identity,'channel':channel}))


def public_operation(row):
    return {k:getattr(row,k) for k in ('id','scope','scope_id','state','created_at','dispatch_at','completed_at','attempts','error_code')}


async def dispatch_one(db,operation_id):
    async with db.begin():
        row=await db.scalar(select(MediaRevocation).where(MediaRevocation.id==operation_id).with_for_update(skip_locked=True))
        if row is None or row.state=='COMPLETE':return
        row.attempts+=1;row.dispatch_at=await db.scalar(select(func.clock_timestamp()))
        try:
            chunks=[row.grant_ids[i:i+1000] for i in range(0,len(row.grant_ids),1000)] or [[]]
            proofs=[]
            async with httpx.AsyncClient(timeout=3,follow_redirects=False) as client:
                for grants in chunks:
                    raw=json.dumps({'operation':str(row.id),'grants':grants},separators=(',',':')).encode()
                    stamp=str(int(time.time()*1000));nonce=secrets.token_hex(16)
                    headers={'Content-Type':'application/json','X-Media-Time':stamp,'X-Media-Nonce':nonce,'X-Media-MAC':sign(raw,stamp,nonce)}
                    response=await client.post(get_settings().media_control_url.rstrip('/')+'/revoke',content=raw,headers=headers)
                    if len(response.content)>262144 or response.status_code!=200 or not hmac.compare_digest(response.headers.get('X-Media-Decision-MAC',''),response_proof(response.content,nonce)):
                        raise ValueError('unverified response')
                    proof=SFUProof.model_validate_json(response.content).model_dump()
                    if proof.get('operation')!=str(row.id) or not proof.get('boot_id') or proof.get('pending') is not False or proof.get('barrier')!='transport-closed-v1' or sorted(proof.get('fenced_grants',[]))!=sorted(grants):
                        raise ValueError('incomplete barrier')
                    proofs.append(proof)
            if len({p['boot_id'] for p in proofs})!=1:raise ValueError('SFU restarted during barrier')
            ack=proofs[0] if len(proofs)==1 else {'operation':str(row.id),'boot_id':proofs[0]['boot_id'],'barrier':'transport-closed-v1','pending':False,
                'chunks':proofs,'fenced_grants':row.grant_ids,'closed_at_ms':max(p['closed_at_ms'] for p in proofs)}
            row.ack=ack;row.state='COMPLETE';row.completed_at=await db.scalar(select(func.clock_timestamp()));row.error_code=None
        except (httpx.HTTPError,ValueError,OSError):
            row.error_code='SFU_ACK_UNAVAILABLE';row.state='PENDING'
            row.next_attempt_at=await db.scalar(select(func.clock_timestamp()))+timedelta(seconds=min(30,2**min(row.attempts,4)))


async def media_outbox_worker():
    from app.db.session import session_factory
    while True:
        try:
            async with session_factory() as db:
                async with db.begin():
                    ids=list(await db.scalars(select(MediaRevocation.id).where(MediaRevocation.state=='PENDING',MediaRevocation.next_attempt_at<=func.clock_timestamp()).order_by(MediaRevocation.created_at).limit(20)))
            for identity in ids:
                async with session_factory() as db: await dispatch_one(db,identity)
        except Exception:
            # Fail-closed SFU lease remains independent of this worker/DB process.
            import logging
            logging.getLogger(__name__).warning('Media outbox retry pending')
        await asyncio.sleep(.3)
