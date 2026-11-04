"""UC37 on real PostgreSQL, independent committed connections for races; no success mocks."""
import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from app.models import User, Workspace, WorkspaceMember, Channel, AuthSession
from app.models.enums import ChannelType, WorkspaceRole
from app.models.study_session import StudySession, StudySessionConnection
from app.services.auth_service import Principal
from app.services.study_session_service import StudySessionService, microseconds
from app.core.exceptions import AppError
from tests import test_chat_realtime as fixtures
from tests.test_channel_chat_hardening import HttpAssertions

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule


class StudySessionTests(HttpAssertions, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.extra_workspace_ids = []
        self.engine = create_async_engine(fixtures._test_url, poolclass=NullPool)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.factory() as db, db.begin():
            users = [User(full_name='UC37 test', email=f'uc37-{uuid4()}@example.com', password_hash='unused') for _ in range(3)]
            workspace = Workspace(name='UC37-' + uuid4().hex)
            db.add_all([workspace, *users]); await db.flush()
            self.workspace_id = workspace.id
            rooms = [Channel(workspace_id=workspace.id, name=f'room-{i}', type=ChannelType.STUDY_ROOM) for i in range(2)]
            chat = Channel(workspace_id=workspace.id, name='chat', type=ChannelType.TEXT)
            sessions = [AuthSession(user_id=u.id, refresh_token_hash=uuid4().hex + uuid4().hex,
                        expires_at=datetime.now(UTC) + timedelta(hours=1)) for u in users]
            db.add_all([*rooms, chat, *sessions, *[WorkspaceMember(workspace_id=workspace.id, user_id=u.id,
                role=WorkspaceRole.OWNER if i == 0 else WorkspaceRole.MEMBER) for i, u in enumerate(users[:2])]])
            await db.flush()
            self.user_ids = [u.id for u in users]
            self.room, self.other_room, self.chat = rooms[0].id, rooms[1].id, chat.id
            self.principals = [Principal(user=SimpleNamespace(id=u.id), session=SimpleNamespace(id=s.id)) for u, s in zip(users, sessions)]
        self.owner, self.member, self.outsider = self.principals
        self.session = self.factory()

    async def asyncTearDown(self):
        await self.session.close()
        async with self.factory() as db, db.begin():
            workspace_ids = [self.workspace_id, *self.extra_workspace_ids]
            await db.execute(delete(WorkspaceMember).where(WorkspaceMember.workspace_id.in_(workspace_ids)))
            await db.execute(delete(Workspace).where(Workspace.id.in_(workspace_ids)))
            await db.execute(delete(AuthSession).where(AuthSession.user_id.in_(self.user_ids)))
            await db.execute(delete(User).where(User.id.in_(self.user_ids)))
        await self.engine.dispose()

    async def event(self, connection, action, principal=None, room=None):
        async with self.factory() as db:
            return await StudySessionService(db).event(principal or self.owner, room or self.room, connection, action)

    async def rows(self):
        async with self.factory() as db:
            return list(await db.scalars(select(StudySession).where(StudySession.channel_id.in_([self.room, self.other_room])).order_by(StudySession.started_at)))

    async def disconnect(self, connection):
        async with self.factory() as db:
            await StudySessionService(db).disconnect(connection)

    async def recover(self):
        async with self.factory() as db:
            await StudySessionService(db).recover()

    async def age(self, seconds=60):
        # Fault setup in this test's own committed rows; real timeout elapsed is tested in Chrome.
        async with self.factory() as db, db.begin():
            await db.execute(text('UPDATE study_session_connections SET last_seen_at=last_seen_at-make_interval(secs=>:age), '
                'lease_expires_at=lease_expires_at-make_interval(secs=>:age) WHERE session_id IN '
                '(SELECT id FROM study_sessions WHERE channel_id=:room)'), {'age': seconds, 'room': self.room})
            await db.execute(text('UPDATE study_sessions SET started_at=started_at-make_interval(secs=>:age), '
                'last_activity_at=last_activity_at-make_interval(secs=>:age), grace_expires_at=grace_expires_at-make_interval(secs=>:age) '
                'WHERE channel_id=:room'), {'age': seconds, 'room': self.room})

    async def test_observer_unauthorized_and_text_room_never_create_sessions(self):
        self.assertIsNone(await self.event(uuid4(), 'state'))
        for p, room in ((self.outsider, self.room), (self.member, self.chat)):
            with self.assertRaises(AppError): await self.event(uuid4(), 'join', p, room)
        self.assertEqual(await self.rows(), [])

    async def test_join_heartbeat_leave_idempotent_exact_server_duration(self):
        key = uuid4(); first = await self.event(key, 'join')
        self.assertEqual((await self.event(key, 'join'))['id'], first['id'])
        await asyncio.sleep(.02); await self.event(key, 'state')
        ended = await self.event(key, 'leave')
        self.assertIsNone(await self.event(key, 'leave'))
        self.assertEqual(ended['status'], 'COMPLETED'); self.assertEqual(ended['end_reason'], 'LEFT')
        self.assertEqual(ended['duration_us'], microseconds(ended['ended_at'] - ended['started_at']))
        self.assertEqual(ended['ended_at'], ended['last_activity_at'])
        self.assertEqual(len(await self.rows()), 1)
        new = await self.event(key, 'join'); self.assertNotEqual(new['id'], first['id'])

    async def test_short_reconnect_keeps_id_and_excludes_offline_gap(self):
        key, new_key = uuid4(), uuid4(); first = await self.event(key, 'join')
        last = await self.event(key, 'state'); await self.disconnect(key)
        self.assertEqual((await self.rows())[0].status, 'DISCONNECTED')
        await asyncio.sleep(.03)
        resumed = await self.event(new_key, 'join')
        self.assertEqual(resumed['id'], first['id']); self.assertEqual(resumed['duration_us'], last['duration_us'])
        final = await self.event(new_key, 'leave')
        gap = resumed['last_activity_at'] - last['last_activity_at']
        self.assertEqual(final['duration_us'], microseconds(final['ended_at'] - first['started_at'] - gap))

    async def test_multi_tab_disconnect_and_leave_do_not_close_other_tab(self):
        a, b = uuid4(), uuid4(); first = await self.event(a, 'join'); await self.event(b, 'join')
        await self.event(a, 'leave'); await self.disconnect(a)
        self.assertEqual((await self.rows())[0].status, 'ACTIVE')
        end = await self.event(b, 'leave'); self.assertEqual(end['id'], first['id']); self.assertEqual(end['status'], 'COMPLETED')

    async def test_concurrent_join_and_leave_use_one_logical_session(self):
        keys = [uuid4() for _ in range(8)]
        joined = await asyncio.gather(*(self.event(k, 'join') for k in keys))
        self.assertEqual(len({r['id'] for r in joined}), 1)
        await asyncio.gather(*(self.event(k, 'leave') for k in keys))
        rows = await self.rows(); self.assertEqual(len(rows), 1); self.assertEqual(rows[0].status, 'COMPLETED')

    async def test_timeout_and_restart_recovery_end_at_last_activity(self):
        key = uuid4(); await self.event(key, 'join'); await self.event(key, 'state')
        await self.age(); before = (await self.rows())[0]
        # A NEW service/session (no original process/presence memory) performs recovery.
        await self.recover(); ended = (await self.rows())[0]
        self.assertEqual(ended.status, 'COMPLETED'); self.assertEqual(ended.end_reason, 'DISCONNECT_TIMEOUT')
        self.assertEqual(ended.ended_at, before.last_activity_at); self.assertEqual(ended.duration_us, before.duration_us)
        await self.recover(); self.assertEqual((await self.rows())[0].duration_us, before.duration_us)
        self.assertNotEqual((await self.event(uuid4(), 'join'))['id'], ended.id)

    async def test_startup_reconnect_does_not_credit_downtime_or_extend_old_grace(self):
        first = await self.event(uuid4(), 'join')
        async with self.factory() as db:
            await StudySessionService(db).recover_after_restart()
        pending = (await self.rows())[0]; self.assertEqual(pending.status, 'DISCONNECTED')
        async with self.factory() as db:
            await StudySessionService(db).recover_after_restart()
        self.assertEqual((await self.rows())[0].grace_expires_at, pending.grace_expires_at)
        resumed = await self.event(uuid4(), 'join')
        self.assertEqual(resumed['id'], first['id']); self.assertEqual(resumed['duration_us'], first['duration_us'])

    async def test_timeout_reconnect_race_and_stale_socket_cannot_close_new_session(self):
        old = uuid4(); first = await self.event(old, 'join'); await self.age()
        new = uuid4(); _, joined = await asyncio.gather(self.recover(), self.event(new, 'join'))
        self.assertNotEqual(joined['id'], first['id'])
        self.assertIsNone(await self.event(old, 'leave'))
        rows = await self.rows(); self.assertEqual([r.status for r in rows], ['COMPLETED', 'ACTIVE'])

    async def test_same_socket_expired_rejoin_flushes_old_connection(self):
        key = uuid4(); first = await self.event(key, 'join'); await self.age()
        joined = await self.event(key, 'join'); self.assertNotEqual(joined['id'], first['id'])
        self.assertEqual((await self.event(key, 'leave'))['status'], 'COMPLETED')

    async def test_invalid_clock_is_marked_and_excluded(self):
        key = uuid4(); await self.event(key, 'join')
        async with self.factory() as db, db.begin():
            await db.execute(text("UPDATE study_sessions SET started_at=clock_timestamp()+interval '5 minutes', "
                "last_activity_at=clock_timestamp()+interval '6 minutes' WHERE channel_id=:room"), {'room': self.room})
        self.assertIsNone(await self.event(key, 'state'))
        invalid = (await self.rows())[0]; self.assertEqual(invalid.status, 'INVALID'); self.assertEqual(invalid.duration_us, 0)
        async with self.factory() as db:
            self.assertEqual(list(await db.scalars(StudySessionService.valid_completed_query(self.owner.user.id, self.room))), [])

    async def test_postgres_constraints_and_open_uniqueness(self):
        first = await self.event(uuid4(), 'join')
        for sql in ("UPDATE study_sessions SET duration_us=-1 WHERE id=:id",
                    "UPDATE study_sessions SET status='COMPLETED', ended_at=started_at-interval '1 second', end_reason='LEFT' WHERE id=:id",
                    "UPDATE study_sessions SET status='COMPLETED', ended_at=last_activity_at, end_reason='LEFT', duration_us=999999999999 WHERE id=:id",
                    "UPDATE study_sessions SET status='DISCONNECTED' WHERE id=:id"):
            async with self.factory() as db:
                with self.assertRaises(IntegrityError):
                    async with db.begin(): await db.execute(text(sql), {'id': first['id']})
        async with self.factory() as db:
            with self.assertRaises(IntegrityError):
                async with db.begin():
                    db.add(StudySession(user_id=self.owner.user.id, channel_id=self.room, status='ACTIVE',
                        started_at=datetime.now(UTC), last_activity_at=datetime.now(UTC), duration_us=0)); await db.flush()
        async with self.factory() as db:
            with self.assertRaises(IntegrityError):
                async with db.begin():
                    db.add(StudySessionConnection(id=uuid4(), session_id=uuid4(), last_seen_at=datetime.now(UTC),
                        lease_expires_at=datetime.now(UTC)+timedelta(seconds=15))); await db.flush()

    async def test_read_api_auth_and_user_room_isolation(self):
        own = await self.event(uuid4(), 'join')
        await self.event(uuid4(), 'join', self.member); await self.event(uuid4(), 'join', room=self.other_room)
        async with self.client() as client:
            path = f'channels/{self.room}/study-room/study-sessions'
            response = await client.get(path + f'?user_id={self.member.user.id}', headers=self.headers(self.owner))
            self.assertEqual(response.status_code, 200)
            self.assertEqual([r['id'] for r in response.json()['data']['items']], [str(own['id'])])
            self.assertEqual(response.headers['cache-control'], 'private, no-store')
            self.assertEqual((await client.get(path)).status_code, 401)
            self.assertEqual((await client.get(path, headers=self.headers(self.outsider))).status_code, 403)
            self.assertEqual((await client.get(f'channels/{self.chat}/study-room/study-sessions', headers=self.headers(self.owner))).status_code, 422)
            self.assertEqual((await client.post(path, headers=self.headers(self.owner), json={'user_id':str(self.member.user.id)})).status_code, 405)

    async def test_revoked_auth_cannot_renew_but_server_disconnect_can_cleanup(self):
        key = uuid4(); await self.event(key, 'join')
        async with self.factory() as db, db.begin():
            await db.execute(text('UPDATE auth_sessions SET revoked_at=clock_timestamp() WHERE id=:id'), {'id': self.owner.session.id})
        with self.assertRaises(AppError): await self.event(key, 'state')
        await self.disconnect(key); self.assertEqual((await self.rows())[0].status, 'DISCONNECTED')

    async def test_cross_workspace_attendance_is_isolated(self):
        async with self.factory() as db, db.begin():
            workspace = Workspace(name='UC37-other-' + uuid4().hex)
            db.add(workspace); await db.flush(); self.extra_workspace_ids.append(workspace.id)
            channel = Channel(workspace_id=workspace.id, name='private-room', type=ChannelType.STUDY_ROOM)
            db.add_all([channel, WorkspaceMember(workspace_id=workspace.id,
                user_id=self.outsider.user.id, role=WorkspaceRole.OWNER)])
            await db.flush(); private_room = channel.id
        await self.event(uuid4(), 'join', self.outsider, private_room)
        with self.assertRaises(AppError) as denied:
            await self.event(uuid4(), 'join', room=private_room)
        self.assertEqual(denied.exception.status_code, 403)
        async with self.client() as client:
            response = await client.get(f'channels/{private_room}/study-room/study-sessions', headers=self.headers(self.owner))
            self.assertEqual(response.status_code, 403)
            own = await client.get(f'channels/{self.room}/study-room/study-sessions', headers=self.headers(self.owner))
            self.assertEqual(own.json()['data']['items'], [])

    async def test_real_db_leave_failure_retains_recoverable_session(self):
        key = uuid4(); session = await self.event(key, 'join')
        constraint = 'ck_uc37_fault_' + uuid4().hex
        async with self.factory() as db, db.begin():
            await db.execute(text(f"ALTER TABLE study_sessions ADD CONSTRAINT {constraint} CHECK (id<>'{session['id']}' OR status<>'COMPLETED')"))
        try:
            with self.assertRaises(IntegrityError): await self.event(key, 'leave')
            self.assertEqual((await self.rows())[0].status, 'ACTIVE')
        finally:
            async with self.factory() as db, db.begin():
                await db.execute(text(f'ALTER TABLE study_sessions DROP CONSTRAINT {constraint}'))
        await self.disconnect(key); await self.age(); await self.recover()
        self.assertEqual((await self.rows())[0].status, 'COMPLETED')
