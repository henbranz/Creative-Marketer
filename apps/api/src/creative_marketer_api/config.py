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
    object_storage_backend: Literal["disabled", "s3"] = "disabled"
    object_storage_endpoint_url: AnyHttpUrl = AnyHttpUrl("http://localhost:9000")
    object_storage_public_endpoint_url: AnyHttpUrl = AnyHttpUrl("http://localhost:9000")
    object_storage_region: str = Field(default="us-east-1", min_length=1, max_length=64)
    object_storage_bucket: str = Field(
        default="creative-marketer-assets", min_length=3, max_length=63
    )
    object_storage_access_key_id: str = Field(default="disabled-access-key", min_length=3)
    object_storage_secret_access_key: SecretStr = Field(
        default=SecretStr("disabled-secret-access-key"), min_length=8
    )
    asset_upload_ttl_seconds: int = Field(default=900, ge=600, le=900)
    asset_download_ttl_seconds: int = Field(default=600, ge=300, le=900)

    @model_validator(mode="after")
    def reject_development_identity_in_deployed_environments(self) -> "Settings":
        if self.dev_identity_enabled and self.app_env not in {"development", "test"}:
            raise ValueError("development identity is forbidden outside development and test")
        if self.otel_mode == "otlp" and self.otel_exporter_otlp_endpoint is None:
            raise ValueError("OTEL_EXPORTER_OTLP_ENDPOINT is required in otlp mode")
        if self.object_storage_backend == "s3" and self.app_env in {"staging", "production"}:
            if self.object_storage_endpoint_url.host in {"localhost", "127.0.0.1"}:
                raise ValueError("deployed S3 storage cannot use a loopback endpoint")
            if self.object_storage_access_key_id.startswith("disabled-"):
                raise ValueError("deployed S3 storage requires injected credentials")
        if self.object_storage_backend == "s3" and not self.cors_origins:
            raise ValueError("S3 storage requires at least one explicit CORS origin")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return one immutable settings object per process."""

    return Settings()
