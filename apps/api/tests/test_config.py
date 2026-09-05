from typing import Literal

import pytest
from pydantic import ValidationError

from creative_marketer_api.config import Settings


def test_settings_reject_invalid_database_url() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="sqlite:///local.db")


def test_settings_reject_unknown_environment() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="demo",
            database_url="postgresql+psycopg://test:test@localhost:5432/test",
        )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_settings_reject_development_identity_in_deployed_environments(
    environment: Literal["staging", "production"],
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env=environment,
            dev_identity_enabled=True,
            database_url="postgresql+psycopg://test:test@localhost:5432/test",
        )


def test_observability_configuration_is_bounded_and_otlp_is_explicit() -> None:
    database_url = "postgresql+psycopg://test:test@localhost:5432/test"
    with pytest.raises(ValidationError):
        Settings(database_url=database_url, otel_trace_sample_ratio=1.1)
    with pytest.raises(ValidationError, match="OTEL_EXPORTER_OTLP_ENDPOINT"):
        Settings(database_url=database_url, otel_mode="otlp")
    configured = Settings(
        database_url=database_url,
        otel_mode="otlp",
        otel_exporter_otlp_endpoint="http://collector:4318",
        otel_trace_sample_ratio=0.25,
    )
    assert configured.otel_trace_sample_ratio == 0.25


def test_deployed_s3_storage_rejects_local_or_placeholder_credentials() -> None:
    common = {
        "app_env": "production",
        "database_url": "postgresql+psycopg://test:test@localhost:5432/test",
        "object_storage_backend": "s3",
    }
    with pytest.raises(ValidationError, match="loopback"):
        Settings(**common)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="injected credentials"):
        Settings(
            **common,  # type: ignore[arg-type]
            object_storage_endpoint_url="https://storage.example.test",
            object_storage_public_endpoint_url="https://storage.example.test",
        )


def test_s3_storage_requires_an_explicit_cors_origin() -> None:
    with pytest.raises(ValidationError, match="explicit CORS origin"):
        Settings(
            database_url="postgresql+psycopg://test:test@localhost:5432/test",
            object_storage_backend="s3",
        )
