# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,assignment"

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.orchestration.application import (
    CanonicalCycleState,
    CreativeCycleService,
    CyclePreflight,
    CycleReadinessEngine,
    require_cycle_mutation,
)
from creative_marketer.orchestration.domain import (
    CreativeCycle,
    CreativeCycleStep,
    CycleConflict,
    CycleNotFound,
    CycleNotReady,
    CyclePermissionDenied,
    CycleStage,
    CycleStatus,
    CycleTransition,
    ReadinessState,
    SupervisorAction,
    SupervisorContextManifest,
    SupervisorReport,
)


def context() -> ExecutionContext:
    user_id = uuid4()
    return ExecutionContext(
        uuid4(),
        Actor(ActorKind.USER, user_id),
        user_id,
        MembershipRole.OWNER,
        MembershipStatus.ACTIVE,
        "test",
        AuthenticationAssurance(datetime.now(UTC), "unit", "explicit"),
    )


def sample_cycle(ctx: ExecutionContext, stage: CycleStage = CycleStage.CREATED) -> CreativeCycle:
    value = CreativeCycle(
        ctx.tenant_id,
        uuid4(),
        ctx.user_id,
        uuid4(),
        "sha256:" + "a" * 64,
    )
    return replace(value, current_stage=stage)


@pytest.mark.parametrize(
    ("stage", "action"),
    [
        (CycleStage.AWAITING_CONCEPT_APPROVAL, SupervisorAction.APPROVE_CONCEPT),
        (CycleStage.AWAITING_PRODUCTION_APPROVAL, SupervisorAction.APPROVE_PRODUCTION_PLAN),
        (CycleStage.AWAITING_FINAL_CREATIVE_APPROVAL, SupervisorAction.REVIEW_FINAL_CREATIVE),
        (CycleStage.AWAITING_PUBLICATION_INPUT, SupervisorAction.PREPARE_PUBLICATION),
        (CycleStage.AWAITING_PUBLICATION_APPROVAL, SupervisorAction.APPROVE_PUBLICATION),
        (CycleStage.MEASURING, SupervisorAction.WAIT_FOR_MEASUREMENT),
        (CycleStage.AWAITING_EXPERIMENT_DECISION, SupervisorAction.REVIEW_EXPERIMENT),
    ],
)
def test_cycle_readiness_explains_every_human_checkpoint(stage, action) -> None:
    ctx = context()
    readiness = CycleReadinessEngine().cycle(CanonicalCycleState(sample_cycle(ctx, stage)))
    assert readiness.state is ReadinessState.WAITING
    assert readiness.allowed_actions == (action,)
    assert readiness.requirements[0].message


def test_cycle_readiness_handles_recovery_completion_and_active_work() -> None:
    ctx = context()
    base = sample_cycle(ctx, CycleStage.RESEARCHING)
    assert CycleReadinessEngine().cycle(CanonicalCycleState(base)).state is ReadinessState.READY
    recovery = replace(base, status=CycleStatus.NEEDS_RECOVERY)
    value = CycleReadinessEngine().cycle(CanonicalCycleState(recovery))
    assert value.state is ReadinessState.BLOCKED
    assert value.allowed_actions == (SupervisorAction.RECOVER_AGENT_RUN,)
    complete = replace(base, status=CycleStatus.COMPLETED, current_stage=CycleStage.COMPLETED)
    assert (
        CycleReadinessEngine().cycle(CanonicalCycleState(complete)).state
        is ReadinessState.COMPLETED
    )
    assembly = CanonicalCycleState(
        sample_cycle(ctx, CycleStage.ASSEMBLING_FINAL_CREATIVE),
        assembly_input_required=True,
    )
    assembly_readiness = CycleReadinessEngine().cycle(assembly)
    assert assembly_readiness.state is ReadinessState.WAITING
    assert assembly_readiness.allowed_actions == (SupervisorAction.PREPARE_FINAL_ASSEMBLY,)


def test_cycle_mutation_requires_active_owner_or_admin() -> None:
    ctx = context()
    with pytest.raises(CyclePermissionDenied):
        require_cycle_mutation(replace(ctx, membership_role=MembershipRole.MEMBER))
    with pytest.raises(CyclePermissionDenied):
        require_cycle_mutation(replace(ctx, membership_status=MembershipStatus.INACTIVE))


class MemoryRepository:
    def __init__(self, ctx: ExecutionContext) -> None:
        self.ctx = ctx
        self.cycle: CreativeCycle | None = None
        self.transition_values: list[CycleTransition] = []
        self.step_values: list[CreativeCycleStep] = []
        self.manifests: list[SupervisorContextManifest] = []
        self.reports: list[SupervisorReport] = []

    async def preflight(self, product_id):
        return CyclePreflight(True, 96, uuid4(), "sha256:" + "b" * 64, 1, True)

    async def active_for_product(self, product_id, *, for_update=False):
        if self.cycle and self.cycle.status in {
            CycleStatus.ACTIVE,
            CycleStatus.BLOCKED,
            CycleStatus.NEEDS_RECOVERY,
        }:
            return self.cycle
        return None

    async def get(self, cycle_id, *, for_update=False):
        return self.cycle if self.cycle and self.cycle.id == cycle_id else None

    async def add(self, cycle):
        self.cycle = cycle

    async def update(self, cycle, *, expected_version):
        assert self.cycle is not None
        if self.cycle.cycle_version != expected_version:
            return False
        self.cycle = cycle
        return True

    async def add_transition(self, value):
        self.transition_values.append(value)

    async def add_step(self, value):
        self.step_values.append(value)

    async def step(self, cycle_id, step_key):
        return next((item for item in self.step_values if item.step_key == step_key), None)

    async def steps(self, cycle_id):
        return tuple(self.step_values)

    async def transitions(self, cycle_id):
        return tuple(self.transition_values)

    async def canonical_state(self, cycle):
        return CanonicalCycleState(cycle)

    async def add_supervisor_manifest(self, value):
        self.manifests.append(value)

    async def add_supervisor_report(self, value):
        self.reports.append(value)

    async def latest_supervisor_report(self, cycle_id):
        return self.reports[-1] if self.reports else None


class MemoryUow:
    def __init__(self, repository):
        self.cycles = repository
        self.audit = SimpleNamespace(append=self.append_audit)
        self.outbox = SimpleNamespace(append=self.append_event)
        self.audits = []
        self.events = []
        self.commits = 0

    async def append_audit(self, value):
        self.audits.append(value)

    async def append_event(self, value):
        self.events.append(value)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_service_start_transition_report_get_and_cancel_are_auditable() -> None:
    ctx = context()
    repository = MemoryRepository(ctx)
    uow = MemoryUow(repository)
    snapshot = SimpleNamespace(id=uuid4(), digest="sha256:" + "c" * 64, source_revision=4)
    catalog = SimpleNamespace(
        get_workspace=lambda *_args: None,
        create_snapshot=lambda *_args: None,
    )

    async def workspace(*_args):
        return SimpleNamespace(latest_snapshot=snapshot, brief=SimpleNamespace(revision=4))

    catalog.get_workspace = workspace
    supervisor_run = SimpleNamespace(
        id=uuid4(),
        status=SimpleNamespace(value="PENDING"),
        agent_type="supervisor",
    )

    async def request_supervisor(*_args, **_kwargs):
        return supervisor_run

    service = CreativeCycleService(
        lambda _tenant: uow,
        catalog,
        SimpleNamespace(request_supervisor=request_supervisor),
    )
    started = await service.start(ctx, uuid4())
    assert repository.cycle is started
    assert uow.events[-1].event_type == "orchestration.cycle.created.v1"

    advanced = await service.reconcile(ctx, started.id)
    assert advanced.current_stage is CycleStage.CHECKING_READINESS
    state, readiness, steps, transitions, report = await service.get(ctx, started.id)
    assert state.cycle.id == started.id and readiness.state is ReadinessState.READY
    assert not steps and len(transitions) == 2 and report is None
    assert await service.active(ctx, started.product_id) is advanced

    run = await service.create_supervisor_report(ctx, started.id)
    assert run.id == supervisor_run.id
    assert repository.manifests[-1].cycle_id == started.id

    cancelled = await service.cancel(ctx, started.id)
    assert cancelled.status is CycleStatus.CANCELLED
    assert await service.cancel(ctx, started.id) is cancelled
    assert any(item.action == "orchestration.cycle.cancelled" for item in uow.audits)


@pytest.mark.parametrize(
    ("stage", "changes", "expected"),
    [
        (CycleStage.CREATED, {}, (CycleStage.CHECKING_READINESS, None)),
        (CycleStage.CHECKING_READINESS, {}, (CycleStage.RESEARCHING, None)),
        (
            CycleStage.RESEARCHING,
            {"research_snapshot_id": uuid4()},
            (CycleStage.CREATIVE_STRATEGY, None),
        ),
        (CycleStage.RESEARCHING, {}, (None, "research")),
        (
            CycleStage.RESEARCHING,
            {"research_run": SimpleNamespace(status=SimpleNamespace(value="FAILED"))},
            (CycleStage.FAILED, None),
        ),
        (
            CycleStage.CREATIVE_STRATEGY,
            {"concept_set_id": uuid4()},
            (CycleStage.AWAITING_CONCEPT_APPROVAL, None),
        ),
        (CycleStage.CREATIVE_STRATEGY, {}, (None, "creative")),
        (
            CycleStage.CREATIVE_STRATEGY,
            {"creative_run": SimpleNamespace(status=SimpleNamespace(value="FAILED"))},
            (CycleStage.FAILED, None),
        ),
        (
            CycleStage.AWAITING_CONCEPT_APPROVAL,
            {"approved_concept_id": uuid4()},
            (CycleStage.PRODUCTION_PLANNING, None),
        ),
        (CycleStage.PRODUCTION_PLANNING, {"approved_concept_id": uuid4()}, (None, "producer")),
        (
            CycleStage.PRODUCTION_PLANNING,
            {"producer_run": SimpleNamespace(status=SimpleNamespace(value="FAILED"))},
            (CycleStage.FAILED, None),
        ),
        (
            CycleStage.AWAITING_PRODUCTION_APPROVAL,
            {"production_plan_approved": True},
            (CycleStage.GENERATING_MEDIA, None),
        ),
        (
            CycleStage.GENERATING_MEDIA,
            {"generation_outcome_unknown": True},
            (None, "needs_recovery"),
        ),
        (CycleStage.GENERATING_MEDIA, {"generation_failed": True}, (CycleStage.FAILED, None)),
        (
            CycleStage.GENERATING_MEDIA,
            {"generation_complete": True},
            (CycleStage.ASSEMBLING_FINAL_CREATIVE, None),
        ),
        (
            CycleStage.ASSEMBLING_FINAL_CREATIVE,
            {"final_creative_id": uuid4()},
            (CycleStage.AWAITING_FINAL_CREATIVE_APPROVAL, None),
        ),
        (
            CycleStage.ASSEMBLING_FINAL_CREATIVE,
            {"assembly_failed": True},
            (CycleStage.FAILED, None),
        ),
        (CycleStage.ASSEMBLING_FINAL_CREATIVE, {}, (None, "assembly")),
        (
            CycleStage.AWAITING_FINAL_CREATIVE_APPROVAL,
            {"final_creative_approved": True},
            (CycleStage.AWAITING_PUBLICATION_INPUT, None),
        ),
        (
            CycleStage.AWAITING_PUBLICATION_INPUT,
            {"publication_draft_id": uuid4()},
            (CycleStage.AWAITING_PUBLICATION_APPROVAL, None),
        ),
        (
            CycleStage.AWAITING_PUBLICATION_APPROVAL,
            {"publication_approved": True},
            (CycleStage.PUBLISHING, None),
        ),
        (CycleStage.PUBLISHING, {"publication_outcome_unknown": True}, (None, "needs_recovery")),
        (CycleStage.PUBLISHING, {"publication_failed": True}, (CycleStage.FAILED, None)),
        (CycleStage.PUBLISHING, {"publication_id": uuid4()}, (CycleStage.MEASURING, None)),
        (
            CycleStage.PUBLISHING,
            {"publication_approved": True, "publication_draft_id": uuid4()},
            (None, "publishing"),
        ),
        (
            CycleStage.MEASURING,
            {"performance_snapshot_id": uuid4()},
            (CycleStage.INTELLIGENCE, None),
        ),
        (CycleStage.MEASURING, {"publication_id": uuid4()}, (None, "measurement")),
        (
            CycleStage.INTELLIGENCE,
            {"intelligence_report_id": uuid4(), "experiment_proposal_id": uuid4()},
            (CycleStage.AWAITING_EXPERIMENT_DECISION, None),
        ),
        (
            CycleStage.INTELLIGENCE,
            {"intelligence_report_id": uuid4()},
            (CycleStage.COMPLETED, None),
        ),
        (CycleStage.INTELLIGENCE, {}, (None, "intelligence")),
        (
            CycleStage.INTELLIGENCE,
            {"intelligence_run": SimpleNamespace(status=SimpleNamespace(value="FAILED"))},
            (CycleStage.FAILED, None),
        ),
        (
            CycleStage.AWAITING_EXPERIMENT_DECISION,
            {"experiment_decided": True},
            (CycleStage.COMPLETED, None),
        ),
    ],
)
def test_reconciler_has_one_deterministic_next_step(stage, changes, expected) -> None:
    ctx = context()
    cycle = sample_cycle(ctx, stage)
    state = CanonicalCycleState(cycle, **changes)
    service = CreativeCycleService(SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    assert service._next(state) == expected


class Runtime:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.contexts: list[ExecutionContext] = []

    async def _run(self, name, execution_context, idempotency_key):
        self.calls.append(name)
        self.contexts.append(execution_context)
        return SimpleNamespace(id=uuid4(), idempotency_key=idempotency_key)

    async def request_researcher(self, execution_context, **kwargs):
        return await self._run("research", execution_context, kwargs["idempotency_key"])

    async def request_creative_strategist(self, execution_context, **kwargs):
        return await self._run("creative", execution_context, kwargs["idempotency_key"])

    async def request_producer(self, execution_context, **kwargs):
        return await self._run("producer", execution_context, kwargs["idempotency_key"])

    async def request_intelligence(self, execution_context, **kwargs):
        return await self._run("intelligence", execution_context, kwargs["idempotency_key"])


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["research", "creative", "producer", "intelligence"])
async def test_reconciler_requests_each_agent_only_through_common_runtime(action) -> None:
    ctx = context()
    repository = MemoryRepository(ctx)
    uow = MemoryUow(repository)
    cycle = sample_cycle(ctx, CycleStage.RESEARCHING)
    repository.cycle = cycle
    runtime = Runtime()
    service = CreativeCycleService(lambda _tenant: uow, SimpleNamespace(), runtime)
    state = CanonicalCycleState(
        cycle,
        approved_concept_id=uuid4() if action == "producer" else None,
    )
    updated = await service._start_action(ctx, state, action)
    assert runtime.calls == [action]
    assert runtime.contexts[0].actor == Actor(ActorKind.USER, ctx.user_id)
    assert runtime.contexts[0].correlation_id == ctx.correlation_id
    assert repository.step_values[-1].idempotency_key == f"cycle:{cycle.id}:{action}:v1"
    assert updated.cycle_version == 2

    replay = await service._start_action(ctx, state, action)
    assert replay is cycle
    assert runtime.calls == [action]


@pytest.mark.asyncio
async def test_reconciler_marks_ambiguous_or_unrelated_work_as_needing_recovery() -> None:
    ctx = context()
    repository = MemoryRepository(ctx)
    uow = MemoryUow(repository)
    cycle = sample_cycle(ctx, CycleStage.RESEARCHING)
    repository.cycle = cycle
    service = CreativeCycleService(lambda _tenant: uow, SimpleNamespace(), Runtime())
    recovered = await service._start_action(ctx, CanonicalCycleState(cycle), "needs_recovery")
    assert recovered.status is CycleStatus.NEEDS_RECOVERY
    assert recovered.failure_code == "AMBIGUOUS_EXTERNAL_OUTCOME"


@pytest.mark.asyncio
async def test_service_fails_closed_for_missing_cycles_and_incomplete_start() -> None:
    ctx = context()
    repository = MemoryRepository(ctx)
    uow = MemoryUow(repository)
    service = CreativeCycleService(lambda _tenant: uow, SimpleNamespace(), Runtime())

    for operation in (
        service.get(ctx, uuid4()),
        service.reconcile(ctx, uuid4()),
        service.cancel(ctx, uuid4()),
    ):
        with pytest.raises(CycleNotFound):
            await operation

    async def incomplete(_product_id):
        return CyclePreflight(True, 20, None, None, 0, True)

    repository.preflight = incomplete
    with pytest.raises(CycleNotReady):
        await service.start(ctx, uuid4())


@pytest.mark.asyncio
async def test_start_refreshes_stale_snapshot_and_rejects_an_active_cycle() -> None:
    ctx = context()
    repository = MemoryRepository(ctx)
    uow = MemoryUow(repository)
    fresh = SimpleNamespace(id=uuid4(), digest="sha256:" + "f" * 64, source_revision=2)

    async def workspace(*_args):
        return SimpleNamespace(
            latest_snapshot=SimpleNamespace(
                id=uuid4(), digest="sha256:" + "e" * 64, source_revision=1
            ),
            brief=SimpleNamespace(revision=2),
        )

    async def snapshot(*_args):
        return fresh

    catalog = SimpleNamespace(get_workspace=workspace, create_snapshot=snapshot)
    service = CreativeCycleService(lambda _tenant: uow, catalog, Runtime())
    created = await service.start(ctx, uuid4())
    assert created.product_snapshot_id == fresh.id

    with pytest.raises(CycleConflict):
        await service.start(ctx, created.product_id)


@pytest.mark.asyncio
async def test_start_launches_ids_only_cycle_workflow_after_commit() -> None:
    ctx = context()
    repository = MemoryRepository(ctx)
    uow = MemoryUow(repository)
    snapshot = SimpleNamespace(id=uuid4(), digest="sha256:" + "f" * 64, source_revision=1)

    async def workspace(*_args):
        return SimpleNamespace(latest_snapshot=snapshot, brief=SimpleNamespace(revision=1))

    class Workflows:
        def __init__(self):
            self.calls = []

        async def start_cycle(self, tenant_id, cycle_id, correlation_id):
            self.calls.append((tenant_id, cycle_id, correlation_id, uow.commits))

    workflows = Workflows()
    service = CreativeCycleService(
        lambda _tenant: uow,
        SimpleNamespace(get_workspace=workspace),
        Runtime(),
        cycle_workflows=workflows,
    )
    cycle = await service.start(ctx, uuid4())

    assert workflows.calls == [(ctx.tenant_id, cycle.id, ctx.correlation_id, 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["assembly", "publishing", "measurement"])
async def test_reconciler_starts_each_existing_workflow_once(action) -> None:
    ctx = context()
    repository = MemoryRepository(ctx)
    uow = MemoryUow(repository)
    cycle = sample_cycle(ctx, CycleStage.ASSEMBLING_FINAL_CREATIVE)
    repository.cycle = cycle
    calls = []

    class Assembly:
        async def readiness(self, execution_context, plan_id):
            return SimpleNamespace(ready=True)

        async def create_plan(self, execution_context, plan_id):
            calls.append(("assembly", execution_context.tenant_id, plan_id))
            return SimpleNamespace(
                plan=SimpleNamespace(id=uuid4()), job=SimpleNamespace(id=uuid4())
            )

    class Workflows:
        async def start_publication(self, tenant_id, draft_id, correlation_id):
            calls.append(("publishing", tenant_id, draft_id, correlation_id))

        async def start_measurement(self, tenant_id, publication_id, correlation_id):
            calls.append(("measurement", tenant_id, publication_id, correlation_id))

    plan_id, draft_id, publication_id = uuid4(), uuid4(), uuid4()
    service = CreativeCycleService(
        lambda _tenant: uow,
        SimpleNamespace(),
        Runtime(),
        Assembly(),
        None,
        Workflows(),
        Workflows(),
    )
    state = CanonicalCycleState(
        cycle,
        production_plan_id=plan_id,
        publication_draft_id=draft_id,
        publication_id=publication_id,
    )

    await service._start_action(ctx, state, action)
    await service._start_action(ctx, state, action)

    assert [item[0] for item in calls] == [action]
    assert repository.step_values[-1].step_key == action
    assert repository.step_values[-1].workflow_ref


@pytest.mark.asyncio
async def test_reconciler_waits_for_final_assembly_inputs_without_starting_a_step() -> None:
    ctx = context()
    repository = MemoryRepository(ctx)
    uow = MemoryUow(repository)
    cycle = sample_cycle(ctx, CycleStage.ASSEMBLING_FINAL_CREATIVE)
    repository.cycle = cycle

    class Assembly:
        async def readiness(self, execution_context, plan_id):
            return SimpleNamespace(ready=False)

        async def create_plan(self, execution_context, plan_id):
            raise AssertionError("an unready assembly must not start")

    service = CreativeCycleService(
        lambda _tenant: uow,
        SimpleNamespace(),
        Runtime(),
        Assembly(),
    )
    result = await service._start_action(
        ctx,
        CanonicalCycleState(cycle, production_plan_id=uuid4()),
        "assembly",
    )
    assert result is cycle
    assert repository.step_values == []
