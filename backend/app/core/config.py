from functools import lru_cache
from pathlib import Path

from typing import Self, Literal

from pydantic import AnyHttpUrl, Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str
    app_env: str
    database_url: PostgresDsn
    frontend_url: AnyHttpUrl
    jwt_secret_key: SecretStr
    access_token_expire_minutes: int = Field(default=15, gt=0)
    refresh_token_expire_days: int = Field(default=7, gt=0)
    refresh_cookie_name: str = Field(default="refresh_token", pattern=r"^[A-Za-z0-9_-]+$")
    cookie_secure: bool = False
    object_storage_endpoint: str = ""
    object_storage_access_key: SecretStr = SecretStr("")
    object_storage_secret_key: SecretStr = SecretStr("")
    object_storage_bucket: str = ""
    object_storage_secure: bool = False
    attachment_max_size_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    livekit_url: str = ""
    livekit_api_key: SecretStr = SecretStr("")
    livekit_api_secret: SecretStr = SecretStr("")
    media_authority_enabled: bool = False
    media_service_key_file: str = ""
    media_control_url: str = ""
    media_authority_pool_size: int = Field(default=10, ge=1, le=30)
    rag_model_dir: str = ""
    # Normal operation uses the configured API key; capped is an explicit test mode.
    ai_chat_billing_mode: Literal['metered', 'capped'] = 'metered'
    ai_chat_v2_live_approved: bool = False
    ai_chat_v2_budget_approval_id: str = ""
    ai_chat_v2_budget_topology: str = "single_host"
    ai_chat_v2_budget_ledger: str = str(Path(__file__).resolve().parents[2] / '.runtime' / 'ai-chat-v2-budget.sqlite3')
    ai_chat_v2_vision_ready: bool = True
    ai_chat_v2_max_images: int = Field(default=24, ge=2, le=24)
    ai_chat_v2_source_repair_attempts: int = Field(default=2, ge=0, le=3)
    ai_chat_v2_original_first: bool = True
    document_progressive_enabled: bool = True
    ai_chat_v2_job_ttl_hours: int = Field(default=24, ge=1, le=168)
    ai_chat_v2_worker_concurrency: int = Field(default=2, ge=1, le=8)
    ai_provider: str = "openai"
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-6-luna"
    openai_timeout_seconds: int = Field(default=60, ge=10, le=90)
    rag_processing_timeout_seconds: int = Field(default=120, ge=10, le=600)
    ai_turn_max_calls: int = Field(default=24,ge=3,le=24)
    ai_turn_timeout_seconds: int = Field(default=240,ge=30,le=300)
    document_vision_enabled: bool = True
    document_vision_max_pages: int = Field(default=32, ge=1, le=50)
    document_vision_max_calls: int = Field(default=32, ge=1, le=50)
    document_vision_max_pixels: int = Field(default=4_000_000, ge=100_000, le=8_000_000)
    document_vision_output_tokens: int = Field(default=4096, ge=512, le=8192)
    document_vision_daily_call_cap: int = Field(default=60, ge=1, le=1000)
    document_vision_ledger: str = str(Path(__file__).resolve().parents[2] / '.runtime' / 'document-vision.sqlite3')

    @field_validator("jwt_secret_key")
    @classmethod
    def require_signing_key(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode("utf-8")) < 32:
            raise ValueError("JWT_SECRET_KEY must contain at least 32 bytes")
        return value

    @model_validator(mode="after")
    def require_secure_production_cookie(self) -> Self:
        if self.app_env.lower() == "production" and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true in production")
        return self

    @field_validator("database_url")
    @classmethod
    def require_asyncpg(cls, value: PostgresDsn) -> PostgresDsn:
        if value.scheme != "postgresql+asyncpg":
            raise ValueError("DATABASE_URL must use postgresql+asyncpg")
        return value

    @field_validator("frontend_url")
    @classmethod
    def require_origin(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if (
            value.path not in (None, "/")
            or value.query is not None
            or value.fragment is not None
            or value.username is not None
            or value.password is not None
        ):
            raise ValueError("FRONTEND_URL must be an HTTP(S) origin without a path")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
