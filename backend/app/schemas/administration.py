from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class ReportCreate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    target_type: Literal['MESSAGE','DOCUMENT']
    target_id: UUID
    reason: Literal['SPAM','ABUSE','COPYRIGHT','OTHER']
    description: str | None = Field(None,max_length=2000)


class StateChange(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version: int = Field(ge=1,strict=True)
    enabled: bool = Field(strict=True)
    reason: str = Field(min_length=3,max_length=1000,pattern=r'\S')


class ReportDecision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version: int = Field(ge=1,strict=True)
    outcome: Literal['NO_VIOLATION','VIOLATION']
    action: Literal['NONE','HIDE','ALREADY_UNAVAILABLE']
    reason: str = Field(min_length=3,max_length=1000,pattern=r'\S')
