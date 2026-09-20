# ruff: noqa: E501
# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,assignment,attr-defined,union-attr"

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

import creative_marketer_api.orchestration_worker as worker_module
from creative_marketer.events.domain import DomainEvent, EventScopeKind, tenant_event
from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.orchestration.domain import CycleStage, CycleStatus
from creative_marketer_api.orchestration_worker import (
    CycleReconciler,
    FakeMeasurementExecutor,
    TrustedContextResolver,
    WakeActiveCreativeCycles,
    _event_bridge_loop,
)


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _Result:
    def __init__(self, values=(), row=None):
        self._values, self._row = values, row

    def scalars(self):
        return _Scalars(self._values)

    def first(self):
        return self._row


class _Session:
    def __init__(self, *, values=(), user_id=None, row=None):
        self.values, self.user_id, self.row = values, user_id, row
        self.execute_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def begin(self):
        return _Transaction()

    async def execute(self, _query, *_args):
        self.execute_count += 1
        return _Result(self.values, self.row)

    async def scalar(self, _query):
        return self.user_id


def _event(tenant_id: UUID, event_type: str = "research.snapshot.created.v1") -> DomainEvent:
    user_id = uuid4()
    context = ExecutionContext(
        tenant_id,
        Actor(ActorKind.USER, user_id),
        user_id,
        MembershipRole.OWNER,
        MembershipStatus.ACTIVE,
        "test",
        AuthenticationAssurance(datetime.now(UTC), "test", "explicit"),
    )
    return tenant_event(
        context,
        event_type=event_type,
        schema_version=1,
        aggregate_type="cycle_wake",
        aggregate_id=uuid4(),
        occurred_at=datetime.now(UTC),
        payload_schema_digest="sha256:" + "a" * 64,
        payload={"id": str(uuid4())},
    )


@pytest.mark.asyncio
async def test_event_wake_is_a_hint_and_signals_every_active_cycle() -> None:
    tenant_id, first, second = uuid4(), uuid4(), uuid4()
    session = _Session(values=(first, second))
    workflows = SimpleNamespace(calls=[])

    async def wake_cycle(tenant, cycle):
        workflows.calls.append((tenant, cycle))

    workflows.wake_cycle = wake_cycle
    handler = WakeActiveCreativeCycles(lambda: session, workflows)
    await handler(_event(tenant_id), SimpleNamespace())
    assert workflows.calls == [(tenant_id, first), (tenant_id, second)]

    with pytest.raises(ValueError, match="unsupported"):
        await handler(_event(tenant_id, "unrelated.event.v1"), SimpleNamespace())
    with pytest.raises(ValueError, match="unsupported"):
        await handler(
            DomainEvent(
                "research.snapshot.created.v1",
                1,
                EventScopeKind.PLATFORM,
                None,
                "cycle_wake",
                uuid4(),
                datetime.now(UTC),
                ActorKind.SYSTEM,
                uuid4(),
                uuid4(),
                {},
                "sha256:" + "b" * 64,
            ),
            SimpleNamespace(),
        )


@pytest.mark.asyncio
async def test_trusted_context_resolver_reloads_current_authority_and_fails_closed() -> None:
    tenant_id, user_id, cycle_id = uuid4(), uuid4(), uuid4()
    row = SimpleNamespace(
        role=MembershipRole.OWNER.value,
        status=MembershipStatus.ACTIVE.value,
        user_status="active",
        tenant_status="active",
    )
    session = _Session(user_id=user_id, row=row)
    resolver = TrustedContextResolver(lambda: session, "test")
    context = await resolver.for_cycle(tenant_id, cycle_id, uuid4())
    assert context.tenant_id == tenant_id
    assert context.user_id == user_id
    assert context.actor.kind is ActorKind.WORKLOAD

    inactive = _Session(
        user_id=user_id, row=SimpleNamespace(**{**row.__dict__, "status": "inactive"})
    )
    with pytest.raises(RuntimeError, match="no longer active"):
        await TrustedContextResolver(lambda: inactive, "test").for_cycle(
            tenant_id, cycle_id, uuid4()
        )

    missing = _Session(user_id=None, row=None)
    with pytest.raises(RuntimeError, match="no longer active"):
        await TrustedContextResolver(lambda: missing, "test").for_publication(
            tenant_id, uuid4(), uuid4()
        )


def _context(tenant_id: UUID) -> ExecutionContext:
    user_id = uuid4()
    return ExecutionContext(
        tenant_id,
        Actor(ActorKind.WORKLOAD, uuid4()),
        user_id,
        MembershipRole.OWNER,
        MembershipStatus.ACTIVE,
        "test",
        AuthenticationAssurance(datetime.now(UTC), "test", "workload"),
    )


@pytest.mark.asyncio
async def test_cycle_and_measurement_activity_adapters_return_ids_only_results() -> None:
    tenant_id, cycle_id, publication_id = uuid4(), uuid4(), uuid4()
    contexts = SimpleNamespace()

    async def for_cycle(*_args):
        return _context(tenant_id)

    async def for_publication(*_args):
        return _context(tenant_id)

    contexts.for_cycle, contexts.for_publication = for_cycle, for_publication
    service = SimpleNamespace()

    async def reconcile(_context_value, value):
        assert value == cycle_id
        return SimpleNamespace(
            id=cycle_id,
            status=CycleStatus.ACTIVE,
            current_stage=CycleStage.RESEARCHING,
        )

    service.reconcile = reconcile
    result = await CycleReconciler(service, contexts).reconcile(tenant_id, cycle_id, uuid4())
    assert result.cycle_id == str(cycle_id)
    assert result.stage == CycleStage.RESEARCHING.value
    assert not result.terminal

    measurement = SimpleNamespace()

    async def collect(_context_value, value, *, checkpoint):
        assert value == publication_id and checkpoint == "schedule-v1:0"
        return SimpleNamespace(id=uuid4())

    measurement.collect = collect
    collected = await FakeMeasurementExecutor(measurement, contexts).collect(
        tenant_id, publication_id, "schedule-v1:0"
    )
    assert collected.publication_id == str(publication_id)
    assert collected.status == "SUCCEEDED"


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer_fails", [False, True])
async def test_event_bridge_claims_only_wake_events_and_records_delivery(
    monkeypatch, consumer_fails
) -> None:
    class StopLoop(Exception):
        pass

    item = SimpleNamespace(event=_event(uuid4()), trace_context={})

    class Publisher:
        instance = None

        def __init__(self, _sessions):
            self.published, self.retried = [], []
            Publisher.instance = self

        async def claim_ready_types(self, _worker, *, event_types, **_kwargs):
            assert set(event_types) == set(worker_module.CYCLE_WAKE_EVENTS)
            return (item,)

        async def mark_published(self, value, *, now):
            self.published.append((value, now))

        async def mark_retryable(self, value, **kwargs):
            self.retried.append((value, kwargs))

    async def consumer(_name, _event_value, _trace):
        if consumer_fails:
            raise RuntimeError("temporal unavailable")

    async def stop_sleep(_seconds):
        raise StopLoop

    monkeypatch.setattr(worker_module, "PostgresPublisherStore", Publisher)
    monkeypatch.setattr(worker_module, "create_session_factory", lambda _url: "publisher-sessions")
    monkeypatch.setattr(worker_module, "ProcessEvent", lambda *_args: consumer)
    monkeypatch.setattr(worker_module.asyncio, "sleep", stop_sleep)
    settings = SimpleNamespace(event_publisher_database_url="postgresql://publisher")
    with pytest.raises(StopLoop):
        await _event_bridge_loop(settings, SimpleNamespace(), SimpleNamespace())
    assert bool(Publisher.instance.retried) is consumer_fails
    assert bool(Publisher.instance.published) is not consumer_fails

    settings.event_publisher_database_url = None
    with pytest.raises(RuntimeError, match="EVENT_PUBLISHER_DATABASE_URL"):
        await _event_bridge_loop(settings, SimpleNamespace(), SimpleNamespace())


@pytest.mark.asyncio
async def test_orchestration_worker_rejects_non_fake_environments(monkeypatch) -> None:
    monkeypatch.setattr(
        worker_module,
        "Settings",
        lambda: SimpleNamespace(app_env="production"),
    )
    with pytest.raises(RuntimeError, match="fake-provider-only"):
        await worker_module.run()


@pytest.mark.asyncio
async def test_orchestration_worker_composes_ids_only_temporal_boundary(monkeypatch) -> None:
    class StopWorker(Exception):
        pass

    settings = SimpleNamespace(
        app_env="test",
        database_url="postgresql://runtime",
        temporal_address="temporal:7233",
        temporal_namespace="default",
        agent_workload_id="test/orchestration",
    )
    activities = SimpleNamespace(
        reconcile_creative_cycle=object(),
        submit_publication=object(),
        reconcile_publication=object(),
        cancel_publication=object(),
        collect_performance=object(),
    )

    class GatewayFactory:
        def __init__(self, *_args):
            pass

        async def __call__(self):
            return SimpleNamespace()

    class Worker:
        def __init__(self, _client, *, task_queue, workflows, activities):
            assert task_queue == worker_module.ORCHESTRATION_TASK_QUEUE
            assert len(workflows) == 3 and len(activities) == 5

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    async def connect(*_args, **_kwargs):
        return SimpleNamespace()

    async def stop_bridge(*_args):
        raise StopWorker

    monkeypatch.setattr(worker_module, "Settings", lambda: settings)
    monkeypatch.setattr(worker_module, "create_session_factory", lambda _url: "sessions")
    for name in (
        "SqlAlchemyAgentRuntimeUnitOfWorkFactory",
        "SqlAlchemyOrchestrationUnitOfWorkFactory",
        "SqlAlchemyCatalogUnitOfWorkFactory",
        "SqlAlchemyAssemblyUnitOfWorkFactory",
        "SqlAlchemyPublishingUnitOfWorkFactory",
        "SqlAlchemyMeasurementUnitOfWorkFactory",
    ):
        monkeypatch.setattr(worker_module, name, lambda *_args: SimpleNamespace())
    for name in (
        "AgentRunService",
        "ModelRouter",
        "ModelProviderRegistry",
        "ConfiguredWorkloadIdentityProvider",
        "CatalogService",
        "AssemblyService",
        "CreativeCycleService",
        "PublishingService",
        "SqlAlchemyPublicationExecutionAuthority",
        "GovernedPublicationJobExecutor",
        "MeasurementService",
        "FakeMeasurementExecutor",
        "CycleReconciler",
    ):
        monkeypatch.setattr(worker_module, name, lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(
        worker_module,
        "LazyOrchestrationWorkflowCoordinator",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(worker_module, "PublishingGatewayFactory", GatewayFactory)
    monkeypatch.setattr(worker_module, "TemporalActivities", lambda *_args, **_kwargs: activities)
    monkeypatch.setattr(worker_module, "connect_client", connect)
    monkeypatch.setattr(worker_module, "Worker", Worker)
    monkeypatch.setattr(worker_module, "_event_bridge_loop", stop_bridge)
    with pytest.raises(StopWorker):
        await worker_module.run()


def test_orchestration_worker_main_enters_async_runtime(monkeypatch) -> None:
    called = []

    def run_coroutine(value):
        called.append(value)
        value.close()

    monkeypatch.setattr(worker_module.asyncio, "run", run_coroutine)
    worker_module.main()
    assert len(called) == 1
