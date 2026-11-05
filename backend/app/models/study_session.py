"""Durable room attendance, independent of shared Pomodoro focus completions."""
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class StudySession(Base):
    __tablename__ = 'study_sessions'
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','DISCONNECTED','COMPLETED','INVALID')", name='ck_study_sessions_status'),
        CheckConstraint('last_activity_at >= started_at', name='ck_study_sessions_activity'),
        CheckConstraint('duration_us >= 0', name='ck_study_sessions_duration'),
        CheckConstraint("(status='DISCONNECTED') = (grace_expires_at IS NOT NULL)", name='ck_study_sessions_grace'),
        CheckConstraint("(status IN ('ACTIVE','DISCONNECTED') AND ended_at IS NULL AND end_reason IS NULL) OR "
                        "(status='COMPLETED' AND ended_at IS NOT NULL AND end_reason IS NOT NULL AND ended_at=last_activity_at "
                        "AND ended_at>=started_at AND duration_us<=EXTRACT(EPOCH FROM (ended_at-started_at))*1000000) OR "
                        "(status='INVALID' AND ended_at IS NULL AND end_reason='INVALID_TIME' AND duration_us=0)", name='ck_study_sessions_completion'),
        Index('uq_study_sessions_open_user_channel', 'user_id', 'channel_id', unique=True,
              postgresql_where=text("status IN ('ACTIVE','DISCONNECTED')")),
        Index('ix_study_sessions_user_channel_started', 'user_id', 'channel_id', 'started_at'),
        Index('ix_study_sessions_recovery', 'status', 'last_activity_at'),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('users.id', ondelete='CASCADE'))
    channel_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('channels.id', ondelete='CASCADE'))
    status: Mapped[str] = mapped_column(String(20), default='ACTIVE')
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grace_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_us: Mapped[int] = mapped_column(BigInteger, default=0)
    analytics_tracked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    end_reason: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StudySessionConnection(Base):
    __tablename__ = 'study_session_connections'
    __table_args__ = (
        CheckConstraint('lease_expires_at > last_seen_at', name='ck_study_session_connections_lease'),
        Index('ix_study_session_connections_session', 'session_id'),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('study_sessions.id', ondelete='CASCADE'))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
