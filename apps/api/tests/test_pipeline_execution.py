# mypy: disable-error-code="arg-type,attr-defined,no-untyped-call,no-untyped-def"

from types import SimpleNamespace
from uuid import uuid4

import pytest

from creative_marketer.orchestration.domain import (
    PipelineActionMismatch,
    PipelineApprovalRequired,
    PipelineExecutionInvariant,
)
from creative_marketer.orchestration.execution import (
    PipelineActionExecutor,
    PipelineExecutionOutcome,
    pipeline_approval_phrase,
)
from creative_marketer.orchestration.pipeline import (
    EXECUTION_BEHAVIOR_REGISTRY,
    AssemblyPipelineState,
    CreativePipelineState,
    MediaPipelineState,
    NextPipelineAction,
    PipelineAction,
    PipelineLocator,
    PipelineStage,
    ProducerPipelineState,
    ResearchPipelineState,
)
from tests.test_agent_runtime_application import context


def action_state(
    action: PipelineAction,
    *,
    research: ResearchPipelineState = ResearchPipelineState.SUCCEEDED_CURRENT,
    creative: CreativePipelineState = CreativePipelineState.NOT_STARTED,
    producer: ProducerPipelineState = ProducerPipelineState.NOT_STARTED,
    research_run_id=None,
    research_snapshot_id=None,
    creative_run_id=None,
    concept_id=None,
    creative_revalidation_id=None,
    producer_run_id=None,
    production_plan_id=None,
    assembly_plan_id=None,
    final_creative_id=None,
    assembly=AssemblyPipelineState.NOT_STARTED,
    media=MediaPipelineState.NOT_OBSERVED,
    stranded_media_start_job_ids=(),
    retryable_unknown_media_job_ids=(),
) -> NextPipelineAction:
    definition = EXECUTION_BEHAVIOR_REGISTRY[action]
    return NextPipelineAction(
        stage=PipelineStage.RESEARCH,
        action=action,
        blocking_reason=None,
        costs_money=definition.provider_cost,
        human_approval_required=definition.explicit_approval_required,
        provider_execution_permitted=definition.provider_execution_permitted,
        research_state=research,
        creative_state=creative,
        producer_state=producer,
        research_run_id=research_run_id,
        research_snapshot_id=research_snapshot_id,
        creative_run_id=creative_run_id,
        concept_id=concept_id,
        creative_revalidation_id=creative_revalidation_id,
        producer_run_id=producer_run_id,
        production_plan_id=production_plan_id,
        assembly_plan_id=assembly_plan_id,
        final_creative_id=final_creative_id,
        assembly_state=assembly,
        media_state=media,
        stranded_media_start_job_ids=stranded_media_start_job_ids,
        retryable_unknown_media_job_ids=retryable_unknown_media_job_ids,
    )


class State:
    def __init__(self, *values: NextPipelineAction) -> None:
        self.values = list(values)
        self.locators: list[PipelineLocator] = []

    async def resolve_next_action(self, _context, locator):
        self.locators.append(locator)
        return self.values[min(len(self.locators) - 1, len(self.values) - 1)]


class Agents:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.runs: dict[object, object] = {}

    async def get_run(self, _context, run_id):
        self.calls.append(("get_run", {"run_id": run_id}))
        return self.runs[run_id]

    async def _request(self, name: str, values: dict[str, object]):
        self.calls.append((name, values))
        return SimpleNamespace(id=uuid4())

    async def request_researcher(self, _context, **values):
        return await self._request("request_researcher", values)

    async def request_creative_strategist(self, _context, **values):
        return await self._request("request_creative_strategist", values)

    async def request_creative_replacement(self, _context, **values):
        return await self._request("request_creative_replacement", values)

    async def request_producer(self, _context, **values):
        return await self._request("request_producer", values)

    async def request_producer_replacement(self, _context, **values):
        return await self._request("request_producer_replacement", values)


class Creative:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def revalidate(self, _context, concept_id):
        self.calls.append(concept_id)
        return SimpleNamespace(id=uuid4())


class Production:
    def __init__(self, feedback: str | None = None) -> None:
        self.feedback = feedback
        self.retry_calls: list[tuple[object, object]] = []
        self.stranded_calls: list[tuple[object, object]] = []
        self.ambiguity_calls: list[tuple[object, object, object]] = []

    async def get_plan(self, _context, _plan_id):
        return SimpleNamespace(decision=SimpleNamespace(rejection_feedback=self.feedback))

    async def retry_after_spend_cap_increase(self, _context, plan_id, *, transition_id):
        self.retry_calls.append((plan_id, transition_id))
        return (SimpleNamespace(id=uuid4()),)

    async def recover_stranded_media_start(self, _context, plan_id, *, job_ids):
        self.stranded_calls.append((plan_id, job_ids))
        return (SimpleNamespace(id=job_ids[0]),)

    async def abandon_unknown_media_and_retry(self, _context, plan_id, *, job_ids, transition_id):
        self.ambiguity_calls.append((plan_id, job_ids, transition_id))
        return (SimpleNamespace(id=job_ids[0]),)


class Assembly:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def create_plan(self, _context, production_plan_id):
        self.calls.append(production_plan_id)
        return SimpleNamespace(plan=SimpleNamespace(id=uuid4()))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "outcome"),
    [
        (PipelineAction.WAIT_FOR_RESEARCH, PipelineExecutionOutcome.WAITING),
        (PipelineAction.APPROVE_CREATIVE, PipelineExecutionOutcome.HUMAN_ACTION_REQUIRED),
        (PipelineAction.RETRY_RESEARCH, PipelineExecutionOutcome.RECOVERY_REQUIRED),
        (PipelineAction.READY_FOR_GENERATION, PipelineExecutionOutcome.COMPLETE),
    ],
)
async def test_passive_actions_are_side_effect_free(action, outcome) -> None:
    ctx = context(uuid4())
    state = State(action_state(action))
    agents = Agents()
    creative = Creative()

    result = await PipelineActionExecutor(state, agents, creative, Production()).execute(
        ctx,
        PipelineLocator(uuid4()),
        expected_action=action,
    )

    assert result.outcome is outcome
    assert result.after is None
    assert result.provider_execution_occurred is False
    assert not agents.calls and not creative.calls


@pytest.mark.asyncio
async def test_action_mismatch_and_missing_paid_approval_fail_before_mutation() -> None:
    ctx = context(uuid4())
    current = action_state(PipelineAction.RUN_RESEARCH, research=ResearchPipelineState.NOT_STARTED)
    agents = Agents()
    creative = Creative()

    with pytest.raises(PipelineActionMismatch):
        await PipelineActionExecutor(State(current), agents, creative, Production()).execute(
            ctx,
            PipelineLocator(uuid4()),
            expected_action=PipelineAction.RUN_CREATIVE,
        )
    with pytest.raises(PipelineApprovalRequired):
        await PipelineActionExecutor(State(current), agents, creative, Production()).execute(
            ctx,
            PipelineLocator(uuid4()),
            expected_action=PipelineAction.RUN_RESEARCH,
            explicit_approval="wrong-action-token",
        )

    assert not agents.calls and not creative.calls


@pytest.mark.asyncio
async def test_paid_research_admission_is_idempotent_and_never_executes_provider() -> None:
    ctx = context(uuid4())
    product_id = uuid4()
    before = action_state(
        PipelineAction.RUN_RESEARCH,
        research=ResearchPipelineState.NOT_STARTED,
    )
    after = action_state(
        PipelineAction.WAIT_FOR_RESEARCH,
        research=ResearchPipelineState.PENDING,
        research_run_id=uuid4(),
    )
    state = State(before, after)
    agents = Agents()

    result = await PipelineActionExecutor(state, agents, Creative(), Production()).execute(
        ctx,
        PipelineLocator(product_id),
        expected_action=PipelineAction.RUN_RESEARCH,
        explicit_approval=pipeline_approval_phrase(PipelineAction.RUN_RESEARCH),
    )

    name, values = agents.calls[0]
    assert name == "request_researcher"
    assert values["product_id"] == product_id
    assert str(values["idempotency_key"]).startswith("pipeline:run_research:")
    assert result.outcome is PipelineExecutionOutcome.EXECUTED
    assert result.locator.research_run_id == result.resource_id
    assert result.locator.use_latest_when_unbound is False
    assert result.provider_execution_occurred is False
    assert state.locators[1] == result.locator


@pytest.mark.asyncio
async def test_free_creative_revalidation_needs_no_spend_approval() -> None:
    ctx = context(uuid4())
    concept_id = uuid4()
    before = action_state(
        PipelineAction.REVALIDATE_CREATIVE,
        creative=CreativePipelineState.STALE_RESEARCH,
        concept_id=concept_id,
    )
    after = action_state(
        PipelineAction.RESTRATEGIZE_CREATIVE,
        creative=CreativePipelineState.REQUIRES_RESTRATEGY,
        concept_id=concept_id,
    )
    creative = Creative()

    result = await PipelineActionExecutor(
        State(before, after), Agents(), creative, Production()
    ).execute(
        ctx,
        PipelineLocator(uuid4()),
        expected_action=PipelineAction.REVALIDATE_CREATIVE,
    )

    assert creative.calls == [concept_id]
    assert result.resource_type == "creative_concept_revalidation"
    assert result.provider_execution_occurred is False


@pytest.mark.asyncio
async def test_restrategy_binds_exact_authority_and_clears_old_producer_lineage() -> None:
    ctx = context(uuid4())
    product_id = uuid4()
    research_run_id = uuid4()
    creative_run_id = uuid4()
    concept_id = uuid4()
    revalidation_id = uuid4()
    producer_run_id = uuid4()
    production_plan_id = uuid4()
    before = action_state(
        PipelineAction.RESTRATEGIZE_CREATIVE,
        creative=CreativePipelineState.REQUIRES_RESTRATEGY,
        research_run_id=research_run_id,
        creative_run_id=creative_run_id,
        concept_id=concept_id,
        creative_revalidation_id=revalidation_id,
        producer_run_id=producer_run_id,
        production_plan_id=production_plan_id,
    )
    after = action_state(
        PipelineAction.WAIT_FOR_CREATIVE,
        creative=CreativePipelineState.PENDING,
        research_run_id=research_run_id,
        creative_run_id=uuid4(),
    )
    agents = Agents()
    agents.runs[creative_run_id] = SimpleNamespace(
        input_context_refs=(
            {
                "kind": "strategy_request",
                "concept_count": 3,
                "channel_intent": "ORGANIC_SHORT_FORM",
            },
        )
    )

    result = await PipelineActionExecutor(
        State(before, after), agents, Creative(), Production()
    ).execute(
        ctx,
        PipelineLocator(product_id),
        expected_action=PipelineAction.RESTRATEGIZE_CREATIVE,
        explicit_approval=pipeline_approval_phrase(PipelineAction.RESTRATEGIZE_CREATIVE),
    )

    name, values = agents.calls[-1]
    assert name == "request_creative_strategist"
    assert values["restrategy_of_concept_id"] == concept_id
    assert values["request"].concept_count == 3
    assert result.locator.research_run_id == research_run_id
    assert result.locator.creative_run_id == result.resource_id
    assert result.locator.concept_id is None
    assert result.locator.creative_revalidation_id is None
    assert result.locator.producer_run_id is None
    assert result.locator.production_plan_id is None


@pytest.mark.asyncio
async def test_new_concept_starts_new_producer_lineage_while_upgrade_uses_recovery() -> None:
    ctx = context(uuid4())
    concept_id = uuid4()
    before = action_state(
        PipelineAction.RUN_PRODUCER,
        creative=CreativePipelineState.APPROVED_FOR_PRODUCTION,
        concept_id=concept_id,
    )
    after = action_state(
        PipelineAction.WAIT_FOR_PRODUCER,
        creative=CreativePipelineState.APPROVED_FOR_PRODUCTION,
        producer=ProducerPipelineState.PENDING,
        concept_id=concept_id,
        producer_run_id=uuid4(),
    )
    agents = Agents()
    result = await PipelineActionExecutor(
        State(before, after), agents, Creative(), Production()
    ).execute(
        ctx,
        PipelineLocator(uuid4()),
        expected_action=PipelineAction.RUN_PRODUCER,
        explicit_approval=pipeline_approval_phrase(PipelineAction.RUN_PRODUCER),
    )

    name, values = agents.calls[-1]
    assert name == "request_producer"
    assert values["concept_id"] == concept_id
    assert "recovery_of_run_id" not in values
    assert result.locator.producer_run_id == result.resource_id

    failed_run_id = uuid4()
    upgrade = action_state(
        PipelineAction.UPGRADE_PRODUCER_CONTRACT,
        creative=CreativePipelineState.APPROVED_FOR_PRODUCTION,
        producer=ProducerPipelineState.CONTRACT_UPGRADE_REQUIRED,
        concept_id=concept_id,
        producer_run_id=failed_run_id,
    )
    upgraded = action_state(
        PipelineAction.WAIT_FOR_PRODUCER,
        creative=CreativePipelineState.APPROVED_FOR_PRODUCTION,
        producer=ProducerPipelineState.PENDING,
        concept_id=concept_id,
        producer_run_id=uuid4(),
    )
    upgrade_agents = Agents()
    await PipelineActionExecutor(
        State(upgrade, upgraded), upgrade_agents, Creative(), Production()
    ).execute(
        ctx,
        PipelineLocator(uuid4()),
        expected_action=PipelineAction.UPGRADE_PRODUCER_CONTRACT,
        explicit_approval=pipeline_approval_phrase(PipelineAction.UPGRADE_PRODUCER_CONTRACT),
    )
    name, values = upgrade_agents.calls[-1]
    assert name == "request_producer_replacement"
    assert values["failed_run_id"] == failed_run_id


@pytest.mark.asyncio
async def test_rejected_plan_feedback_is_bound_to_a_new_producer_run() -> None:
    ctx = context(uuid4())
    concept_id = uuid4()
    producer_run_id = uuid4()
    plan_id = uuid4()
    before = action_state(
        PipelineAction.RERUN_PRODUCER,
        creative=CreativePipelineState.APPROVED_FOR_PRODUCTION,
        producer=ProducerPipelineState.PRODUCTION_PLAN_REJECTED,
        concept_id=concept_id,
        producer_run_id=producer_run_id,
        production_plan_id=plan_id,
    )
    after = action_state(
        PipelineAction.WAIT_FOR_PRODUCER,
        creative=CreativePipelineState.APPROVED_FOR_PRODUCTION,
        producer=ProducerPipelineState.PENDING,
        concept_id=concept_id,
        producer_run_id=uuid4(),
    )
    agents = Agents()
    agents.runs[producer_run_id] = SimpleNamespace(
        input_context_refs=(
            {
                "kind": "production_request",
                "target_format": "SHORT_FORM_VERTICAL_VIDEO",
                "aspect_ratio": "9:16",
            },
        )
    )
    feedback = "Shorten the opening and use only one product hero shot."

    await PipelineActionExecutor(
        State(before, after), agents, Creative(), Production(feedback)
    ).execute(
        ctx,
        PipelineLocator(uuid4()),
        expected_action=PipelineAction.RERUN_PRODUCER,
        explicit_approval=pipeline_approval_phrase(PipelineAction.RERUN_PRODUCER),
    )

    name, values = agents.calls[-1]
    assert name == "request_producer"
    assert values["request"].rejection_feedback == feedback
    assert "recovery_of_run_id" not in values

    without_feedback = Agents()
    without_feedback.runs[producer_run_id] = agents.runs[producer_run_id]
    with pytest.raises(PipelineExecutionInvariant):
        await PipelineActionExecutor(
            State(before), without_feedback, Creative(), Production()
        ).execute(
            ctx,
            PipelineLocator(uuid4()),
            expected_action=PipelineAction.RERUN_PRODUCER,
            explicit_approval=pipeline_approval_phrase(PipelineAction.RERUN_PRODUCER),
        )
    assert [name for name, _values in without_feedback.calls] == ["get_run"]


@pytest.mark.asyncio
async def test_free_assembly_plan_creation_uses_exact_plan_and_never_calls_provider() -> None:
    ctx = context(uuid4())
    plan_id = uuid4()
    before = action_state(
        PipelineAction.CREATE_ASSEMBLY_PLAN,
        producer=ProducerPipelineState.APPROVED_FOR_GENERATION,
        production_plan_id=plan_id,
        assembly=AssemblyPipelineState.READY_TO_PLAN,
    )
    after = action_state(
        PipelineAction.WAIT_FOR_ASSEMBLY,
        producer=ProducerPipelineState.APPROVED_FOR_GENERATION,
        production_plan_id=plan_id,
        assembly_plan_id=uuid4(),
        assembly=AssemblyPipelineState.RUNNING,
    )
    agents = Agents()
    assembly = Assembly()

    result = await PipelineActionExecutor(
        State(before, after), agents, Creative(), Production(), assembly
    ).execute(
        ctx,
        PipelineLocator(uuid4()),
        expected_action=PipelineAction.CREATE_ASSEMBLY_PLAN,
    )

    assert assembly.calls == [plan_id]
    assert result.resource_type == "assembly_plan"
    assert result.locator.assembly_plan_id == result.resource_id
    assert result.provider_execution_occurred is False
    assert not agents.calls


@pytest.mark.asyncio
async def test_spend_cap_recovery_reuses_plan_and_requires_exact_approval() -> None:
    ctx = context(uuid4())
    plan_id = uuid4()
    before = action_state(
        PipelineAction.RETRY_MEDIA_AFTER_SPEND_CAP_INCREASE,
        producer=ProducerPipelineState.APPROVED_FOR_GENERATION,
        production_plan_id=plan_id,
        media=MediaPipelineState.BLOCKED_SPEND_CAP,
    )
    after = action_state(
        PipelineAction.WAIT_FOR_MEDIA,
        producer=ProducerPipelineState.APPROVED_FOR_GENERATION,
        production_plan_id=plan_id,
        media=MediaPipelineState.READY,
    )
    production = Production()
    executor = PipelineActionExecutor(State(before, after), Agents(), Creative(), production)
    with pytest.raises(PipelineApprovalRequired):
        await executor.execute(
            ctx,
            PipelineLocator(uuid4()),
            expected_action=PipelineAction.RETRY_MEDIA_AFTER_SPEND_CAP_INCREASE,
        )
    assert not production.retry_calls

    result = await PipelineActionExecutor(
        State(before, after), Agents(), Creative(), production
    ).execute(
        ctx,
        PipelineLocator(uuid4()),
        expected_action=PipelineAction.RETRY_MEDIA_AFTER_SPEND_CAP_INCREASE,
        explicit_approval=pipeline_approval_phrase(
            PipelineAction.RETRY_MEDIA_AFTER_SPEND_CAP_INCREASE
        ),
    )
    assert production.retry_calls[0][0] == plan_id
    assert result.resource_type == "production_plan"
    assert result.resource_id == plan_id
    assert result.provider_execution_occurred is False


@pytest.mark.asyncio
async def test_stranded_start_reconciliation_is_free_and_provider_is_forbidden() -> None:
    ctx, plan_id, job_id = context(uuid4()), uuid4(), uuid4()
    before = action_state(
        PipelineAction.RECOVER_STRANDED_MEDIA_START,
        producer=ProducerPipelineState.APPROVED_FOR_GENERATION,
        production_plan_id=plan_id,
        media=MediaPipelineState.STRANDED_START,
        stranded_media_start_job_ids=(job_id,),
    )
    after = action_state(
        PipelineAction.ABANDON_UNKNOWN_MEDIA_AND_RETRY,
        producer=ProducerPipelineState.APPROVED_FOR_GENERATION,
        production_plan_id=plan_id,
        media=MediaPipelineState.RETRYABLE_OUTCOME_UNKNOWN,
        retryable_unknown_media_job_ids=(job_id,),
    )
    production = Production()
    result = await PipelineActionExecutor(
        State(before, after), Agents(), Creative(), production
    ).execute(
        ctx,
        PipelineLocator(uuid4()),
        expected_action=PipelineAction.RECOVER_STRANDED_MEDIA_START,
    )
    assert production.stranded_calls == [(plan_id, (job_id,))]
    assert result.provider_execution_occurred is False
    definition = EXECUTION_BEHAVIOR_REGISTRY[PipelineAction.RECOVER_STRANDED_MEDIA_START]
    assert (definition.provider_cost, definition.provider_execution_permitted) == (False, False)


@pytest.mark.asyncio
async def test_unknown_media_retry_requires_action_bound_risk_acceptance() -> None:
    ctx, plan_id, job_id = context(uuid4()), uuid4(), uuid4()
    before = action_state(
        PipelineAction.ABANDON_UNKNOWN_MEDIA_AND_RETRY,
        producer=ProducerPipelineState.APPROVED_FOR_GENERATION,
        production_plan_id=plan_id,
        media=MediaPipelineState.RETRYABLE_OUTCOME_UNKNOWN,
        retryable_unknown_media_job_ids=(job_id,),
    )
    after = action_state(
        PipelineAction.WAIT_FOR_MEDIA,
        producer=ProducerPipelineState.APPROVED_FOR_GENERATION,
        production_plan_id=plan_id,
        media=MediaPipelineState.READY,
    )
    production = Production()
    executor = PipelineActionExecutor(State(before, after), Agents(), Creative(), production)
    with pytest.raises(PipelineApprovalRequired):
        await executor.execute(
            ctx,
            PipelineLocator(uuid4()),
            expected_action=PipelineAction.ABANDON_UNKNOWN_MEDIA_AND_RETRY,
        )
    assert not production.ambiguity_calls
    result = await PipelineActionExecutor(
        State(before, after), Agents(), Creative(), production
    ).execute(
        ctx,
        PipelineLocator(uuid4()),
        expected_action=PipelineAction.ABANDON_UNKNOWN_MEDIA_AND_RETRY,
        explicit_approval=pipeline_approval_phrase(PipelineAction.ABANDON_UNKNOWN_MEDIA_AND_RETRY),
    )
    assert production.ambiguity_calls[0][:2] == (plan_id, (job_id,))
    assert result.provider_execution_occurred is False
