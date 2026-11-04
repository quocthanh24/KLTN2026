"""UC37: PostgreSQL-owned attendance, per-socket leases and offline-gap exclusion."""
import asyncio
import logging
from datetime import timedelta
from sqlalchemy import delete, func, or_, select, text
from app.core.exceptions import AppError
from app.models.enums import ChannelType
from app.models.study_session import StudySession, StudySessionConnection
from app.models.learning_activity import StudyActivityInterval
from app.services.channel_service import ChannelService

HEARTBEAT_TIMEOUT_SECONDS = 15
RECONNECT_GRACE_SECONDS = 30
OPEN_STATUSES = ('ACTIVE', 'DISCONNECTED')
logger = logging.getLogger(__name__)


def microseconds(delta):
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


class StudySessionService(ChannelService):
    async def _lock(self, user_id, channel_id):
        # Locks even when no open row exists. Always acquire pair lock BEFORE row locks.
        await self.db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))'),
                              {'scope': f'uc37:{user_id}:{channel_id}'})

    async def _now(self):
        return await self.db.scalar(select(func.clock_timestamp()))

    async def _open(self, user_id, channel_id):
        return await self.db.scalar(select(StudySession).where(
            StudySession.user_id == user_id, StudySession.channel_id == channel_id,
            StudySession.status.in_(OPEN_STATUSES)).with_for_update().execution_options(populate_existing=True))

    async def _connections(self, row):
        return list(await self.db.scalars(select(StudySessionConnection).where(
            StudySessionConnection.session_id == row.id).execution_options(populate_existing=True)))

    async def _finish(self, row, reason):
        row.status = 'INVALID' if reason == 'INVALID_TIME' else 'COMPLETED'
        row.ended_at = None if reason == 'INVALID_TIME' else row.last_activity_at
        row.end_reason = reason
        row.grace_expires_at = None
        if reason == 'INVALID_TIME':
            row.duration_us = 0
            logger.warning('Study session invalid time: session_id=%s', row.id)
        await self.db.execute(delete(StudySessionConnection).where(StudySessionConnection.session_id == row.id))
        logger.info('Study session closed: session_id=%s reason=%s duration_us=%s', row.id, reason, row.duration_us)

    async def _reconcile(self, row, now):
        if now < row.last_activity_at or now < row.started_at:
            await self._finish(row, 'INVALID_TIME')
            return []
        connections = await self._connections(row)
        live = []
        for connection in connections:
            if connection.lease_expires_at <= now:
                await self.db.delete(connection)
            else:
                live.append(connection)
        if not live and row.status == 'ACTIVE':
            # Recovery uses the actual lease boundary, NOT the time a worker woke up.
            detected_at = max((c.lease_expires_at for c in connections),
                              default=row.last_activity_at + timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS))
            row.status = 'DISCONNECTED'
            row.grace_expires_at = detected_at + timedelta(seconds=RECONNECT_GRACE_SECONDS)
        if row.status == 'DISCONNECTED' and row.grace_expires_at <= now:
            await self._finish(row, 'DISCONNECT_TIMEOUT')
        return live

    async def _credit(self, row, now):
        # Called only when a live, authorized server connection proves continuity.
        if row.analytics_tracked and now > row.last_activity_at:
            interval = await self.db.scalar(select(StudyActivityInterval).where(
                StudyActivityInterval.session_id == row.id,
                StudyActivityInterval.ended_at == row.last_activity_at))
            if interval:
                interval.ended_at = now
            else:
                self.db.add(StudyActivityInterval(session_id=row.id,
                    started_at=row.last_activity_at, ended_at=now))
        row.duration_us += microseconds(now - row.last_activity_at)
        row.last_activity_at = now

    async def event(self, principal, channel_id, connection_id, action):
        if action not in {'join', 'leave', 'state', 'devices'}:
            raise ValueError('Invalid server attendance event')
        async with self.transaction(principal):
            channel = await self.channel_access(channel_id, principal.user.id)
            if channel.type != ChannelType.STUDY_ROOM:
                raise AppError(422, 'CHANNEL_NOT_STUDY_ROOM', 'Channel không phải phòng tự học')
            await self._lock(principal.user.id, channel_id)
            now = await self._now()
            row = await self._open(principal.user.id, channel_id)
            live = await self._reconcile(row, now) if row else []
            await self.db.flush()
            if row and row.status not in OPEN_STATUSES:
                row = None
            connection = next((c for c in live if c.id == connection_id), None)
            if action == 'join':
                if row is None:
                    row = StudySession(user_id=principal.user.id, channel_id=channel_id,
                                       started_at=now, last_activity_at=now, duration_us=0, status='ACTIVE',
                                       analytics_tracked=True)
                    self.db.add(row)
                    await self.db.flush()
                elif row.status == 'ACTIVE':
                    await self._credit(row, now)
                else:
                    # Resume same session without crediting ANY disconnected gap.
                    row.status = 'ACTIVE'
                    row.grace_expires_at = None
                    row.last_activity_at = now
                if connection is None:
                    connection = StudySessionConnection(id=connection_id, session_id=row.id)
                    self.db.add(connection)
                connection.last_seen_at = now
                connection.lease_expires_at = now + timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS)
            elif connection is not None:
                await self._credit(row, now)
                if action == 'leave':
                    await self.db.delete(connection)
                    if len(live) == 1:
                        await self._finish(row, 'LEFT')
                else:
                    connection.last_seen_at = now
                    connection.lease_expires_at = now + timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS)
            else:
                # Observer, duplicate leave or an old/expired socket cannot renew a session.
                await self.db.flush()
                return None
            await self.db.flush()
            return self.public(row)

    async def disconnect(self, connection_id):
        # Trusted server-generated socket ID only. Must work after auth/membership revocation.
        async with self.db.begin():
            pair = (await self.db.execute(select(StudySession.user_id, StudySession.channel_id)
                .join(StudySessionConnection, StudySessionConnection.session_id == StudySession.id)
                .where(StudySessionConnection.id == connection_id))).first()
            if pair is None:
                return
            await self._lock(*pair)
            row = await self._open(*pair)
            if row is None:
                return
            now = await self._now()
            live = await self._reconcile(row, now)
            connection = next((c for c in live if c.id == connection_id), None)
            if connection is None:
                return
            await self.db.delete(connection)
            if len(live) == 1:
                row.status = 'DISCONNECTED'
                row.grace_expires_at = now + timedelta(seconds=RECONNECT_GRACE_SECONDS)
            # No _credit here: disconnect detection is not evidence of learning activity.

    async def recover(self, limit=100):
        async with self.db.begin():
            now = await self._now()
            pairs = (await self.db.execute(select(StudySession.user_id, StudySession.channel_id).where(
                StudySession.status.in_(OPEN_STATUSES), or_(
                    StudySession.last_activity_at <= now - timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS),
                    StudySession.last_activity_at > now,
                    StudySession.grace_expires_at <= now)).order_by(StudySession.last_activity_at).limit(limit))).all()
        for pair in pairs:
            async with self.db.begin():
                await self._lock(*pair)
                row = await self._open(*pair)
                if row:
                    await self._reconcile(row, await self._now())
        return len(pairs)

    async def recover_after_restart(self):
        """Before accepting sockets: prior single-worker connections cannot still be live.

        The existing Study Room presence architecture supports one backend worker.
        Do not use this startup boundary with multiple workers sharing one presence DB.
        """
        async with self.db.begin():
            pairs = (await self.db.execute(select(StudySession.user_id, StudySession.channel_id)
                .where(StudySession.status.in_(OPEN_STATUSES)))).all()
        for pair in pairs:
            async with self.db.begin():
                await self._lock(*pair)
                row = await self._open(*pair)
                if not row:
                    continue
                now = await self._now()
                live = await self._reconcile(row, now)
                if row.status == 'ACTIVE':
                    row.status = 'DISCONNECTED'
                    row.grace_expires_at = min(now + timedelta(seconds=RECONNECT_GRACE_SECONDS),
                        max(c.lease_expires_at for c in live) + timedelta(seconds=RECONNECT_GRACE_SECONDS))
                await self.db.execute(delete(StudySessionConnection).where(StudySessionConnection.session_id == row.id))

    @staticmethod
    def valid_completed_query(user_id, channel_id):
        # Future statistics can consume valid records; no UC38 aggregation is implemented.
        return select(StudySession).where(StudySession.user_id == user_id,
            StudySession.channel_id == channel_id, StudySession.status == 'COMPLETED')

    @staticmethod
    def public(row):
        return {key: getattr(row, key) for key in ('id', 'channel_id', 'status', 'started_at',
            'last_activity_at', 'ended_at', 'duration_us', 'grace_expires_at', 'end_reason')}

    async def recent(self, principal, channel_id, offset=0):
        async with self.transaction(principal):
            channel = await self.channel_access(channel_id, principal.user.id)
            if channel.type != ChannelType.STUDY_ROOM:
                raise AppError(422, 'CHANNEL_NOT_STUDY_ROOM', 'Channel không phải phòng tự học')
            rows = list(await self.db.scalars(select(StudySession).where(
                StudySession.user_id == principal.user.id, StudySession.channel_id == channel_id)
                .order_by(StudySession.started_at.desc(), StudySession.id).offset(offset).limit(51)))
            return {'items': [self.public(row) for row in rows[:50]],
                    'next_offset': offset + 50 if len(rows) > 50 else None}


async def study_session_worker():
    from app.db.session import session_factory
    while True:
        try:
            async with session_factory() as db:
                await StudySessionService(db).recover()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('Study session recovery unavailable; durable leases retained')
        await asyncio.sleep(2)
