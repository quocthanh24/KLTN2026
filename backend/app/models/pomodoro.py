"""Room cursor plus durable session and exactly-once interval history."""
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.core.pomodoro import DEFAULT_FOCUS_SECONDS, DEFAULT_BREAK_SECONDS


class PomodoroState(Base):
    __tablename__ = "pomodoro_states"
    channel_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(10), default="idle")
    phase: Mapped[str] = mapped_column(String(10), default="focus")
    focus_seconds: Mapped[int] = mapped_column(Integer, default=DEFAULT_FOCUS_SECONDS)
    break_seconds: Mapped[int] = mapped_column(Integer, default=DEFAULT_BREAK_SECONDS)
    session_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    session_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    phase_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remaining_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    completed_focus: Mapped[int] = mapped_column(Integer, default=0)


class PomodoroSession(Base):
    __tablename__ = "pomodoro_sessions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    channel_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    started_by: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PomodoroCompletion(Base):
    __tablename__ = "pomodoro_completions"
    __table_args__ = (UniqueConstraint("session_id", "interval", name="uq_pomodoro_completion_interval"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("pomodoro_sessions.id", ondelete="CASCADE"), index=True)
    interval: Mapped[int] = mapped_column(Integer)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    focus_seconds: Mapped[int] = mapped_column(Integer)
