"""Exact attendance intervals and room focus execution ledger, never inferred history."""
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class StudyActivityInterval(Base):
    __tablename__ = 'study_activity_intervals'
    __table_args__ = (
        CheckConstraint('ended_at > started_at', name='ck_study_activity_positive'),
        UniqueConstraint('session_id', 'started_at', name='uq_study_activity_start'),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('study_sessions.id', ondelete='CASCADE'))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PomodoroFocusInterval(Base):
    __tablename__ = 'pomodoro_focus_intervals'
    __table_args__ = (
        UniqueConstraint('session_id', 'interval', name='uq_pomodoro_focus_cycle'),
        CheckConstraint("status IN ('RUNNING','COMPLETED','ENDED')", name='ck_pomodoro_focus_status'),
        CheckConstraint("(status='RUNNING' AND ended_at IS NULL) OR (status<>'RUNNING' AND ended_at>=started_at)",
                        name='ck_pomodoro_focus_end'),
        CheckConstraint('interval > 0', name='ck_pomodoro_focus_sequence'),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('pomodoro_sessions.id', ondelete='CASCADE'))
    interval: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default='RUNNING')
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PomodoroFocusSegment(Base):
    __tablename__ = 'pomodoro_focus_segments'
    __table_args__ = (
        UniqueConstraint('focus_id', 'started_at', name='uq_pomodoro_focus_segment_start'),
        CheckConstraint('ended_at IS NULL OR ended_at>=started_at', name='ck_pomodoro_focus_segment_time'),
        Index('uq_pomodoro_focus_segment_open', 'focus_id', unique=True,
              postgresql_where=text('ended_at IS NULL')),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    focus_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('pomodoro_focus_intervals.id', ondelete='CASCADE'))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
