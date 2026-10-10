from uuid import uuid4

import pytest

from creative_marketer.orchestration.pipeline import (
    EXECUTION_BEHAVIOR_REGISTRY,
    AssemblyPipelineState,
    CreativePipelineState,
    FinalCreativePipelineState,
    MediaPipelineState,
    PipelineAction,
    PipelineExecutionBehavior,
    PipelineFailureCategory,
    PipelineObservation,
    PipelineStateResolver,
    ProducerPipelineState,
    ResearchPipelineState,
    classify_creative_failure,
    classify_pipeline_failure,
    classify_producer_failure,
)


def test_every_pipeline_action_has_exactly_one_execution_behavior() -> None:
    assert set(EXECUTION_BEHAVIOR_REGISTRY) == set(PipelineAction)
    assert all(
        definition.behavior in set(PipelineExecutionBehavior)
        and definition.operation
        and definition.api_boundary
        and definition.resulting_states
        for definition in EXECUTION_BEHAVIOR_REGISTRY.values()
    )


def test_wait_human_recovery_and_terminal_actions_cannot_execute_provider() -> None:
    assert all(
        not definition.provider_execution_permitted
        for definition in EXECUTION_BEHAVIOR_REGISTRY.values()
        if definition.behavior
        in {
            PipelineExecutionBehavior.WAIT,
            PipelineExecutionBehavior.HUMAN_GATE,
            PipelineExecutionBehavior.RECOVERY_GATE,
            PipelineExecutionBehavior.TERMINAL,
        }
    )


def test_every_billable_execute_action_requires_explicit_approval() -> None:
    assert all(
        definition.explicit_approval_required
        for definition in EXECUTION_BEHAVIOR_REGISTRY.values()
        if definition.behavior is PipelineExecutionBehavior.EXECUTE and definition.provider_cost
    )


@pytest.mark.parametrize(
    ("state", "action"),
    [
        (ResearchPipelineState.NOT_STARTED, PipelineAction.RUN_RESEARCH),
        (ResearchPipelineState.PENDING, PipelineAction.WAIT_FOR_RESEARCH),
        (ResearchPipelineState.RUNNING, PipelineAction.WAIT_FOR_RESEARCH),
        (ResearchPipelineState.SUCCEEDED_EXPIRED, PipelineAction.REFRESH_RESEARCH),
        (ResearchPipelineState.FAILED_BEFORE_PROVIDER, PipelineAction.RETRY_RESEARCH),
        (ResearchPipelineState.FAILED_NO_RESPONSE, PipelineAction.RETRY_RESEARCH),
        (ResearchPipelineState.FAILED_RESPONSE, PipelineAction.RERUN_RESEARCH),
        (
            ResearchPipelineState.OUTCOME_UNKNOWN,
            PipelineAction.RECONCILE_RESEARCH_OUTCOME,
        ),
    ],
)
def test_every_noncurrent_research_state_has_one_governed_action(
    state: ResearchPipelineState, action: PipelineAction
) -> None:
    result = PipelineStateResolver().resolve(PipelineObservation(uuid4(), state))

    assert result.action is action
    assert result.blocking_reason is not None or action is PipelineAction.RUN_RESEARCH


def test_research_transition_matrix_covers_every_declared_state() -> None:
    terminal = {ResearchPipelineState.SUCCEEDED_CURRENT}
    exercised = {
        ResearchPipelineState.NOT_STARTED,
        ResearchPipelineState.PENDING,
        ResearchPipelineState.RUNNING,
        ResearchPipelineState.SUCCEEDED_EXPIRED,
        ResearchPipelineState.FAILED_BEFORE_PROVIDER,
        ResearchPipelineState.FAILED_NO_RESPONSE,
        ResearchPipelineState.FAILED_RESPONSE,
        ResearchPipelineState.OUTCOME_UNKNOWN,
    }

    assert exercised | terminal == set(ResearchPipelineState)


@pytest.mark.parametrize(
    ("state", "action"),
    [
        (CreativePipelineState.NOT_STARTED, PipelineAction.RUN_CREATIVE),
        (CreativePipelineState.PENDING, PipelineAction.WAIT_FOR_CREATIVE),
        (CreativePipelineState.RUNNING, PipelineAction.WAIT_FOR_CREATIVE),
        (CreativePipelineState.SUCCEEDED, PipelineAction.APPROVE_CREATIVE),
        (CreativePipelineState.FAILED, PipelineAction.RERUN_CREATIVE),
        (
            CreativePipelineState.OUTCOME_UNKNOWN,
            PipelineAction.RECONCILE_CREATIVE_OUTCOME,
        ),
        (
            CreativePipelineState.OUTPUT_LIMITED,
            PipelineAction.REPLACE_OUTPUT_LIMITED_CREATIVE,
        ),
        (CreativePipelineState.APPROVAL_REQUIRED, PipelineAction.APPROVE_CREATIVE),
        (CreativePipelineState.REJECTED, PipelineAction.RESTRATEGIZE_CREATIVE),
        (CreativePipelineState.STALE_RESEARCH, PipelineAction.REVALIDATE_CREATIVE),
        (
            CreativePipelineState.REQUIRES_RESTRATEGY,
            PipelineAction.RESTRATEGIZE_CREATIVE,
        ),
    ],
)
def test_every_incomplete_creative_state_has_one_governed_action(
    state: CreativePipelineState, action: PipelineAction
) -> None:
    result = PipelineStateResolver().resolve(
        PipelineObservation(
            uuid4(),
            ResearchPipelineState.SUCCEEDED_CURRENT,
            state,
        )
    )

    assert result.action is action


def test_creative_transition_matrix_covers_every_declared_state() -> None:
    continuing = {
        CreativePipelineState.APPROVED_FOR_PRODUCTION,
        CreativePipelineState.REVALIDATED_FOR_PRODUCTION,
    }
    exercised = {
        CreativePipelineState.NOT_STARTED,
        CreativePipelineState.PENDING,
        CreativePipelineState.RUNNING,
        CreativePipelineState.SUCCEEDED,
        CreativePipelineState.FAILED,
        CreativePipelineState.OUTPUT_LIMITED,
        CreativePipelineState.OUTCOME_UNKNOWN,
        CreativePipelineState.APPROVAL_REQUIRED,
        CreativePipelineState.REJECTED,
        CreativePipelineState.STALE_RESEARCH,
        CreativePipelineState.REQUIRES_RESTRATEGY,
    }

    assert exercised | continuing == set(CreativePipelineState)


@pytest.mark.parametrize(
    "creative_state",
    [
        CreativePipelineState.APPROVED_FOR_PRODUCTION,
        CreativePipelineState.REVALIDATED_FOR_PRODUCTION,
    ],
)
@pytest.mark.parametrize(
    ("state", "action"),
    [
        (ProducerPipelineState.NOT_STARTED, PipelineAction.RUN_PRODUCER),
        (ProducerPipelineState.PENDING, PipelineAction.WAIT_FOR_PRODUCER),
        (ProducerPipelineState.RUNNING, PipelineAction.WAIT_FOR_PRODUCER),
        (ProducerPipelineState.SUCCEEDED, PipelineAction.RECOVER_PRODUCER_INVARIANT),
        (ProducerPipelineState.FAILED_NO_RESPONSE, PipelineAction.RETRY_PRODUCER),
        (ProducerPipelineState.FAILED_RESPONSE, PipelineAction.RERUN_PRODUCER),
        (
            ProducerPipelineState.OUTCOME_UNKNOWN,
            PipelineAction.RECONCILE_PRODUCER_OUTCOME,
        ),
        (ProducerPipelineState.PRODUCTION_PLAN_INVALID, PipelineAction.RERUN_PRODUCER),
        (
            ProducerPipelineState.CURRENT_CONTRACT_RETRY_EXHAUSTED,
            PipelineAction.ESCALATE_PRODUCER_INVALID_OUTPUT,
        ),
        (
            ProducerPipelineState.CONTRACT_UPGRADE_REQUIRED,
            PipelineAction.UPGRADE_PRODUCER_CONTRACT,
        ),
        (
            ProducerPipelineState.INVARIANT_VIOLATION,
            PipelineAction.RECOVER_PRODUCER_INVARIANT,
        ),
        (
            ProducerPipelineState.PRODUCTION_PLAN_REVIEW_REQUIRED,
            PipelineAction.REVIEW_PRODUCTION_PLAN,
        ),
        (ProducerPipelineState.PRODUCTION_PLAN_REJECTED, PipelineAction.RERUN_PRODUCER),
        (
            ProducerPipelineState.APPROVED_FOR_GENERATION,
            PipelineAction.READY_FOR_GENERATION,
        ),
    ],
)
def test_every_producer_state_has_one_governed_action(
    creative_state: CreativePipelineState,
    state: ProducerPipelineState,
    action: PipelineAction,
) -> None:
    result = PipelineStateResolver().resolve(
        PipelineObservation(
            uuid4(),
            ResearchPipelineState.SUCCEEDED_CURRENT,
            creative_state,
            state,
        )
    )

    assert result.action is action


def test_producer_transition_matrix_covers_every_declared_state() -> None:
    assert {
        ProducerPipelineState.NOT_STARTED,
        ProducerPipelineState.PENDING,
        ProducerPipelineState.RUNNING,
        ProducerPipelineState.SUCCEEDED,
        ProducerPipelineState.FAILED_NO_RESPONSE,
        ProducerPipelineState.FAILED_RESPONSE,
        ProducerPipelineState.OUTCOME_UNKNOWN,
        ProducerPipelineState.PRODUCTION_PLAN_INVALID,
        ProducerPipelineState.CURRENT_CONTRACT_RETRY_EXHAUSTED,
        ProducerPipelineState.CONTRACT_UPGRADE_REQUIRED,
        ProducerPipelineState.INVARIANT_VIOLATION,
        ProducerPipelineState.PRODUCTION_PLAN_REVIEW_REQUIRED,
        ProducerPipelineState.PRODUCTION_PLAN_REJECTED,
        ProducerPipelineState.APPROVED_FOR_GENERATION,
    } == set(ProducerPipelineState)


def test_only_provider_actions_are_marked_as_permitted_and_billable() -> None:
    resolver = PipelineStateResolver()
    refresh = resolver.resolve(
        PipelineObservation(uuid4(), ResearchPipelineState.SUCCEEDED_EXPIRED)
    )
    revalidate = resolver.resolve(
        PipelineObservation(
            uuid4(),
            ResearchPipelineState.SUCCEEDED_CURRENT,
            CreativePipelineState.STALE_RESEARCH,
        )
    )
    ready = resolver.resolve(
        PipelineObservation(
            uuid4(),
            ResearchPipelineState.SUCCEEDED_CURRENT,
            CreativePipelineState.REVALIDATED_FOR_PRODUCTION,
            ProducerPipelineState.APPROVED_FOR_GENERATION,
        )
    )

    assert (refresh.costs_money, refresh.human_approval_required) == (True, True)
    assert refresh.provider_execution_permitted is True
    assert (revalidate.costs_money, revalidate.provider_execution_permitted) == (False, False)
    assert ready.action is PipelineAction.READY_FOR_GENERATION
    assert ready.provider_execution_permitted is False


@pytest.mark.parametrize(
    ("state", "action"),
    [
        (MediaPipelineState.NOT_OBSERVED, PipelineAction.READY_FOR_GENERATION),
        (MediaPipelineState.READY, PipelineAction.WAIT_FOR_MEDIA),
        (MediaPipelineState.RUNNING, PipelineAction.WAIT_FOR_MEDIA),
        (MediaPipelineState.FAILED, PipelineAction.RECOVER_MEDIA_FAILURE),
        (MediaPipelineState.OUTCOME_UNKNOWN, PipelineAction.RECONCILE_MEDIA_OUTCOME),
        (
            MediaPipelineState.INVARIANT_VIOLATION,
            PipelineAction.RECOVER_MEDIA_INVARIANT,
        ),
    ],
)
def test_every_incomplete_media_state_has_one_governed_action(
    state: MediaPipelineState, action: PipelineAction
) -> None:
    result = PipelineStateResolver().resolve(
        PipelineObservation(
            uuid4(),
            ResearchPipelineState.SUCCEEDED_CURRENT,
            CreativePipelineState.APPROVED_FOR_PRODUCTION,
            ProducerPipelineState.APPROVED_FOR_GENERATION,
            media=state,
        )
    )

    assert result.action is action


def test_media_transition_matrix_covers_every_declared_state() -> None:
    exercised = {
        MediaPipelineState.NOT_OBSERVED,
        MediaPipelineState.READY,
        MediaPipelineState.RUNNING,
        MediaPipelineState.FAILED,
        MediaPipelineState.OUTCOME_UNKNOWN,
        MediaPipelineState.INVARIANT_VIOLATION,
    }
    assert exercised | {MediaPipelineState.SUCCEEDED} == set(MediaPipelineState)


@pytest.mark.parametrize(
    ("state", "action"),
    [
        (AssemblyPipelineState.NOT_STARTED, PipelineAction.RECOVER_ASSEMBLY_INVARIANT),
        (
            AssemblyPipelineState.INPUT_REQUIRED,
            PipelineAction.BIND_MANUAL_ASSEMBLY_INPUT,
        ),
        (AssemblyPipelineState.BLOCKED, PipelineAction.RECOVER_ASSEMBLY_INPUT),
        (AssemblyPipelineState.READY_TO_PLAN, PipelineAction.CREATE_ASSEMBLY_PLAN),
        (AssemblyPipelineState.RUNNING, PipelineAction.WAIT_FOR_ASSEMBLY),
        (AssemblyPipelineState.FAILED, PipelineAction.RECOVER_ASSEMBLY_FAILURE),
        (
            AssemblyPipelineState.INVARIANT_VIOLATION,
            PipelineAction.RECOVER_ASSEMBLY_INVARIANT,
        ),
    ],
)
def test_every_incomplete_assembly_state_has_one_governed_action(
    state: AssemblyPipelineState, action: PipelineAction
) -> None:
    result = PipelineStateResolver().resolve(
        PipelineObservation(
            uuid4(),
            ResearchPipelineState.SUCCEEDED_CURRENT,
            CreativePipelineState.APPROVED_FOR_PRODUCTION,
            ProducerPipelineState.APPROVED_FOR_GENERATION,
            media=MediaPipelineState.SUCCEEDED,
            assembly=state,
        )
    )

    assert result.action is action


def test_assembly_transition_matrix_covers_every_declared_state() -> None:
    exercised = {
        AssemblyPipelineState.NOT_STARTED,
        AssemblyPipelineState.INPUT_REQUIRED,
        AssemblyPipelineState.BLOCKED,
        AssemblyPipelineState.READY_TO_PLAN,
        AssemblyPipelineState.RUNNING,
        AssemblyPipelineState.FAILED,
        AssemblyPipelineState.INVARIANT_VIOLATION,
    }
    assert exercised | {AssemblyPipelineState.SUCCEEDED} == set(AssemblyPipelineState)


@pytest.mark.parametrize(
    ("state", "action"),
    [
        (
            FinalCreativePipelineState.NOT_STARTED,
            PipelineAction.RECOVER_ASSEMBLY_INVARIANT,
        ),
        (
            FinalCreativePipelineState.REVIEW_REQUIRED,
            PipelineAction.REVIEW_FINAL_CREATIVE,
        ),
        (
            FinalCreativePipelineState.REJECTED,
            PipelineAction.RECOVER_REJECTED_FINAL_CREATIVE,
        ),
        (FinalCreativePipelineState.APPROVED, PipelineAction.FINAL_CREATIVE_READY),
    ],
)
def test_every_final_creative_state_has_one_governed_action(
    state: FinalCreativePipelineState, action: PipelineAction
) -> None:
    result = PipelineStateResolver().resolve(
        PipelineObservation(
            uuid4(),
            ResearchPipelineState.SUCCEEDED_CURRENT,
            CreativePipelineState.APPROVED_FOR_PRODUCTION,
            ProducerPipelineState.APPROVED_FOR_GENERATION,
            media=MediaPipelineState.SUCCEEDED,
            assembly=AssemblyPipelineState.SUCCEEDED,
            final_creative=state,
        )
    )

    assert result.action is action


def test_final_creative_transition_matrix_covers_every_declared_state() -> None:
    assert set(FinalCreativePipelineState) == {
        FinalCreativePipelineState.NOT_STARTED,
        FinalCreativePipelineState.REVIEW_REQUIRED,
        FinalCreativePipelineState.APPROVED,
        FinalCreativePipelineState.REJECTED,
    }


@pytest.mark.parametrize(
    ("operational", "attempt", "expected"),
    [
        ("normal", None, PipelineFailureCategory.BEFORE_PROVIDER),
        ("normal", "CLAIMED", PipelineFailureCategory.BEFORE_PROVIDER),
        ("normal", "FAILED_NO_RESPONSE", PipelineFailureCategory.NO_RESPONSE),
        ("normal", "FAILED_RESPONSE", PipelineFailureCategory.RESPONSE),
        ("normal", "SUCCEEDED", PipelineFailureCategory.RESPONSE),
        ("normal", "PROVIDER_STARTED", PipelineFailureCategory.OUTCOME_UNKNOWN),
        ("normal", "UNKNOWN", PipelineFailureCategory.OUTCOME_UNKNOWN),
        ("recovery_required", "FAILED_NO_RESPONSE", PipelineFailureCategory.OUTCOME_UNKNOWN),
    ],
)
def test_attempt_history_is_classified_without_unsafe_retry(
    operational: str,
    attempt: str | None,
    expected: PipelineFailureCategory,
) -> None:
    assert classify_pipeline_failure(operational, attempt) is expected


def test_output_limit_and_producer_contract_failures_are_bounded() -> None:
    assert (
        classify_creative_failure(PipelineFailureCategory.OUTCOME_UNKNOWN, None)
        is CreativePipelineState.OUTCOME_UNKNOWN
    )
    assert (
        classify_creative_failure(PipelineFailureCategory.RESPONSE, "MAX_OUTPUT_TOKENS")
        is CreativePipelineState.OUTPUT_LIMITED
    )
    assert (
        classify_creative_failure(PipelineFailureCategory.RESPONSE, None)
        is CreativePipelineState.FAILED
    )
    assert (
        classify_producer_failure(
            PipelineFailureCategory.BEFORE_PROVIDER,
            None,
            2,
            2,
        )
        is ProducerPipelineState.FAILED_NO_RESPONSE
    )
    assert (
        classify_producer_failure(
            PipelineFailureCategory.NO_RESPONSE,
            None,
            2,
            2,
        )
        is ProducerPipelineState.FAILED_NO_RESPONSE
    )
    assert (
        classify_producer_failure(
            PipelineFailureCategory.RESPONSE,
            "PRODUCTION_PLAN_INVALID",
            2,
            3,
        )
        is ProducerPipelineState.CONTRACT_UPGRADE_REQUIRED
    )
    assert (
        classify_producer_failure(
            PipelineFailureCategory.RESPONSE,
            "PRODUCTION_PLAN_INVALID",
            3,
            3,
        )
        is ProducerPipelineState.PRODUCTION_PLAN_INVALID
    )
    assert (
        classify_producer_failure(
            PipelineFailureCategory.OUTCOME_UNKNOWN,
            "PRODUCTION_PLAN_INVALID",
            2,
            3,
        )
        is ProducerPipelineState.OUTCOME_UNKNOWN
    )
    assert (
        classify_producer_failure(
            PipelineFailureCategory.RESPONSE,
            "PROVIDER_REJECTED",
            2,
            2,
        )
        is ProducerPipelineState.FAILED_RESPONSE
    )
