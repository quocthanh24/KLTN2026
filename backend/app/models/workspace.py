from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__=(CheckConstraint('admin_version >= 1',name='ck_workspaces_admin_version'),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    admin_version: Mapped[int] = mapped_column(Integer,default=1,server_default='1')
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    members: Mapped[list["WorkspaceMember"]] = relationship(back_populates="workspace", lazy="raise")
    invitations: Mapped[list["WorkspaceInvitation"]] = relationship(back_populates="workspace", lazy="raise")
    channels: Mapped[list["Channel"]] = relationship(back_populates="workspace", lazy="raise")
