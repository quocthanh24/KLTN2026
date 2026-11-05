"""Additive, opt-in derived-unit progress; original storage/status are unchanged."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import ForeignKey,Uuid,String,DateTime,func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped,mapped_column
from app.db.base import Base


class DocumentDerivation(Base):
    __tablename__='document_derivations'
    document_id:Mapped[UUID]=mapped_column(Uuid,ForeignKey('learning_documents.id',ondelete='CASCADE'),primary_key=True)
    revision:Mapped[str]=mapped_column(String(64))
    state:Mapped[dict]=mapped_column(JSONB)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),onupdate=func.now())
