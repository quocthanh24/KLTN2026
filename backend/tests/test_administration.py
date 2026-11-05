"""Phase 7C actual PostgreSQL tests; no mocked authorization/commit success."""
import asyncio
import unittest
from uuid import uuid4
from sqlalchemy import select,delete
from app.models import ChatMessage,LearningDocument,User
from app.models.enums import SystemRole
from app.models.moderation import ViolationReport,AdministrationAudit
from app.schemas.administration import ReportCreate,ReportDecision
from app.services.administration_service import AdministrationService
from app.core.exceptions import AppError
from app.services.report_service import ReportService
from tests import test_study_sessions as fixture
from tests import test_chat_realtime as database
from tests.test_channel_chat_hardening import HttpAssertions
setUpModule=database.setUpModule
tearDownModule=database.tearDownModule


class AdministrationTests(HttpAssertions,unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.StudySessionTests.asyncSetUp
    async def asyncTearDown(self):
        async with self.factory() as db,db.begin():
            await db.execute(delete(AdministrationAudit).where(AdministrationAudit.actor_id.in_(self.user_ids)))
            await db.execute(delete(ViolationReport).where(ViolationReport.workspace_id.in_([self.workspace_id,*self.extra_workspace_ids])))
        await fixture.StudySessionTests.asyncTearDown(self)

    async def promote(self):
        async with self.factory() as db,db.begin():
            row=await db.get(User,self.owner.user.id);row.system_role=SystemRole.ADMIN

    async def targets(self):
        async with self.factory() as db,db.begin():
            message=ChatMessage(channel_id=self.chat,sender_user_id=self.owner.user.id,content='UC41 fixture')
            doc=LearningDocument(channel_id=self.chat,uploader_id=self.owner.user.id,name='test',original_filename='test.pdf',content_type='application/pdf',size_bytes=10,object_key='uc7c-test/'+uuid4().hex,checksum='a'*64)
            db.add_all([message,doc]);await db.flush();return message.id,doc.id

    async def report(self,target_type,target_id,principal=None):
        async with self.factory() as db:
            return await ReportService(db).create(principal or self.owner,ReportCreate(target_type=target_type,target_id=target_id,reason='SPAM'))

    async def test_uc41_reports_and_permissions(self):
        message,doc=await self.targets()
        rows=await asyncio.gather(*(self.report('MESSAGE',message) for _ in range(4)))
        self.assertEqual(len({r['id'] for r in rows}),1)
        self.assertEqual((await self.report('DOCUMENT',doc))['status'],'PENDING')
        async with self.client() as client:
            route='/violation-reports';body={'target_type':'MESSAGE','target_id':str(message),'reason':'SPAM'}
            self.assertEqual((await client.post(route,json=body)).status_code,401)
            self.assertEqual((await client.post(route,json=body,headers=self.headers(self.outsider))).status_code,403)
            for patch in [{'reporter_id':str(self.member.user.id)},{'status':'RESOLVED'},{'reason':''},{'target_type':'USER'}]:
                r=await client.post(route,json=body|patch,headers=self.headers(self.owner));self.assertEqual(r.status_code,422,r.text)
            async with self.factory() as db,db.begin():
                row=await db.get(ChatMessage,message);row.deleted_at=await db.scalar(select(__import__('sqlalchemy').func.now()))
            r=await client.post(route,json=body,headers=self.headers(self.owner));self.assertEqual(r.status_code,404)

    async def test_uc42_system_rbac_lock_unlock_and_stale(self):
        async with self.client() as client:
            path='/admin/users'
            for p in [self.owner,self.member,self.outsider]:
                self.assertEqual((await client.get(path,headers=self.headers(p))).status_code,403)
            await self.promote()
            r=await client.get(path+'?search='+str(self.member.user.id),headers=self.headers(self.owner));self.assertEqual(r.status_code,200,r.text)
            target=path+'/'+str(self.member.user.id)
            r=await client.get(target,headers=self.headers(self.owner));self.assertNotIn('password_hash',r.text)
            body={'version':1,'enabled':False,'reason':'UC42 test lock'}
            r=await client.patch(target+'/state',json=body,headers=self.headers(self.owner));self.assertEqual(r.status_code,200,r.text)
            self.assertEqual((await client.get('/users/me',headers=self.headers(self.member))).status_code,403)
            self.assertEqual((await client.patch(target+'/state',json=body,headers=self.headers(self.owner))).status_code,409)
            r=await client.patch(target+'/state',json=body|{'version':2,'enabled':True},headers=self.headers(self.owner));self.assertEqual(r.status_code,200,r.text)
            self.assertEqual((await client.get('/users/me',headers=self.headers(self.member))).status_code,200)
            r=await client.patch(path+'/'+str(self.owner.user.id)+'/state',json=body,headers=self.headers(self.owner));self.assertEqual(r.status_code,403)

    async def test_uc43_disable_restore_children_and_preservation(self):
        message,doc=await self.targets();await self.promote()
        async with self.client() as client:
            target='/admin/workspaces/'+str(self.workspace_id)
            body={'version':1,'enabled':False,'reason':'UC43 fixture disable'}
            self.assertEqual((await client.patch(target+'/state',json=body,headers=self.headers(self.member))).status_code,403)
            r=await client.patch(target+'/state',json=body,headers=self.headers(self.owner));self.assertEqual(r.status_code,200,r.text)
            for path in [f'/workspaces/{self.workspace_id}',f'/channels/{self.room}/study-room',f'/channels/{self.chat}/messages',f'/channels/{self.chat}/learning-documents',f'/channels/{self.chat}/ai/conversations']:
                r=await client.get(path,headers=self.headers(self.member));self.assertIn(r.status_code,[403,404],path+r.text)
            r=await client.patch(target+'/state',json=body,headers=self.headers(self.owner));self.assertEqual(r.status_code,409)
            r=await client.patch(target+'/state',json=body|{'version':2,'enabled':True},headers=self.headers(self.owner));self.assertEqual(r.status_code,200,r.text)
            r=await client.get(f'/workspaces/{self.workspace_id}',headers=self.headers(self.member));self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()['data']['role'],'MEMBER')

    async def decision(self,identity,outcome='VIOLATION',action='HIDE'):
        async with self.factory() as db:
            return await AdministrationService(db).resolve(self.owner,identity,ReportDecision(version=1,outcome=outcome,action=action,reason='UC44 real PostgreSQL decision'))

    async def test_uc44_concurrent_resolution_and_hidden_reads(self):
        message,doc=await self.targets();m=await self.report('MESSAGE',message);d=await self.report('DOCUMENT',doc)
        await self.promote()
        result=await asyncio.gather(self.decision(m['id']),self.decision(m['id']),return_exceptions=True)
        self.assertEqual(sum(isinstance(x,dict) for x in result),1)
        self.assertEqual([x.status_code for x in result if isinstance(x,AppError)],[409])
        await self.decision(d['id'])
        async with self.client() as client:
            r=await client.get(f'/channels/{self.chat}/messages',headers=self.headers(self.member));self.assertEqual(r.status_code,200,r.text)
            self.assertNotIn(str(message),r.text)
            r=await client.get(f'/learning-documents/{doc}',headers=self.headers(self.member));self.assertEqual(r.status_code,404,r.text)
            r=await client.get('/admin/reports/'+str(d['id']),headers=self.headers(self.owner));self.assertEqual(r.status_code,200,r.text)
            self.assertFalse(r.json()['data']['target_available']);self.assertEqual(r.json()['data']['target']['name'],'test')
            r=await client.get('/admin/reports/'+str(d['id']),headers=self.headers(self.member));self.assertEqual(r.status_code,403)

    async def test_uc44_no_violation_missing_and_atomic_failure(self):
        message,doc=await self.targets();m=await self.report('MESSAGE',message);await self.promote()
        await self.decision(m['id'],'NO_VIOLATION','NONE')
        async with self.factory() as db:
            self.assertIsNotNone(await db.get(ChatMessage,message,populate_existing=True))
        report=await self.report('DOCUMENT',doc)
        from sqlalchemy import text
        constraint='uc44_fault_'+uuid4().hex
        async with self.factory() as db,db.begin():
            await db.execute(text(f"ALTER TABLE administration_audit ADD CONSTRAINT {constraint} CHECK(target_id<>'{report['id']}'::uuid)"))
        try:
            from sqlalchemy.exc import IntegrityError
            with self.assertRaises(IntegrityError):await self.decision(report['id'])
            async with self.factory() as db:
                target=await db.get(LearningDocument,doc,populate_existing=True);self.assertIsNone(target.moderated_at)
                self.assertEqual((await db.get(ViolationReport,report['id'])).status,'PENDING')
        finally:
            async with self.factory() as db,db.begin():await db.execute(text(f'ALTER TABLE administration_audit DROP CONSTRAINT {constraint}'))
        async with self.factory() as db,db.begin():
            target=await db.get(LearningDocument,doc);target.deleted_at=await db.scalar(select(__import__('sqlalchemy').func.now()))
        await self.decision(report['id'],'VIOLATION','ALREADY_UNAVAILABLE')

    async def test_uc45_dashboard_ledger_reconciliation_and_security(self):
        from tests import test_learning_analytics as analytics
        from app.services.administration_analytics import AdministrationAnalytics
        from datetime import date
        await analytics.AnalyticsTests.history(self,[('2026-01-02T10:00:00+07:00','2026-01-02T10:02:00+07:00')])
        await analytics.AnalyticsTests.history(self,[('2026-01-02T10:01:00+07:00','2026-01-02T10:03:00+07:00')],room=self.other_room)
        await analytics.AnalyticsTests.history(self,[('2026-01-02T10:00:00+07:00','2026-01-02T10:01:00+07:00')],principal=self.member)
        await self.promote()
        async with self.factory() as db:
            data=await AdministrationAnalytics(db).overview(self.owner,date(2026,1,2),date(2026,1,2))
        self.assertEqual(data['study']['duration_us'],240_000_000)
        await analytics.AnalyticsTests.history(self,[('2026-01-02T23:59:30+07:00','2026-01-03T00:00:30+07:00')])
        await analytics.AnalyticsTests.history(self,[('2026-01-02T10:00:00+07:00','2026-01-02T10:01:00+07:00')],tracked=False)
        async with self.factory() as db:
            split=await AdministrationAnalytics(db).overview(self.owner,date(2026,1,2),date(2026,1,3))
        self.assertEqual([d['duration_us'] for d in split['days']],[270_000_000,30_000_000])
        self.assertEqual(split['study']['excluded_sessions'],1)
        await analytics.AnalyticsTests.history(self,[('2026-03-08T23:30:00+07:00','2026-03-09T00:30:00+07:00')])
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy import text
        async with self.engine.connect() as connection:
            await connection.execute(text("SET TIME ZONE 'America/New_York'"));await connection.commit()
            async with AsyncSession(bind=connection,expire_on_commit=False) as session:
                dst=await AdministrationAnalytics(session).overview(self.owner,date(2026,3,8),date(2026,3,9))
        self.assertEqual([d['duration_us'] for d in dst['days']],[1_800_000_000,1_800_000_000])
        async with self.client() as client:
            for p in [self.member,self.outsider]:self.assertEqual((await client.get('/admin/dashboard',headers=self.headers(p))).status_code,403)
            for group in ['users','workspaces','documents','study','reports']:
                r=await client.get('/admin/dashboard/details?group='+group,headers=self.headers(self.owner));self.assertEqual(r.status_code,200,r.text)
            for query in ['start_date=bad','start_date=2026-03-01&end_date=2026-02-01','start_date=2020-01-01&end_date=2026-01-01']:
                self.assertEqual((await client.get('/admin/dashboard?'+query,headers=self.headers(self.owner))).status_code,422)
            r=await client.get('/admin/dashboard?start_date=2020-01-01&end_date=2020-01-07',headers=self.headers(self.owner));self.assertEqual(r.status_code,200,r.text)
            d=r.json()['data'];self.assertEqual(d['study']['duration_us'],0);self.assertTrue(all(x['created_in_period']==0 for x in [d['users'],d['workspaces'],d['documents'],d['reports']]))

    async def test_concurrent_admin_user_and_workspace_state(self):
        from app.schemas.administration import StateChange
        await self.promote()
        async with self.factory() as db,db.begin():
            row=await db.get(User,self.outsider.user.id);row.system_role=SystemRole.ADMIN
        request=StateChange(version=1,enabled=False,reason='Concurrent real administrator test')
        async def change(principal,workspace=False):
            async with self.factory() as db:
                service=AdministrationService(db)
                return await (service.workspace_state(principal,self.workspace_id,request) if workspace else service.user_state(principal,self.member.user.id,request))
        for workspace in [False,True]:
            result=await asyncio.gather(change(self.owner,workspace),change(self.outsider,workspace),return_exceptions=True)
            self.assertEqual(sum(isinstance(r,dict) for r in result),1)
            self.assertEqual([r.status_code for r in result if isinstance(r,AppError)],[409])

    async def test_postgresql_constraints_and_report_target_retention(self):
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError
        message,doc=await self.targets();report=await self.report('MESSAGE',message);await self.promote()
        for sql,params in [
            ('UPDATE users SET admin_version=0 WHERE id=:id',{'id':self.member.user.id}),
            ('UPDATE workspaces SET admin_version=0 WHERE id=:id',{'id':self.workspace_id}),
            ("UPDATE violation_reports SET status='RESOLVED',outcome=NULL,action='NONE',resolution='invalid test',resolved_at=clock_timestamp() WHERE id=:id",{'id':report['id']})]:
            with self.assertRaises(IntegrityError):
                async with self.factory() as db,db.begin():await db.execute(text(sql),params)
        await self.decision(report['id'],'NO_VIOLATION','NONE')
        async with self.factory() as db,db.begin():await db.execute(delete(ChatMessage).where(ChatMessage.id==message))
        async with self.factory() as db:
            detail=await AdministrationService(db).report_detail(self.owner,report['id'])
        self.assertEqual(detail['status'],'RESOLVED');self.assertEqual(detail['target_id'],message)
        self.assertIsNone(detail['target'])

    async def test_socket_reauthorization_preserves_refresh_close_code(self):
        import jwt
        from app.core.config import get_settings
        from app.core.security import create_access_token
        from app.realtime.moderation import register,permitted,connections
        token=create_access_token(self.owner.user.id,self.owner.session.id)
        payload=jwt.decode(token,options={'verify_signature':False});payload['exp']=1
        expired=jwt.encode(payload,get_settings().jwt_secret_key.get_secret_value(),algorithm='HS256')
        class Socket:
            code=None
            async def close(self,code):self.code=code
        socket=Socket();register(socket,expired,self.chat,self.owner.user.id,factory=self.factory)
        try:
            self.assertFalse(await permitted(socket));self.assertEqual(socket.code,4401)
        finally:connections.pop(socket,None)
