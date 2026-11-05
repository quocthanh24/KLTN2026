"""Real PostgreSQL policy/transaction tests. HTTP fake used only to reject forged ACKs."""
import json,secrets,time,unittest
from uuid import UUID
from unittest.mock import patch
import httpx
from sqlalchemy import select,delete
from app.core.config import get_settings
from app.models import WorkspaceMember
from app.models.media_grant import MediaRevocation
from app.services.media_authority import sign,allowed,revoke_in_transaction,dispatch_one
from tests import test_chat_realtime as database
from tests import test_study_sessions as fixture
from tests.test_channel_chat_hardening import HttpAssertions
setUpModule=database.setUpModule
tearDownModule=database.tearDownModule

class MediaAuthorityTests(HttpAssertions,unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.StudySessionTests.asyncSetUp(self)
        self.old=get_settings().media_authority_enabled
        get_settings().media_authority_enabled=True
    async def asyncTearDown(self):
        get_settings().media_authority_enabled=self.old
        async with self.factory() as db,db.begin():
            await db.execute(delete(MediaRevocation).where(MediaRevocation.actor_id.in_(self.user_ids)))
        await fixture.StudySessionTests.asyncTearDown(self)
    async def token(self):
        async with self.client() as client:
            r=await client.post(f'/channels/{self.room}/study-room/media-token',headers=self.headers(self.owner));self.assertEqual(r.status_code,200,r.text)
            return r.json()['data']
    async def test_grant_bound_to_session_room_and_current_membership(self):
        grant=await self.token();identity=UUID(grant['grant_id'])
        async with self.factory() as db:
            self.assertTrue(await allowed(db,identity,self.owner.user.id,'study-'+str(self.room)))
            self.assertFalse(await allowed(db,identity,self.outsider.user.id,'study-'+str(self.room)))
            self.assertFalse(await allowed(db,identity,self.owner.user.id,'study-'+str(self.other_room)))
        async with self.client() as client:
            r=await client.post(f'/channels/{self.room}/study-room/media-token',headers=self.headers(self.outsider));self.assertEqual(r.status_code,403)
        self.assertEqual((await self.token())['grant_id'],grant['grant_id'])
        async with self.factory() as db,db.begin():
            member=await db.scalar(select(WorkspaceMember).where(WorkspaceMember.user_id==self.owner.user.id,WorkspaceMember.workspace_id==self.workspace_id))
            await db.delete(member)
        async with self.factory() as db:self.assertFalse(await allowed(db,identity,self.owner.user.id,'study-'+str(self.room)))
    async def test_internal_hmac_nonce_binding_and_oversize(self):
        grant=await self.token()
        body=json.dumps({'grant':grant['grant_id'],'identity':str(self.owner.user.id),'room':'study-'+str(self.room)},separators=(',',':')).encode()
        stamp=str(int(time.time()*1000));nonce=secrets.token_hex(16)
        headers={'X-Media-Time':stamp,'X-Media-Nonce':nonce,'X-Media-MAC':sign(body,stamp,nonce),'Content-Type':'application/json'}
        async with self.client() as client:
            r=await client.post('/internal/media/authorize',content=body,headers=headers);self.assertEqual(r.status_code,200);self.assertTrue(r.json()['allow'])
            self.assertEqual((await client.post('/internal/media/authorize',content=body,headers=headers)).status_code,403)
            self.assertEqual((await client.post('/internal/media/authorize',content=b'x'*1025)).status_code,413)
            self.assertEqual((await client.post('/internal/media/authorize',content=body)).status_code,403)
    async def test_rollback_and_revoke_never_revives(self):
        grant=await self.token();gid=UUID(grant['grant_id'])
        async with self.factory() as db:
            try:
                async with db.begin():
                    await revoke_in_transaction(db,'USER',self.owner.user.id,self.owner.user.id)
                    raise RuntimeError('intentional fixture rollback')
            except RuntimeError:pass
        async with self.factory() as db:self.assertTrue(await allowed(db,gid,self.owner.user.id,'study-'+str(self.room)))
        async with self.factory() as db,db.begin():op=await revoke_in_transaction(db,'USER',self.owner.user.id,self.owner.user.id)
        async with self.factory() as db:
            row=await db.get(MediaRevocation,op);self.assertEqual(row.state,'PENDING');self.assertIsNone(row.ack)
            self.assertFalse(await allowed(db,gid,self.owner.user.id,'study-'+str(self.room)))
    async def test_unsigned_or_forged_ack_never_completes(self):
        await self.token()
        async with self.factory() as db,db.begin():op=await revoke_in_transaction(db,'USER',self.owner.user.id,self.owner.user.id)
        # Unit failure injection ONLY: this is not a claimed live SFU test.
        async def fake(*args,**kwargs):return httpx.Response(200,json={'pending':False,'barrier':'transport-closed-v1'},headers={})
        with patch('httpx.AsyncClient.post',side_effect=fake):
            async with self.factory() as db:await dispatch_one(db,op)
        async with self.factory() as db:
            row=await db.get(MediaRevocation,op);self.assertEqual(row.state,'PENDING');self.assertIsNone(row.completed_at);self.assertIsNone(row.ack)
    async def test_signed_but_malformed_ack_never_completes(self):
        from app.services.media_authority import response_proof
        await self.token()
        async with self.factory() as db,db.begin():op=await revoke_in_transaction(db,'USER',self.owner.user.id,self.owner.user.id)
        async def fake(*args,**kwargs):
            raw=json.dumps({'operation':str(op),'boot_id':'a'*32,'pending':False,'barrier':'transport-closed-v1','fenced_grants':[]}).encode()
            return httpx.Response(200,content=raw,headers={'X-Media-Decision-MAC':response_proof(raw,kwargs['headers']['X-Media-Nonce'])})
        with patch('httpx.AsyncClient.post',side_effect=fake):
            async with self.factory() as db:await dispatch_one(db,op)
        async with self.factory() as db:
            row=await db.get(MediaRevocation,op);self.assertEqual(row.state,'PENDING');self.assertIsNone(row.completed_at);self.assertIsNone(row.ack)
