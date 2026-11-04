from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, computed_field
from app.models.learning_document import DocumentStatus


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    channel_id: UUID
    uploader_id: UUID
    original_filename: str
    name: str
    description: str | None
    content_type: str
    size_bytes: int
    status: DocumentStatus
    processing_error: str | None
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None
    uploader_name: str | None = None
    can_manage: bool = False
    checksum: str
    storage_status: str = "STORED"
    derived_state: dict | None = None

    @computed_field
    @property
    def original_read_capability(self) -> dict:
        from app.core.config import get_settings
        settings=get_settings()
        image=self.content_type in {'application/pdf','image/jpeg','image/png'}
        return {'representation':'page_image' if self.content_type=='application/pdf' else
            'original_image' if image else 'docx_body_structure',
            'available_independently_of_index':True,
            'visual_model':'OPERATOR_ENABLED_NOT_LIVE_VERIFIED' if settings.ai_chat_v2_vision_ready else 'NOT_ENABLED',
            'semantic_coverage':'NOT_CERTIFIED_BY_INDEX','direct_pdf':False}


class DocumentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("description")
    @classmethod
    def trim_description(cls, value):
        return value.strip() or None if value is not None else None
