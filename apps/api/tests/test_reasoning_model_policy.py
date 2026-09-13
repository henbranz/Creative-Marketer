from decimal import Decimal

from creative_marketer.agent_runtime.application import (
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
        8_000,
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
        "creative_balanced",
        "production_deep",
    ]
    assert [
        configuration.prompt_revision for configuration in (researcher, creative, producer)
    ] == [
        "researcher_v2_sol_policy",
        "creative_strategist_v2_sol_policy",
        "producer_v3_sol_policy",
    ]
    assert [
        configuration.run_budget_policy.max_cost
        for configuration in (researcher, creative, producer)
    ] == [
        Decimal("0.16"),
        Decimal("0.256"),
        Decimal("0.32"),
    ]
    assert [
        configuration.period_budget_policy.max_cost
        for configuration in (researcher, creative, producer)
    ] == [
        Decimal("3.20"),
        Decimal("5.12"),
        Decimal("6.40"),
    ]
    assert all(
        configuration.run_budget_policy.max_model_calls == 1
        for configuration in (researcher, creative, producer)
    )
    assert all(
        configuration.run_budget_policy.max_tool_calls == 0
        for configuration in (researcher, creative, producer)
    )
    assert producer.display_name == "Producer"


def test_reasoning_policy_does_not_change_media_routes() -> None:
    router = initial_media_router()
    assert router.resolve("production_image").model == "gpt-image-2.5-sunburst-2026-09-08"
    assert router.resolve("production_video").model == "dreamina-seedance-2-5-260628"
