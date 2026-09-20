# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,assignment"

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest

from creative_marketer_api import researcher_worker
from creative_marketer_api.fake_demo_outputs import (
    creative_output,
    intelligence_output,
    production_output,
)


@pytest.mark.asyncio
async def test_bridge_requires_publisher_database() -> None:
    settings = SimpleNamespace(event_publisher_database_url=None)
    with pytest.raises(RuntimeError, match="EVENT_PUBLISHER_DATABASE_URL"):
        await researcher_worker._bridge_loop(settings, object())


@pytest.mark.asyncio
async def test_database_agent_type_resolver_reads_run_from_tenant_uow() -> None:
    holder = {"run": SimpleNamespace(agent_type="commerce_operations")}

    class Runs:
        async def get(self, _run_id):
            return holder["run"]

    class Uow:
        runs = Runs()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    resolver = researcher_worker.DatabaseAgentTypeResolver(lambda _tenant_id: Uow())
    assert await resolver.agent_type(object(), object()) == "commerce_operations"
    holder["run"] = None
    assert await resolver.agent_type(object(), object()) is None


def test_researcher_worker_main_delegates_to_async_runtime(monkeypatch) -> None:
    observed: list[object] = []
    sentinel = object()
    monkeypatch.setattr(researcher_worker, "run", lambda: sentinel)
    monkeypatch.setattr(asyncio, "run", observed.append)

    researcher_worker.main()

    assert observed == [sentinel]


@pytest.mark.asyncio
async def test_researcher_worker_rejects_openai_without_key(monkeypatch) -> None:
    monkeypatch.setattr(
        researcher_worker,
        "Settings",
        lambda: SimpleNamespace(model_provider_backend="openai", openai_api_key=None),
    )

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        await researcher_worker.run()


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer_fails", [False, True])
async def test_bridge_marks_started_events_published_or_retryable(
    monkeypatch, consumer_fails: bool
) -> None:
    item = SimpleNamespace(event=object(), trace_context=None)

    class Publisher:
        def __init__(self):
            self.published = []
            self.retried = []

        async def claim_ready_types(self, *_args, **kwargs):
            assert kwargs["event_types"] == ("agent.run.requested.v1",)
            return (item,)

        async def mark_published(self, value, **_kwargs):
            self.published.append(value)

        async def mark_retryable(self, value, **kwargs):
            assert kwargs["error_code"] == "RESEARCHER_BRIDGE_UNAVAILABLE"
            assert kwargs["error_digest"].startswith("sha256:")
            self.retried.append(value)

    class Consumer:
        async def __call__(self, name, event, trace_context):
            assert name == "researcher-temporal-starter"
            assert event is item.event and trace_context is None
            if consumer_fails:
                raise RuntimeError("temporary Temporal outage")

    async def stop_after_iteration(_delay):
        raise asyncio.CancelledError

    publisher = Publisher()
    monkeypatch.setattr(researcher_worker, "PostgresPublisherStore", lambda _factory: publisher)
    monkeypatch.setattr(researcher_worker, "create_session_factory", lambda _url: object())
    monkeypatch.setattr(
        researcher_worker, "SqlAlchemyConsumerUnitOfWorkFactory", lambda _factory: object()
    )
    monkeypatch.setattr(researcher_worker, "ProcessEvent", lambda *_args: Consumer())
    monkeypatch.setattr(asyncio, "sleep", stop_after_iteration)
    settings = SimpleNamespace(
        event_publisher_database_url="postgresql://publisher",
        database_url="postgresql://runtime",
    )
    with pytest.raises(asyncio.CancelledError):
        await researcher_worker._bridge_loop(settings, object())
    assert bool(publisher.retried) is consumer_fails
    assert bool(publisher.published) is not consumer_fails


@pytest.mark.asyncio
async def test_worker_composes_credential_only_in_worker_process(monkeypatch) -> None:
    settings = SimpleNamespace(
        model_provider_backend="openai",
        openai_api_key=SimpleNamespace(get_secret_value=lambda: "unit-live-credential"),
        database_url="postgresql://runtime",
        event_publisher_database_url="postgresql://publisher",
        agent_workload_id="test/researcher-worker",
        app_env="test",
    )
    client = object()
    observed: dict[str, object] = {}

    async def connect(address, *, namespace):
        observed["temporal"] = (address, namespace)
        return client

    async def bridge(received_settings, researcher, agent, resolver):
        observed["bridge"] = (received_settings, researcher, agent, resolver)

    class Worker:
        def __init__(self, received_client, **kwargs):
            assert received_client is client
            assert kwargs["task_queue"]
            assert len(kwargs["workflows"]) == len(kwargs["activities"]) == 2

        async def __aenter__(self):
            observed["entered"] = True
            return self

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(researcher_worker, "Settings", lambda: settings)
    monkeypatch.setattr(researcher_worker, "create_session_factory", lambda _url: object())
    monkeypatch.setattr(
        researcher_worker, "SqlAlchemyAgentRuntimeUnitOfWorkFactory", lambda _factory: object()
    )
    monkeypatch.setattr(
        researcher_worker,
        "OpenAIResponsesModelProvider",
        lambda key: observed.setdefault("key", key),
    )
    monkeypatch.setattr(researcher_worker, "connect_client", connect)
    monkeypatch.setattr(researcher_worker, "Worker", Worker)
    monkeypatch.setattr(researcher_worker, "_bridge_loop", bridge)
    await researcher_worker.run()
    assert observed.get("key") == "unit-live-credential"
    assert observed.get("entered") is True
    assert observed.get("bridge") is not None


@pytest.mark.asyncio
async def test_worker_fails_closed_without_enabled_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        researcher_worker,
        "Settings",
        lambda: SimpleNamespace(model_provider_backend="disabled", openai_api_key=None),
    )
    with pytest.raises(RuntimeError, match="MODEL_PROVIDER_BACKEND=openai"):
        await researcher_worker.run()


def test_worker_main_owns_asyncio_entrypoint(monkeypatch) -> None:
    observed = []

    def run(coroutine):
        observed.append(coroutine)
        coroutine.close()

    monkeypatch.setattr(asyncio, "run", run)
    researcher_worker.main()
    assert len(observed) == 1


def test_fake_worker_supports_grounded_commerce_report_without_tools() -> None:
    inventory_id = "00000000-0000-0000-0000-000000000101"
    order_id = "00000000-0000-0000-0000-000000000102"
    invocation = SimpleNamespace(
        output_contract_key="commerce.operations_report",
        capability_context={
            "inventory": (
                {
                    "observation_id": inventory_id,
                    "external_variant_id": "fake-variant-1",
                },
            ),
            "orders": (
                {
                    "observation_id": order_id,
                    "external_order_id": "fake-order-paid",
                    "payment_state": "PAID",
                    "currency": "USD",
                },
            ),
            "deterministic_exceptions": (
                {"observation_id": inventory_id, "kind": "LOW_STOCK"},
                {"observation_id": order_id, "kind": "PAID_BUT_UNFULFILLED"},
            ),
        },
    )
    result = researcher_worker._fake_demo_result(invocation)
    assert result.model == "gpt-5.6-sol"
    assert isinstance(result.output, dict)
    inventory_exceptions = result.output["inventory_exceptions"]
    order_exceptions = result.output["order_exceptions"]
    assert isinstance(inventory_exceptions, list) and isinstance(order_exceptions, list)
    assert inventory_exceptions[0]["observation_id"] == inventory_id
    assert order_exceptions[0]["observation_id"] == order_id
    assert result.output["action_proposals"] == [
        {
            "action_type": "INVENTORY_ADJUSTMENT",
            "external_variant_id": "fake-variant-1",
            "exact_quantity": 8,
            "reason": "Restore the reviewed local-demo inventory buffer.",
        },
        {
            "action_type": "INVENTORY_ADJUSTMENT",
            "external_variant_id": "fake-variant-1",
            "exact_quantity": 6,
            "reason": "Alternative local-demo buffer for rejection-path validation.",
        },
        {
            "action_type": "REFUND",
            "external_order_id": "fake-order-paid",
            "exact_amount": "10.00",
            "currency": "USD",
            "reason": "Issue the exact reviewed local-demo partial refund.",
        },
    ]


def test_fake_worker_supervisor_uses_only_deterministically_allowed_actions() -> None:
    invocation = SimpleNamespace(
        output_contract_key="orchestration.supervisor_report",
        capability_context={
            "cycle": {"stage": "AWAITING_CONCEPT_APPROVAL"},
            "readiness": {
                "requirements": [
                    {
                        "key": "concept_approval",
                        "state": "WAITING",
                        "message": "Choose a concept for production.",
                    }
                ],
                "allowed_actions": ["APPROVE_CONCEPT"],
            },
        },
    )
    result = researcher_worker._fake_demo_result(invocation)
    assert result.output["suggested_next_actions"] == ["APPROVE_CONCEPT"]
    assert result.output["attention_items"] == ["Choose a concept for production."]


def test_fake_worker_routes_each_non_commerce_demo_contract(monkeypatch) -> None:
    cases = (
        ("creative.creative_concept_set", "creative_output"),
        ("production.production_plan", "production_output"),
        ("intelligence.intelligence_report", "intelligence_output"),
    )
    for contract, function_name in cases:
        monkeypatch.setattr(
            researcher_worker,
            function_name,
            lambda _invocation, value=contract: {"contract": value},
        )
        result = researcher_worker._fake_demo_result(SimpleNamespace(output_contract_key=contract))
        assert result.output == {"contract": contract}
    with pytest.raises(RuntimeError, match="does not support"):
        researcher_worker._fake_demo_result(SimpleNamespace(output_contract_key="unknown"))


def test_fake_worker_supports_grounded_research_contract() -> None:
    evidence_id = "00000000-0000-0000-0000-000000000201"
    invocation = SimpleNamespace(
        output_contract_key="research.research_snapshot",
        untrusted_evidence=(
            SimpleNamespace(
                evidence_snapshot_id=evidence_id,
                block_index=0,
                block_digest="sha256:" + "a" * 64,
            ),
        ),
    )
    result = researcher_worker._fake_demo_result(invocation)
    assert isinstance(result.output, dict)
    citation = result.output["findings"][0]["citations"][0]
    assert citation["evidence_snapshot_id"] == evidence_id
    assert result.provider_response_id == "local-demo-research.research_snapshot"


def test_installed_worker_demo_outputs_cover_the_complete_fake_agent_sequence() -> None:
    asset_id = "00000000-0000-0000-0000-000000000301"
    research_snapshot_id = "00000000-0000-0000-0000-000000000302"
    concept = creative_output(
        SimpleNamespace(
            capability_context={
                "research_findings": [{"key": "audience_language"}],
                "product_claim_refs": [{"key": "durable_claim", "text": "Durable."}],
                "research_snapshot_id": research_snapshot_id,
                "available_assets": [{"asset_id": asset_id}],
                "strategy_request": {
                    "concept_count": 5,
                    "channel_intent": "ORGANIC_SHORT_FORM",
                },
            }
        )
    )
    concepts = cast(list[dict[str, object]], concept["concepts"])
    assert len(concepts) == 5
    assert {item["channel_intent"] for item in concepts} == {"ORGANIC_SHORT_FORM"}

    plan = production_output(
        SimpleNamespace(
            capability_context={
                "selected_assets": [{"asset_id": asset_id}],
                "concept_scene_keys": ["hook", "proof", "action"],
            }
        )
    )
    scenes = cast(list[dict[str, object]], plan["scenes"])
    sources = [
        cast(list[dict[str, object]], scene["shots"])[0]["source_strategy"] for scene in scenes
    ]
    assert sources == [
        "GENERATE_IMAGE",
        "GENERATE_VIDEO",
        "MANUAL_CAPTURE",
    ]

    unmatched = intelligence_output(SimpleNamespace(capability_context={}))
    assert unmatched["next_experiments"] == []
    matched = intelligence_output(
        SimpleNamespace(
            capability_context={
                "performance_comparisons": [
                    {
                        "id": "comparison-1",
                        "comparison_window": "24h",
                        "metric_key": "HOOK_HOLD_RATE",
                    }
                ]
            }
        )
    )
    assert len(matched["next_experiments"]) == 1
