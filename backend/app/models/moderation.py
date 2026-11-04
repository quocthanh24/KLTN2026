"""Reports retain stable target/scope IDs; audit survives deletion of business rows."""
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class ViolationReport(Base):
    __tablename__ = 'violation_reports'
    __table_args__ = (
        CheckConstraint("target_type IN ('MESSAGE','DOCUMENT')", name='ck_reports_target'),
        CheckConstraint("reason IN ('SPAM','ABUSE','COPYRIGHT','OTHER')", name='ck_reports_reason'),
        CheckConstraint('version >= 1',name='ck_reports_version'),
        CheckConstraint("(status='PENDING' AND resolution IS NULL AND resolved_by IS NULL) OR "
                        "(status='RESOLVED' AND outcome IS NOT NULL AND action IS NOT NULL AND "
                        "((outcome='NO_VIOLATION' AND action='NONE') OR (outcome='VIOLATION' AND action IN ('HIDE','ALREADY_UNAVAILABLE'))))",name='ck_reports_outcome_action'),
        CheckConstraint('resolved_at IS NULL OR resolved_at >= created_at',name='ck_reports_resolved_time'),
        CheckConstraint("(status='PENDING' AND outcome IS NULL AND action IS NULL AND resolved_at IS NULL) OR "
                        "(status='RESOLVED' AND outcome IN ('NO_VIOLATION','VIOLATION') AND action IN ('NONE','HIDE','ALREADY_UNAVAILABLE') "
                        "AND resolved_at IS NOT NULL AND resolution IS NOT NULL)", name='ck_reports_resolution'),
        Index('uq_reports_pending_reporter_target','reporter_id','target_type','target_id',unique=True,
              postgresql_where=text("status='PENDING'")),
        Index('ix_reports_status_created','status','created_at','id'),
    )
    id: Mapped[UUID] = mapped_column(Uuid,primary_key=True,default=uuid4)
    reporter_id: Mapped[UUID | None] = mapped_column(Uuid,ForeignKey('users.id',ondelete='SET NULL'))
    target_type: Mapped[str] = mapped_column(String(12))
    target_id: Mapped[UUID] = mapped_column(Uuid)
    channel_id: Mapped[UUID] = mapped_column(Uuid)
    workspace_id: Mapped[UUID] = mapped_column(Uuid)
    reason: Mapped[str] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12),default='PENDING',server_default='PENDING')
    version: Mapped[int] = mapped_column(Integer,default=1,server_default='1')
    outcome: Mapped[str | None] = mapped_column(String(20))
    action: Mapped[str | None] = mapped_column(String(24))
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[UUID | None] = mapped_column(Uuid,ForeignKey('users.id',ondelete='SET NULL'))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now())


class AdministrationAudit(Base):
    __tablename__='administration_audit'
    __table_args__=(Index('ix_administration_audit_target','target_type','target_id','created_at'),)
    id: Mapped[UUID] = mapped_column(Uuid,primary_key=True,default=uuid4)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid,ForeignKey('users.id',ondelete='SET NULL'))
    target_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[UUID] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(32))
    before: Mapped[dict] = mapped_column(JSONB)
    after: Mapped[dict] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now())
