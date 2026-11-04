import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from sqlalchemy import select
from app.core.exceptions import AppError
from app.models.enums import ChannelType
from app.models.pomodoro import PomodoroState, PomodoroSession, PomodoroCompletion
from app.models.learning_activity import PomodoroFocusInterval, PomodoroFocusSegment
from app.realtime.study_room import room_presence, publish_pomodoro
from app.services.channel_service import ChannelService
from app.core.pomodoro import DEFAULT_FOCUS_SECONDS, DEFAULT_BREAK_SECONDS


def utcnow():
    return datetime.now(UTC)


class PomodoroService(ChannelService):
    async def focus_start(self, state, now):
        focus = PomodoroFocusInterval(session_id=state.session_id,
            interval=state.completed_focus + 1, started_at=now, status='RUNNING')
        self.db.add(focus)
        await self.db.flush()
        self.db.add(PomodoroFocusSegment(focus_id=focus.id, started_at=now))

    async def focus_change(self, state, now, action):
        if state.phase != 'focus':
            return
        focus = await self.db.scalar(select(PomodoroFocusInterval).where(
            PomodoroFocusInterval.session_id == state.session_id,
            PomodoroFocusInterval.interval == state.completed_focus + 1))
        # A pre-migration running cycle has no exact ledger: do not reconstruct it.
        if focus is None:
            return
        if action == 'resume':
            self.db.add(PomodoroFocusSegment(focus_id=focus.id, started_at=now))
        else:
            segment = await self.db.scalar(select(PomodoroFocusSegment).where(
                PomodoroFocusSegment.focus_id == focus.id, PomodoroFocusSegment.ended_at.is_(None)))
            if segment:
                segment.ended_at = now
            if action in ('COMPLETED', 'ENDED'):
                focus.status, focus.ended_at = action, now
        await self.db.flush()

    async def state_row(self, channel_id):
        # All paths lock Channel before the cursor, including initial creation.
        state = await self.db.get(PomodoroState, channel_id, populate_existing=True, with_for_update=True)
        if state is None:
            state = PomodoroState(channel_id=channel_id, focus_seconds=DEFAULT_FOCUS_SECONDS,
                                  break_seconds=DEFAULT_BREAK_SECONDS)
            self.db.add(state)
            await self.db.flush()
        return state

    async def advance(self, state, now):
        while state.status == "running" and state.deadline <= now:
            boundary = state.deadline
            if state.phase == "focus":
                await self.focus_change(state, boundary, 'COMPLETED')
                state.completed_focus += 1
                self.db.add(PomodoroCompletion(session_id=state.session_id,
                    interval=state.completed_focus, completed_at=boundary, focus_seconds=state.focus_seconds))
                state.phase = "break"
                duration = state.break_seconds
            else:
                state.phase = "focus"
                duration = state.focus_seconds
                await self.focus_start(state, boundary)
            state.phase_started_at = boundary
            state.deadline = boundary + timedelta(seconds=duration)
            state.revision += 1
        await self.db.flush()

    @staticmethod
    def snapshot(state, now, can_control):
        fields = ("channel_id", "revision", "status", "phase", "focus_seconds", "break_seconds",
                  "session_id", "session_started_at", "phase_started_at", "deadline",
                  "remaining_seconds", "completed_focus")
        result = {field: getattr(state, field) for field in fields}
        for field, value in result.items():
            if isinstance(value, datetime): result[field] = value.isoformat()
            elif field in ("channel_id", "session_id") and value is not None: result[field] = str(value)
        return {**result, "server_now": now.isoformat(), "can_control": can_control}

    async def current(self, principal, channel_id, request=None):
        conflict = None
        async with self.transaction(principal):
            channel = await self.channel_access(channel_id, principal.user.id)
            if channel.type != ChannelType.STUDY_ROOM:
                raise AppError(422, "CHANNEL_NOT_STUDY_ROOM", "Channel không phải phòng tự học")
            member = await self.members.get_membership(channel.workspace_id, principal.user.id)
            can_control = member.role in self.MANAGERS
            state = await self.state_row(channel_id)
            previous_revision = state.revision
            now = utcnow()
            await self.advance(state, now)
            if request:
                participating = any(p["user_id"] == str(principal.user.id)
                                    for p in room_presence.snapshot(channel_id)["participants"])
                if not can_control:
                    conflict = AppError(403, "POMODORO_PERMISSION_DENIED", "Chỉ chủ sở hữu hoặc quản trị viên Workspace được điều khiển.")
                elif not participating:
                    conflict = AppError(403, "ROOM_PARTICIPATION_REQUIRED", "Hãy tham gia phòng trước khi điều khiển.")
                elif request.revision != state.revision:
                    conflict = AppError(409, "POMODORO_STALE", "Pomodoro đã thay đổi. Đã tải trạng thái mới nhất.")
                elif request.action == "start" and state.status not in ("idle", "ended"):
                    conflict = AppError(409, "POMODORO_INVALID_STATE", "Phiên Pomodoro đang hoạt động.")
                elif request.action == "pause" and state.status != "running" or request.action == "resume" and state.status != "paused" or request.action == "end" and state.status not in ("running", "paused"):
                    conflict = AppError(409, "POMODORO_INVALID_STATE", "Thao tác không phù hợp trạng thái hiện tại.")
                else:
                    if request.action == "start":
                        state.session_id = uuid4()
                        state.session_started_at = state.phase_started_at = now
                        state.focus_seconds, state.break_seconds = request.focus_seconds, request.break_seconds
                        state.completed_focus = 0
                        state.status, state.phase = "running", "focus"
                        state.deadline = now + timedelta(seconds=state.focus_seconds)
                        state.remaining_seconds = None
                        self.db.add(PomodoroSession(id=state.session_id, channel_id=channel_id,
                            started_by=principal.user.id, started_at=now))
                        await self.db.flush()
                        await self.focus_start(state, now)
                    elif request.action == "pause":
                        await self.focus_change(state, now, 'pause')
                        state.remaining_seconds = (state.deadline - now).total_seconds()
                        state.status, state.deadline = "paused", None
                    elif request.action == "resume":
                        await self.focus_change(state, now, 'resume')
                        state.deadline = now + timedelta(seconds=state.remaining_seconds)
                        state.status, state.remaining_seconds = "running", None
                    else:
                        await self.focus_change(state, now, 'ENDED')
                        state.status, state.deadline, state.remaining_seconds = "ended", None, None
                        session = await self.db.get(PomodoroSession, state.session_id)
                        session.ended_at = now
                    state.revision += 1
            result = self.snapshot(state, now, can_control)
        # Even rejected stale requests commit elapsed natural transitions.
        if result["revision"] != previous_revision:
            await publish_pomodoro(channel_id, result)
        if conflict: raise conflict
        return result


async def transition_worker():
    from app.db.session import session_factory
    from app.models.channel import Channel
    while True:
        try:
            async with session_factory() as db:
                async with db.begin():
                    ids = list((await db.scalars(select(PomodoroState.channel_id).where(
                        PomodoroState.status == "running", PomodoroState.deadline <= utcnow()))).all())
                for channel_id in ids:
                    snapshot = None
                    async with db.begin():
                        channel = await db.get(Channel, channel_id, with_for_update=True)
                        if channel is None: continue
                        service = PomodoroService(db)
                        state = await service.state_row(channel_id)
                        revision = state.revision
                        await service.advance(state, utcnow())
                        if state.revision != revision:
                            snapshot = service.snapshot(state, utcnow(), False)
                    if snapshot:
                        await publish_pomodoro(channel_id, snapshot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).error("Pomodoro transition worker failed; retrying")
        await asyncio.sleep(0.5)
