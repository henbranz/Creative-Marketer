# mypy: disable-error-code="no-untyped-def"

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

import scripts.bootstrap_creative_strategist as creative_bootstrap
from creative_marketer.agent_runtime.application import (
    historical_creative_strategist_route,
    initial_agent_model_routes,
    initial_creative_strategist_route,
    initial_researcher_route,
)
from creative_marketer.production.application import initial_media_router, initial_producer_route
from scripts.bootstrap_creative_strategist import creative_strategist_configuration
from scripts.bootstrap_producer import producer_configuration
from scripts.bootstrap_researcher import researcher_configuration


def test_current_reasoning_routes_use_sol_with_independent_effort_and_bounds() -> None:
    researcher = initial_researcher_route()
    creative = initial_creative_strategist_route()
    producer = initial_producer_route()

    assert [route.model for route in (researcher, creative, producer)] == [
        "gpt-5.6-sol",
        "gpt-5.6-sol",
        "gpt-5.6-sol",
    ]
    assert researcher.model != "gpt-5.6-terra"
    assert creative.model != "gpt-5.6-terra"
    assert producer.model != "gpt-6-astra"
    assert [route.reasoning_effort for route in (researcher, creative, producer)] == [
        "medium",
        "high",
        "high",
    ]
    assert [route.max_output_tokens for route in (researcher, creative, producer)] == [
        6_000,
        16_000,
        12_000,
    ]
    assert len({route.route_version for route in (researcher, creative, producer)}) == 3
    for route in (researcher, creative, producer):
        assert route.pricing.version == "openai-gpt-5.6-sol-2026-09-13"
        assert route.pricing.input_price_per_million == Decimal("4")
        assert route.pricing.output_price_per_million == Decimal("20")


def test_fresh_agent_configurations_create_new_sol_policy_versions() -> None:
    researcher = researcher_configuration()
    creative = creative_strategist_configuration()
    producer = producer_configuration()

    assert [
        configuration.model_policy.profile_key for configuration in (researcher, creative, producer)
    ] == [
        "research_balanced",
        "creative_balanced_v2",
        "production_deep",
    ]
    assert [
        configuration.prompt_revision for configuration in (researcher, creative, producer)
    ] == [
        "researcher_v3_social_evidence",
        "creative_strategist_v3_sol_16k_policy",
        "producer_v5_contract_v2",
    ]
    assert [
        configuration.run_budget_policy.max_cost
        for configuration in (researcher, creative, producer)
    ] == [
        Decimal("0.16"),
        Decimal("0.416"),
        Decimal("0.32"),
    ]
    assert [
        configuration.period_budget_policy.max_cost
        for configuration in (researcher, creative, producer)
    ] == [
        Decimal("3.20"),
        Decimal("8.32"),
        Decimal("6.40"),
    ]
    assert all(
        configuration.run_budget_policy.max_model_calls == 1
        for configuration in (researcher, creative, producer)
    )
    assert producer.output_contract_version == 2
    assert all(
        configuration.run_budget_policy.max_tool_calls == 0
        for configuration in (researcher, creative, producer)
    )
    assert producer.display_name == "Producer"


def test_creative_route_upgrade_preserves_historical_route_and_exact_envelope() -> None:
    historical = historical_creative_strategist_route()
    current = initial_creative_strategist_route()
    routes = {route.profile_key: route for route in initial_agent_model_routes()}

    assert routes[historical.profile_key] == historical
    assert routes[current.profile_key] == current
    assert historical.profile_key == "creative_balanced"
    assert historical.max_output_tokens == 8_000
    assert current.profile_key == "creative_balanced_v2"
    assert current.max_output_tokens == 16_000
    assert 21_324 + current.max_output_tokens <= 40_000
    assert current.pricing.cost(24_000, 16_000) == Decimal("0.416")
    assert current.pricing.cost(40_000, 0) == Decimal("0.160")


def test_reasoning_policy_does_not_change_media_routes() -> None:
    router = initial_media_router()
    assert router.resolve("production_image").model == "gpt-image-2.5-sunburst-2026-09-08"
    assert router.resolve("production_video").model == "dreamina-seedance-2-5-260628"


@pytest.mark.asyncio
async def test_creative_bootstrap_creates_and_activates_new_immutable_version(
    monkeypatch,
) -> None:
    tenant_id, user_id, definition_id, old_version_id, new_version_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    old = SimpleNamespace(
        id=old_version_id,
        version_number=1,
        configuration_digest="sha256:" + "a" * 64,
    )
    definition = SimpleNamespace(id=definition_id, agent_type="creative_strategist")
    captured = {}

    async def list_definitions(_context):
        return (definition,)

    async def resolve_active(_context, _definition_id):
        return old

    async def create_version(_context, _definition_id, configuration):
        captured["configuration"] = configuration
        return SimpleNamespace(id=new_version_id, version_number=2)

    async def activate(_context, _definition_id, version_id):
        captured["activated"] = version_id

    monkeypatch.setenv("BOOTSTRAP_TENANT_ID", str(tenant_id))
    monkeypatch.setenv("BOOTSTRAP_USER_ID", str(user_id))
    monkeypatch.setattr(
        creative_bootstrap,
        "Settings",
        lambda: SimpleNamespace(app_env="development", database_url="unused"),
    )
    monkeypatch.setattr(creative_bootstrap, "create_session_factory", lambda _url: object())
    monkeypatch.setattr(
        creative_bootstrap, "SqlAlchemyAgentRegistryUnitOfWorkFactory", lambda _value: object()
    )
    monkeypatch.setattr(
        creative_bootstrap, "ListTenantAgentDefinitions", lambda _factory: list_definitions
    )
    monkeypatch.setattr(
        creative_bootstrap, "ResolveActiveAgentVersion", lambda _factory: resolve_active
    )
    monkeypatch.setattr(creative_bootstrap, "CreateAgentVersion", lambda _factory: create_version)
    monkeypatch.setattr(creative_bootstrap, "ActivateAgentVersion", lambda _factory: activate)

    await creative_bootstrap.run()

    assert captured["configuration"].configuration_digest != old.configuration_digest
    assert captured["configuration"].model_policy.profile_key == "creative_balanced_v2"
    assert captured["activated"] == new_version_id
    assert old.version_number == 1 and old.configuration_digest == "sha256:" + "a" * 64
