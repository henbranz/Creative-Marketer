# mypy: disable-error-code="no-untyped-def"

from types import SimpleNamespace

import pytest

import scripts.live_validation as live
from creative_marketer.production.application import initial_producer_route
from creative_marketer_api.config import Settings

DATABASE = "postgresql+psycopg://test:test@localhost:5432/test"


@pytest.mark.asyncio
async def test_provider_preflight_checks_models_without_generation(monkeypatch, capsys) -> None:
    secret = "unit-secret-sentinel-never-print"
    retrieve = []

    class Models:
        async def retrieve(self, model):
            retrieve.append(model)
            return SimpleNamespace(id=model)

    client = SimpleNamespace(models=Models())
    monkeypatch.setattr("scripts.live_validation.socket.getaddrinfo", lambda *_args: [(object(),)])
    settings = Settings(
        database_url=DATABASE,
        openai_api_key=secret,
        byteplus_las_api_key=secret,
    )
    assert await live.preflight(settings, client) == 0
    assert retrieve == ["gpt-6-astra", "gpt-image-2.5-sunburst-2026-09-08"]
    output = capsys.readouterr().out
    assert secret not in output
    assert "no inference or media-generation requests" in output


def test_live_api_requires_acknowledgement_product_and_local_identity(monkeypatch) -> None:
    settings = Settings(database_url=DATABASE)
    with pytest.raises(RuntimeError, match="ALLOW_BILLABLE_MEDIA"):
        live._api(settings)
    authorized = Settings(
        database_url=DATABASE,
        allow_billable_media=True,
        run_live_e2e=live.ACK,
    )
    with pytest.raises(RuntimeError, match="LIVE_E2E_PRODUCT_ID"):
        live._api(authorized)
    configured = Settings(
        database_url=DATABASE,
        allow_billable_media=True,
        run_live_e2e=live.ACK,
        live_e2e_product_id="10000000-0000-0000-0000-000000000001",
    )
    monkeypatch.delenv("CM_TENANT_ID", raising=False)
    monkeypatch.delenv("CM_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="CM_TENANT_ID"):
        live._api(configured)


def test_successful_run_requires_the_exact_current_route() -> None:
    route = initial_producer_route()
    historical = {
        "id": "old",
        "status": "SUCCEEDED",
        "resolved_model": "gpt-5.6-sol",
        "model_route_version": "openai-gpt-5.6-sol-production-2026-09-12",
    }
    current = {
        "id": "current",
        "status": "SUCCEEDED",
        "resolved_model": route.model,
        "model_route_version": route.route_version,
    }
    assert live._successful([historical, current], route) == current
    assert live._successful([historical], route) is None
