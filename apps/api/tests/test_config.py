from typing import Literal
from uuid import uuid4

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
        "object_storage_access_key_id": "disabled-access-key",
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
            cors_origins=[],
        )


def test_openai_provider_requires_real_key_and_deployed_workload_identity() -> None:
    database_url = "postgresql+psycopg://test:test@localhost:5432/test"
    api_only = Settings(database_url=database_url, model_provider_backend="openai")
    assert api_only.openai_api_key is None
    with pytest.raises(ValidationError, match="placeholder"):
        Settings(
            database_url=database_url,
            model_provider_backend="openai",
            openai_api_key="test-placeholder",
        )
    with pytest.raises(ValidationError, match="workload identity"):
        Settings(
            app_env="production",
            database_url=database_url,
            object_storage_backend="disabled",
            model_provider_backend="openai",
            openai_api_key="unit-live-shaped-credential",
        )
    configured = Settings(
        app_env="production",
        database_url=database_url,
        object_storage_backend="disabled",
        model_provider_backend="openai",
        openai_api_key="unit-live-shaped-credential",
        agent_workload_id="kubernetes/service-account/researcher",
    )
    assert configured.openai_api_key is not None
    assert "real-looking" not in repr(configured)


def test_recovery_operator_configuration_is_paired_and_deployment_safe() -> None:
    database_url = "postgresql+psycopg://test:test@localhost:5432/test"
    with pytest.raises(ValidationError, match="configured together"):
        Settings(database_url=database_url, agent_recovery_operator_id="operations/recovery")
    with pytest.raises(ValidationError, match="configured together"):
        Settings(database_url=database_url, agent_recovery_tenant_id=uuid4())
    with pytest.raises(ValidationError, match="deployment-issued operator identity"):
        Settings(
            app_env="production",
            database_url=database_url,
            object_storage_backend="disabled",
            agent_recovery_operator_id="local-recovery",
            agent_recovery_tenant_id=uuid4(),
        )
