from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class AIConversation(Base):
    __tablename__ = 'ai_conversations'
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    channel_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('channels.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('users.id', ondelete='CASCADE'), index=True)
    title: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AITurn(Base):
    __tablename__ = 'ai_turns'
    __table_args__ = (UniqueConstraint('conversation_id', 'request_id', name='uq_ai_turn_request'),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('ai_conversations.id', ondelete='CASCADE'), index=True)
    request_id: Mapped[UUID] = mapped_column(Uuid)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30))
    processing_token: Mapped[UUID | None] = mapped_column(Uuid)
    # Snapshot IDs/locations; no chunk FK cascade that would erase history.
    sources: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
