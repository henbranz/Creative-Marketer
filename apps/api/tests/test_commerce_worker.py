# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

import ast
import asyncio
from dataclasses import fields
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

import creative_marketer_api.commerce_temporal as commerce_temporal
import creative_marketer_api.commerce_worker as commerce_worker
from creative_marketer.commerce.execution import CommerceWorkloadIdentity
from creative_marketer.commerce.gateway_composition import CommerceGatewayFactory
from creative_marketer.commerce.provider import FakeCommerceProvider, MutationDisposition
from creative_marketer.workflow_orchestration.contracts import (
    CommerceActionWorkflowInput,
    CommerceSyncWorkflowInput,
)
from creative_marketer_api.commerce_worker import commerce_workload
from creative_marketer_api.config import Settings
from scripts.bootstrap_commerce_agent import (
    commerce_configuration,
    commerce_execution_configuration,
)

DATABASE = "postgresql+psycopg://runtime:runtime@localhost:5432/creative_marketer"


def test_commerce_workload_identity_is_deterministic_and_deployed_identity_fails_closed() -> None:
    first = commerce_workload(Settings(database_url=DATABASE))
    second = commerce_workload(Settings(database_url=DATABASE))
    assert first == second
    assert first.workload_id == "local-commerce-worker"
    assert not hasattr(first, "membership_role")
    actor = uuid4()
    explicit = commerce_workload(
        Settings(
            database_url=DATABASE,
            commerce_workload_actor_id=actor,
            commerce_workload_id="local-explicit-commerce",
        )
    )
    assert explicit.actor_id == actor
    with pytest.raises(ValueError, match="invalid"):
        CommerceWorkloadIdentity(actor, " ", "test")
    for values in (
        {},
        {"commerce_workload_actor_id": actor},
        {"commerce_workload_id": "issued-commerce-worker"},
        {
            "commerce_workload_actor_id": actor,
            "commerce_workload_id": "local-commerce-worker",
        },
        {
            "commerce_workload_actor_id": actor,
            "commerce_workload_id": "placeholder-commerce-worker",
        },
        {
            "commerce_workload_actor_id": UUID(int=0),
            "commerce_workload_id": "issued-commerce-worker",
        },
    ):
        with pytest.raises(RuntimeError, match="deployment-issued"):
            commerce_workload(Settings(app_env="production", database_url=DATABASE, **values))
    deployed = commerce_workload(
        Settings(
            app_env="production",
            database_url=DATABASE,
            commerce_workload_actor_id=actor,
            commerce_workload_id="issued-commerce-worker",
        )
    )
    assert deployed.actor_id == actor


@pytest.mark.asyncio
async def test_fake_unknown_operation_reconstructs_after_worker_restart_exactly_once() -> None:
    store = "fake-store-restart"
    operation = "op_" + uuid4().hex
    first = FakeCommerceProvider()
    first.seed_store(store)
    first.next_refund_disposition = MutationDisposition.OUTCOME_UNKNOWN
    unknown = await first.submit_refund(
        external_store_id=store,
        external_order_id="fake-order-paid",
        amount=Decimal("10"),
        currency="USD",
        idempotency_key=operation,
    )
    assert unknown.disposition is MutationDisposition.OUTCOME_UNKNOWN

    restarted = FakeCommerceProvider()
    effect = {
        "kind": "REFUND",
        "store": store,
        "order": "fake-order-paid",
        "amount": Decimal("10"),
        "currency": "USD",
    }
    restarted.restore_unknown_operation(unknown.operation_id, effect)
    confirmed = await restarted.get_operation_status(store, unknown.operation_id)
    replay = await restarted.get_operation_status(store, unknown.operation_id)
    assert confirmed == replay
    assert confirmed.disposition is MutationDisposition.ACCEPTED
    paid = next(v for v in restarted.orders[store] if v.external_order_id == "fake-order-paid")
    assert paid.payment_state.value == "PARTIALLY_REFUNDED"
    with pytest.raises(ValueError, match="does not match"):
        restarted.restore_unknown_operation("wrong-operation", effect)
    scaled = FakeCommerceProvider()
    scaled.restore_unknown_operation(
        unknown.operation_id, {**effect, "amount": Decimal("10.000000000")}
    )
    assert (
        await scaled.get_operation_status(store, unknown.operation_id)
    ).disposition is MutationDisposition.ACCEPTED


def test_commerce_worker_is_dedicated_fake_only_and_agent_model_has_zero_tools() -> None:
    source = Path("src/creative_marketer_api/commerce_worker.py").read_text()
    assert "CommerceSyncWorkflow" in source and "CommerceActionWorkflow" in source
    assert "MediaProductionWorkflow" not in source
    assert "PublicationWorkflow" not in source
    assert "OpenAI" not in source and "Shopify" not in source
    configuration = commerce_configuration()
    assert configuration.run_budget_policy.max_tool_calls == 0
    assert configuration.allowed_tool_keys == ()
    execution = commerce_execution_configuration()
    assert execution.run_budget_policy.max_model_calls == 0
    assert set(execution.allowed_tool_keys) == {
        "commerce.inventory.adjust",
        "commerce.refund.submit",
        "commerce.operation.status",
    }


def test_commerce_mutations_and_temporal_payloads_remain_narrow() -> None:
    source_root = Path("src/creative_marketer")
    mutation_calls: list[str] = []
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in {"set_available_to", "submit_refund"}:
                mutation_calls.append(f"{path.as_posix()}:{node.lineno}")
    assert len(mutation_calls) == 2
    assert all("commerce/execution.py" in value for value in mutation_calls)
    assert {value.name for value in fields(CommerceActionWorkflowInput)} == {
        "tenant_id",
        "proposal_id",
        "correlation_id",
        "request_ref",
        "approval_timeout_seconds",
        "approval_fallback_poll_seconds",
        "reconcile_interval_seconds",
        "maximum_reconcile_attempts",
    }
    assert {value.name for value in fields(CommerceSyncWorkflowInput)} == {
        "tenant_id",
        "sync_request_id",
        "connection_id",
        "correlation_id",
        "sync_types",
    }
    frontend = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("../web/src").rglob("*.ts*")
    )
    assert "COMMERCE_WORKLOAD" not in frontend


@pytest.mark.asyncio
async def test_lazy_commerce_temporal_starter_connects_once_and_delegates(monkeypatch) -> None:
    calls: list[object] = []

    async def connect(address, *, namespace):
        calls.append((address, namespace))
        return object()

    class Starter:
        def __init__(self, client):
            calls.append(client)

        async def start_action(self, request):
            calls.append(("action", request))

        async def signal_action(self, request):
            calls.append(("signal", request))

        async def start_sync(self, request):
            calls.append(("sync", request))

    monkeypatch.setattr(commerce_temporal, "connect_client", connect)
    monkeypatch.setattr(commerce_temporal, "TemporalCommerceWorkflowStarter", Starter)
    adapter = commerce_temporal.LazyCommerceWorkflowStarter("temporal:7233", "default")
    tenant_id, proposal_id, correlation_id = (str(uuid4()) for _ in range(3))
    action = CommerceActionWorkflowInput(
        tenant_id, proposal_id, correlation_id, "tool-request://" + uuid4().hex
    )
    sync = CommerceSyncWorkflowInput(
        tenant_id, str(uuid4()), str(uuid4()), correlation_id, ("CATALOG",)
    )
    await adapter.start_action(action)
    await adapter.signal_action(action)
    await adapter.start_sync(sync)

    assert calls[0] == ("temporal:7233", "default")
    assert calls[2:] == [("action", action), ("signal", action), ("sync", sync)]


@pytest.mark.asyncio
async def test_commerce_worker_composes_only_dedicated_fake_activities(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class StopWorker(Exception):
        pass

    async def connect(address, *, namespace):
        observed["connection"] = (address, namespace)
        return object()

    class GatewayFactory:
        def __init__(self, *_args):
            pass

        async def __call__(self):
            return object()

    class Worker:
        def __init__(self, _client, **kwargs):
            observed.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    async def stop():
        raise StopWorker

    monkeypatch.setattr(
        commerce_worker,
        "Settings",
        lambda: Settings(
            database_url=DATABASE,
            temporal_address="temporal:7233",
            temporal_namespace="default",
        ),
    )
    monkeypatch.setattr(commerce_worker, "create_session_factory", lambda _url: object())
    monkeypatch.setattr(commerce_worker, "CommerceGatewayFactory", GatewayFactory)
    monkeypatch.setattr(commerce_worker, "connect_client", connect)
    monkeypatch.setattr(commerce_worker, "Worker", Worker)
    monkeypatch.setattr(asyncio, "Future", lambda: stop())

    with pytest.raises(StopWorker):
        await commerce_worker.run()

    assert observed["connection"] == ("temporal:7233", "default")
    assert observed["task_queue"] == "creative-marketer-commerce"
    assert len(observed["workflows"]) == 2
    assert len(observed["activities"]) == 3


@pytest.mark.asyncio
async def test_commerce_gateway_factory_rejects_non_fake_and_reuses_cached_gateway() -> None:
    invalid = CommerceGatewayFactory(object(), object(), object())
    with pytest.raises(RuntimeError, match="only FakeCommerceProvider"):
        await invalid()

    cached = CommerceGatewayFactory(
        object(),
        object(),
        FakeCommerceProvider(),
    )
    sentinel = object()
    cached._gateway = sentinel  # type: ignore[assignment]
    assert await cached() is sentinel


def test_commerce_worker_main_delegates_to_async_runtime(monkeypatch) -> None:
    observed: list[object] = []
    sentinel = object()
    monkeypatch.setattr(commerce_worker, "run", lambda: sentinel)
    monkeypatch.setattr(asyncio, "run", observed.append)

    commerce_worker.main()

    assert observed == [sentinel]
