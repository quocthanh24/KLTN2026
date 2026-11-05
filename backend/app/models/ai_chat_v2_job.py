"""Private, additive V2 checkpoints. No columns added to legacy entities."""
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class AIChatV2Job(Base):
    __tablename__ = 'ai_chat_v2_jobs'
    id: Mapped[UUID] = mapped_column(Uuid,primary_key=True,default=uuid4)
    turn_id: Mapped[UUID] = mapped_column(Uuid,ForeignKey('ai_turns.id',ondelete='CASCADE'),unique=True)
    user_id: Mapped[UUID] = mapped_column(Uuid,ForeignKey('users.id',ondelete='CASCADE'),index=True)
    channel_id: Mapped[UUID] = mapped_column(Uuid,ForeignKey('channels.id',ondelete='CASCADE'),index=True)
    status: Mapped[str] = mapped_column(String(30),default='pending')
    stage: Mapped[str] = mapped_column(String(30),default='inventory')
    version: Mapped[int] = mapped_column(Integer,default=0)
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checkpoint: Mapped[dict] = mapped_column(JSONB,default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now(),onupdate=func.now())
