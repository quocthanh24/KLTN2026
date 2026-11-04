"""Derived media authorization; never stores bearer JWTs."""
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import Uuid, ForeignKey, DateTime, String, BigInteger, Integer, CheckConstraint, Index, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class MediaGrant(Base):
    __tablename__ = 'media_grants'
    __table_args__ = (CheckConstraint('expires_at > issued_at',name='ck_media_grant_expiry'),
        CheckConstraint('user_version > 0 AND workspace_version > 0',name='ck_media_grant_versions'),
        Index('ix_media_grant_user_session','user_id','auth_session_id'),
        Index('ix_media_grant_workspace_channel','workspace_id','channel_id'),
        Index('ix_media_grant_session','auth_session_id'),Index('ix_media_grant_channel','channel_id'),)
    id: Mapped[UUID] = mapped_column(Uuid,primary_key=True,default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid,ForeignKey('users.id',ondelete='CASCADE'),nullable=False)
    auth_session_id: Mapped[UUID] = mapped_column(Uuid,ForeignKey('auth_sessions.id',ondelete='CASCADE'),nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(Uuid,ForeignKey('workspaces.id',ondelete='CASCADE'),nullable=False)
    channel_id: Mapped[UUID] = mapped_column(Uuid,ForeignKey('channels.id',ondelete='CASCADE'),nullable=False)
    membership_id: Mapped[UUID] = mapped_column(Uuid,nullable=False)  # snapshot: membership removal must remain possible
    user_version: Mapped[int] = mapped_column(BigInteger,nullable=False)
    workspace_version: Mapped[int] = mapped_column(BigInteger,nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,server_default=func.clock_timestamp())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MediaRevocation(Base):
    __tablename__ = 'media_revocations'
    __table_args__ = (CheckConstraint("state IN ('PENDING','COMPLETE','ERROR')",name='ck_media_revocation_state'),
        CheckConstraint("scope IN ('USER','WORKSPACE','SESSION','CHANNEL','MEMBERSHIP')",name='ck_media_revocation_scope'),
        CheckConstraint("jsonb_typeof(grant_ids)='array'",name='ck_media_revocation_grants'),
        CheckConstraint('attempts >= 0',name='ck_media_revocation_attempts'),
        CheckConstraint("(state='COMPLETE' AND ack IS NOT NULL AND completed_at IS NOT NULL AND COALESCE(ack->>'operation'=id::text AND ack->>'barrier'='transport-closed-v1' AND ack->>'pending'='false' AND length(ack->>'boot_id')=32,false)) OR (state<>'COMPLETE' AND completed_at IS NULL AND ack IS NULL)",name='ck_media_revocation_ack'),
        Index('ix_media_outbox_due','state','next_attempt_at'),Index('ix_media_revocation_actor','actor_id'),)
    id: Mapped[UUID] = mapped_column(Uuid,primary_key=True,default=uuid4)
    scope: Mapped[str] = mapped_column(String(20),nullable=False)
    scope_id: Mapped[UUID] = mapped_column(Uuid,nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid,ForeignKey('users.id',ondelete='SET NULL'))
    grant_ids: Mapped[list] = mapped_column(JSONB,nullable=False)
    state: Mapped[str] = mapped_column(String(12),nullable=False,default='PENDING',server_default='PENDING')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,server_default=func.clock_timestamp())
    origin_xid: Mapped[int] = mapped_column(BigInteger,nullable=False,server_default=text('txid_current()'))
    # This is written by the first post-commit dispatcher. Actual commit latency
    # evidence also brackets commit with the test client's DB/HTTP observations.
    dispatch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,server_default=func.clock_timestamp())
    attempts: Mapped[int] = mapped_column(Integer,nullable=False,default=0,server_default='0')
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ack: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))
