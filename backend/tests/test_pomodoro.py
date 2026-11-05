import unittest
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy import select, func, update, delete
from sqlalchemy.ext.asyncio import async_sessionmaker
from uuid import uuid4
from pydantic import ValidationError
from app.models import WorkspaceMember, Channel, User, Workspace, AuthSession
from app.models.enums import WorkspaceRole, ChannelType
from app.models.pomodoro import PomodoroCompletion, PomodoroSession
from app.schemas.pomodoro import PomodoroMutation
from app.services.pomodoro_service import PomodoroService
from app.services.auth_service import Principal
from app.core.exceptions import AppError
from app.realtime.study_room import room_presence
from tests import test_chat_realtime as fixtures

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule


class PomodoroTests(unittest.IsolatedAsyncioTestCase):
    assert_app_error = fixtures.ChatRealtimeTests.assert_app_error
    async def asyncSetUp(self):
        await fixtures.ChatRealtimeTests.asyncSetUp(self)
        self.service = PomodoroService(self.session)
        self.now = datetime(2026, 10, 5, tzinfo=UTC)
        self.clock = patch("app.services.pomodoro_service.utcnow", lambda: self.now)
        self.clock.start()
        self.key = object()
        room_presence.update(self.key, self.study_id, SimpleNamespace(id=self.sender.user.id, full_name="Owner"), "join")

    async def asyncTearDown(self):
        self.clock.stop()
        room_presence.disconnect(self.key)
        await fixtures.ChatRealtimeTests.asyncTearDown(self)

    async def mutate(self, action, revision, **kwargs):
        return await self.service.current(self.sender, self.study_id, PomodoroMutation(action=action, revision=revision, **kwargs))

    async def count(self):
        async with self.session.begin():
            return await self.session.scalar(select(func.count()).select_from(PomodoroCompletion))

    async def test_idle_default_start_session(self):
        idle = await self.service.current(self.member, self.study_id)
        self.assertEqual((idle["status"], idle["revision"], idle["can_control"]), ("idle", 0, False))
        state = await self.mutate("start", 0)
        self.assertEqual((state["focus_seconds"], state["break_seconds"]), (1500, 300))
        async with self.session.begin():
            self.assertIsNotNone(await self.session.get(PomodoroSession, state["session_id"]))

    async def test_pause_resume_end_unfinished(self):
        state = await self.mutate("start", 0, focus_seconds=12, break_seconds=3)
        self.now += timedelta(seconds=2.25)
        state = await self.mutate("pause", state["revision"])
        self.assertEqual(state["remaining_seconds"], 9.75)
        self.now += timedelta(minutes=10)
        paused = await self.service.current(self.member, self.study_id)
        self.assertEqual(paused["remaining_seconds"], 9.75)
        state = await self.mutate("resume", state["revision"])
        self.assertEqual(datetime.fromisoformat(state["deadline"]), self.now + timedelta(seconds=9.75))
        state = await self.mutate("end", state["revision"])
        self.assertEqual(state["status"], "ended")
        self.assertEqual(await self.count(), 0)

    async def test_transitions_exactly_once_and_retry(self):
        await self.mutate("start", 0, focus_seconds=10, break_seconds=3)
        self.now += timedelta(seconds=10)
        state = await self.service.current(self.member, self.study_id)
        self.assertEqual((state["phase"], state["completed_focus"]), ("break", 1))
        await self.service.current(self.sender, self.study_id)
        self.assertEqual(await self.count(), 1)
        self.now += timedelta(seconds=3)
        state = await self.service.current(self.member, self.study_id)
        self.assertEqual((state["phase"], state["revision"]), ("focus", 3))
        self.now += timedelta(seconds=10)
        await self.service.current(self.sender, self.study_id)
        self.assertEqual(await self.count(), 2)

    async def test_stale_rejected_and_transition_committed(self):
        await self.mutate("start", 0, focus_seconds=2)
        await self.assert_app_error("POMODORO_STALE", self.mutate("pause", 0))
        self.now += timedelta(seconds=2)
        await self.assert_app_error("POMODORO_STALE", self.mutate("end", 1))
        self.assertEqual(await self.count(), 1)
        current = await self.service.current(self.sender, self.study_id)
        self.assertEqual((current["status"], current["phase"]), ("running", "break"))

    async def test_permission_loss_and_participation(self):
        request = PomodoroMutation(action="start", revision=0)
        await self.assert_app_error("POMODORO_PERMISSION_DENIED", self.service.current(self.member, self.study_id, request))
        room_presence.disconnect(self.key)
        await self.assert_app_error("ROOM_PARTICIPATION_REQUIRED", self.mutate("start", 0))
        room_presence.update(self.key, self.study_id, SimpleNamespace(id=self.sender.user.id, full_name="Owner"), "join")
        state = await self.mutate("start", 0)
        async with self.session.begin():
            await self.session.execute(update(WorkspaceMember).where(WorkspaceMember.user_id == self.sender.user.id).values(role=WorkspaceRole.MEMBER))
        await self.assert_app_error("POMODORO_PERMISSION_DENIED", self.mutate("pause", state["revision"]))
        self.assertFalse((await self.service.current(self.sender, self.study_id))["can_control"])

    async def test_isolation_and_non_room(self):
        await self.mutate("start", 0)
        await self.assert_app_error("CHANNEL_PERMISSION_DENIED", self.service.current(self.outsider, self.study_id))
        await self.assert_app_error("CHANNEL_NOT_STUDY_ROOM", self.service.current(self.sender, self.channel_id))
        async with self.session.begin():
            original = await self.session.get(Channel, self.study_id)
            second = Channel(workspace_id=original.workspace_id, name="second", type=ChannelType.STUDY_ROOM)
            self.session.add(second)
            await self.session.flush()
            second_id = second.id
        self.assertEqual((await self.service.current(self.sender, second_id))["status"], "idle")

    async def test_invalid_states(self):
        await self.assert_app_error("POMODORO_INVALID_STATE", self.mutate("pause", 0))
        await self.assert_app_error("POMODORO_INVALID_STATE", self.mutate("resume", 0))
        await self.assert_app_error("POMODORO_INVALID_STATE", self.mutate("end", 0))
        await self.mutate("start", 0)
        await self.assert_app_error("POMODORO_INVALID_STATE", self.mutate("start", 1))

    async def test_restart_catchup_without_browser(self):
        await self.mutate("start", 0, focus_seconds=4, break_seconds=2)
        room_presence.disconnect(self.key)
        self.now += timedelta(seconds=18)
        state = await PomodoroService(self.session).current(self.sender, self.study_id)
        self.assertEqual((state["phase"], state["completed_focus"]), ("focus", 3))
        self.assertEqual(await self.count(), 3)

    async def test_independent_connections_concurrent_start_and_completion(self):
        factory = async_sessionmaker(self.engine, expire_on_commit=False)
        users, sessions, principals, keys = [], [], [], []
        workspace_id = channel_id = None
        try:
            async with factory() as db, db.begin():
                users = [User(email=f"{uuid4()}@example.com", full_name="Concurrent controller", password_hash="unused") for _ in range(2)]
                workspace = Workspace(name=f"pomodoro-{uuid4()}")
                db.add_all([*users, workspace])
                await db.flush()
                workspace_id = workspace.id
                sessions = [AuthSession(user_id=user.id, refresh_token_hash=uuid4().hex + uuid4().hex,
                    expires_at=datetime.now(UTC) + timedelta(hours=1)) for user in users]
                channel = Channel(workspace_id=workspace.id, name="study", type=ChannelType.STUDY_ROOM)
                db.add_all([*sessions, channel, *(WorkspaceMember(workspace_id=workspace.id, user_id=user.id,
                    role=role) for user, role in zip(users, (WorkspaceRole.OWNER, WorkspaceRole.ADMIN)))])
                await db.flush()
                channel_id = channel.id
                principals = [Principal(user=SimpleNamespace(id=user.id), session=SimpleNamespace(id=session.id))
                              for user, session in zip(users, sessions)]
            for user in users:
                key = object(); keys.append(key)
                room_presence.update(key, channel_id, user, "join")

            async def act(principal, request=None):
                async with factory() as db:
                    try:
                        return await PomodoroService(db).current(principal, channel_id, request)
                    except AppError as error:
                        return error.code

            request = PomodoroMutation(action="start", revision=0, focus_seconds=2, break_seconds=3)
            results = await asyncio.gather(*(act(principal, request) for principal in principals))
            self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
            self.assertIn("POMODORO_STALE", results)
            self.now += timedelta(seconds=2)
            snapshots = await asyncio.gather(*(act(principal) for principal in principals))
            self.assertTrue(all(state["completed_focus"] == 1 and state["phase"] == "break" for state in snapshots))
            async with factory() as db, db.begin():
                self.assertEqual(await db.scalar(select(func.count()).select_from(PomodoroCompletion).join(
                    PomodoroSession).where(PomodoroSession.channel_id == channel_id)), 1)
            await self.assert_app_error("CHANNEL_PERMISSION_DENIED", self.service.current(self.sender, channel_id))
        finally:
            for key in keys: room_presence.disconnect(key)
            async with factory() as db, db.begin():
                if workspace_id:
                    await db.execute(delete(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id))
                    await db.execute(delete(Workspace).where(Workspace.id == workspace_id))
                ids = [user.id for user in users]
                await db.execute(delete(AuthSession).where(AuthSession.user_id.in_(ids)))
                await db.execute(delete(User).where(User.id.in_(ids)))

    def test_duration_bounds(self):
        for value in (0, -1, 14401, True, "12", 1.5, None):
            for field in ("focus_seconds", "break_seconds"):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    PomodoroMutation(action="start", revision=0, **{field: value})
