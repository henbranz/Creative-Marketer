from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import AnyHttpUrl, Field, PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    """Validated process configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: PostgresDsn
    event_publisher_database_url: PostgresDsn | None = None
    dev_identity_enabled: bool = False
    audit_fingerprint_key: SecretStr = Field(min_length=32)
    cors_origins: list[AnyHttpUrl] = Field(default_factory=list)
    otel_mode: Literal["disabled", "development", "console", "otlp", "test", "in_memory"] = (
        "disabled"
    )
    otel_exporter_otlp_endpoint: AnyHttpUrl | None = None
    service_instance_id: str = Field(
        default_factory=lambda: uuid4().hex, min_length=1, max_length=128
    )
    otel_trace_sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def reject_development_identity_in_deployed_environments(self) -> "Settings":
        if self.dev_identity_enabled and self.app_env not in {"development", "test"}:
            raise ValueError("development identity is forbidden outside development and test")
        if self.otel_mode == "otlp" and self.otel_exporter_otlp_endpoint is None:
            raise ValueError("OTEL_EXPORTER_OTLP_ENDPOINT is required in otlp mode")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return one immutable settings object per process."""

    return Settings()
