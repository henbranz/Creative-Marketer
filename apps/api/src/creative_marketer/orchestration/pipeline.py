from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID


class ResearchPipelineState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED_CURRENT = "SUCCEEDED_CURRENT"
    SUCCEEDED_EXPIRED = "SUCCEEDED_EXPIRED"
    FAILED_BEFORE_PROVIDER = "FAILED_BEFORE_PROVIDER"
    FAILED_NO_RESPONSE = "FAILED_NO_RESPONSE"
    FAILED_RESPONSE = "FAILED_RESPONSE"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class CreativePipelineState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    OUTPUT_LIMITED = "OUTPUT_LIMITED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVED_FOR_PRODUCTION = "APPROVED_FOR_PRODUCTION"
    REJECTED = "REJECTED"
    STALE_RESEARCH = "STALE_RESEARCH"
    REVALIDATED_FOR_PRODUCTION = "REVALIDATED_FOR_PRODUCTION"
    REQUIRES_RESTRATEGY = "REQUIRES_RESTRATEGY"


class ProducerPipelineState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_NO_RESPONSE = "FAILED_NO_RESPONSE"
    FAILED_RESPONSE = "FAILED_RESPONSE"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    PRODUCTION_PLAN_INVALID = "PRODUCTION_PLAN_INVALID"
    CURRENT_CONTRACT_RETRY_EXHAUSTED = "CURRENT_CONTRACT_RETRY_EXHAUSTED"
    CONTRACT_UPGRADE_REQUIRED = "CONTRACT_UPGRADE_REQUIRED"
    INVARIANT_VIOLATION = "INVARIANT_VIOLATION"
    PRODUCTION_PLAN_REVIEW_REQUIRED = "PRODUCTION_PLAN_REVIEW_REQUIRED"
    PRODUCTION_PLAN_REJECTED = "PRODUCTION_PLAN_REJECTED"
    APPROVED_FOR_GENERATION = "APPROVED_FOR_GENERATION"


class PipelineStage(StrEnum):
    RESEARCH = "RESEARCH"
    RESEARCH_RECOVERY = "RESEARCH_RECOVERY"
    CREATIVE = "CREATIVE"
    CREATIVE_RECOVERY = "CREATIVE_RECOVERY"
    CREATIVE_APPROVAL = "CREATIVE_APPROVAL"
    CREATIVE_AUTHORITY_REFRESH = "CREATIVE_AUTHORITY_REFRESH"
    CREATIVE_REVALIDATION = "CREATIVE_REVALIDATION"
    CREATIVE_RESTRATEGY = "CREATIVE_RESTRATEGY"
    PRODUCER = "PRODUCER"
    PRODUCER_RECOVERY = "PRODUCER_RECOVERY"
    PRODUCTION_PLAN_REVIEW = "PRODUCTION_PLAN_REVIEW"
    READY_FOR_GENERATION = "READY_FOR_GENERATION"


class PipelineAction(StrEnum):
    RUN_RESEARCH = "RUN_RESEARCH"
    WAIT_FOR_RESEARCH = "WAIT_FOR_RESEARCH"
    REFRESH_RESEARCH = "REFRESH_RESEARCH"
    RETRY_RESEARCH = "RETRY_RESEARCH"
    RERUN_RESEARCH = "RERUN_RESEARCH"
    RECONCILE_RESEARCH_OUTCOME = "RECONCILE_RESEARCH_OUTCOME"
    RUN_CREATIVE = "RUN_CREATIVE"
    WAIT_FOR_CREATIVE = "WAIT_FOR_CREATIVE"
    RERUN_CREATIVE = "RERUN_CREATIVE"
    RECONCILE_CREATIVE_OUTCOME = "RECONCILE_CREATIVE_OUTCOME"
    REPLACE_OUTPUT_LIMITED_CREATIVE = "REPLACE_OUTPUT_LIMITED_CREATIVE"
    APPROVE_CREATIVE = "APPROVE_CREATIVE"
    REVALIDATE_CREATIVE = "REVALIDATE_CREATIVE"
    RESTRATEGIZE_CREATIVE = "RESTRATEGIZE_CREATIVE"
    RUN_PRODUCER = "RUN_PRODUCER"
    WAIT_FOR_PRODUCER = "WAIT_FOR_PRODUCER"
    RETRY_PRODUCER = "RETRY_PRODUCER"
    RERUN_PRODUCER = "RERUN_PRODUCER"
    RECONCILE_PRODUCER_OUTCOME = "RECONCILE_PRODUCER_OUTCOME"
    ESCALATE_PRODUCER_INVALID_OUTPUT = "ESCALATE_PRODUCER_INVALID_OUTPUT"
    RECOVER_PRODUCER_INVARIANT = "RECOVER_PRODUCER_INVARIANT"
    UPGRADE_PRODUCER_CONTRACT = "UPGRADE_PRODUCER_CONTRACT"
    REVIEW_PRODUCTION_PLAN = "REVIEW_PRODUCTION_PLAN"
    READY_FOR_GENERATION = "READY_FOR_GENERATION"


class PipelineExecutionBehavior(StrEnum):
    """The only allowed execution disposition for a resolver-emitted action."""

    EXECUTE = "EXECUTE"
    WAIT = "WAIT"
    HUMAN_GATE = "HUMAN_GATE"
    RECOVERY_GATE = "RECOVERY_GATE"
    TERMINAL = "TERMINAL"


@dataclass(frozen=True, slots=True)
class PipelineActionExecution:
    behavior: PipelineExecutionBehavior
    operation: str
    api_boundary: str
    provider_cost: bool
    explicit_approval_required: bool
    provider_execution_permitted: bool
    resulting_states: tuple[str, ...]


def _execution(
    behavior: PipelineExecutionBehavior,
    operation: str,
    api_boundary: str,
    *,
    provider_cost: bool = False,
    explicit_approval_required: bool = False,
    provider_execution_permitted: bool = False,
    resulting_states: tuple[str, ...],
) -> PipelineActionExecution:
    return PipelineActionExecution(
        behavior,
        operation,
        api_boundary,
        provider_cost,
        explicit_approval_required,
        provider_execution_permitted,
        resulting_states,
    )


_EXECUTION_BEHAVIOR_REGISTRY: dict[PipelineAction, PipelineActionExecution] = {
    PipelineAction.RUN_RESEARCH: _execution(
        PipelineExecutionBehavior.EXECUTE,
        "request_researcher",
        "POST /v1/products/{product_id}/pipeline/execute-next",
        provider_cost=True,
        explicit_approval_required=True,
        provider_execution_permitted=True,
        resulting_states=("Research:PENDING",),
    ),
    PipelineAction.WAIT_FOR_RESEARCH: _execution(
        PipelineExecutionBehavior.WAIT,
        "await_research_worker",
        "GET /v1/agent-runs/{research_run_id}",
        resulting_states=("Research:RUNNING", "Research:SUCCEEDED_CURRENT", "Research:FAILED"),
    ),
    PipelineAction.REFRESH_RESEARCH: _execution(
        PipelineExecutionBehavior.EXECUTE,
        "request_researcher_refresh",
        "POST /v1/products/{product_id}/pipeline/execute-next",
        provider_cost=True,
        explicit_approval_required=True,
        provider_execution_permitted=True,
        resulting_states=("Research:PENDING",),
    ),
    PipelineAction.RETRY_RESEARCH: _execution(
        PipelineExecutionBehavior.RECOVERY_GATE,
        "rerun_research_after_safe_failure",
        "operator AgentRun recovery boundary",
        provider_cost=True,
        explicit_approval_required=True,
        resulting_states=("Research:PENDING",),
    ),
    PipelineAction.RERUN_RESEARCH: _execution(
        PipelineExecutionBehavior.EXECUTE,
        "request_fresh_researcher_run",
        "POST /v1/products/{product_id}/pipeline/execute-next",
        provider_cost=True,
        explicit_approval_required=True,
        provider_execution_permitted=True,
        resulting_states=("Research:PENDING",),
    ),
    PipelineAction.RECONCILE_RESEARCH_OUTCOME: _execution(
        PipelineExecutionBehavior.RECOVERY_GATE,
        "reconcile_research_provider_outcome",
        "operator AgentRun reconciliation boundary",
        explicit_approval_required=True,
        resulting_states=("Research:OUTCOME_UNKNOWN", "Research:FAILED"),
    ),
    PipelineAction.RUN_CREATIVE: _execution(
        PipelineExecutionBehavior.EXECUTE,
        "request_creative_strategist",
        "POST /v1/products/{product_id}/pipeline/execute-next",
        provider_cost=True,
        explicit_approval_required=True,
        provider_execution_permitted=True,
        resulting_states=("Creative:PENDING",),
    ),
    PipelineAction.WAIT_FOR_CREATIVE: _execution(
        PipelineExecutionBehavior.WAIT,
        "await_creative_worker",
        "GET /v1/agent-runs/{creative_run_id}",
        resulting_states=("Creative:RUNNING", "Creative:SUCCEEDED", "Creative:FAILED"),
    ),
    PipelineAction.RERUN_CREATIVE: _execution(
        PipelineExecutionBehavior.EXECUTE,
        "request_fresh_creative_run",
        "POST /v1/products/{product_id}/pipeline/execute-next",
        provider_cost=True,
        explicit_approval_required=True,
        provider_execution_permitted=True,
        resulting_states=("Creative:PENDING",),
    ),
    PipelineAction.RECONCILE_CREATIVE_OUTCOME: _execution(
        PipelineExecutionBehavior.RECOVERY_GATE,
        "reconcile_creative_provider_outcome",
        "operator AgentRun reconciliation boundary",
        explicit_approval_required=True,
        resulting_states=("Creative:OUTCOME_UNKNOWN", "Creative:FAILED"),
    ),
    PipelineAction.REPLACE_OUTPUT_LIMITED_CREATIVE: _execution(
        PipelineExecutionBehavior.EXECUTE,
        "replace_output_limited_creative",
        "POST /v1/products/{product_id}/pipeline/execute-next",
        provider_cost=True,
        explicit_approval_required=True,
        provider_execution_permitted=True,
        resulting_states=("Creative:PENDING",),
    ),
    PipelineAction.APPROVE_CREATIVE: _execution(
        PipelineExecutionBehavior.HUMAN_GATE,
        "select_and_decide_creative_concept",
        "POST /v1/creative/concepts/{concept_id}/decision",
        explicit_approval_required=True,
        resulting_states=("Creative:APPROVED_FOR_PRODUCTION", "Creative:REJECTED"),
    ),
    PipelineAction.REVALIDATE_CREATIVE: _execution(
        PipelineExecutionBehavior.EXECUTE,
        "revalidate_creative_concept",
        "POST /v1/products/{product_id}/pipeline/execute-next",
        resulting_states=(
            "Creative:REVALIDATED_FOR_PRODUCTION",
            "Creative:REQUIRES_RESTRATEGY",
        ),
    ),
    PipelineAction.RESTRATEGIZE_CREATIVE: _execution(
        PipelineExecutionBehavior.EXECUTE,
        "request_creative_restrategy",
        "POST /v1/products/{product_id}/pipeline/execute-next",
        provider_cost=True,
        explicit_approval_required=True,
        provider_execution_permitted=True,
        resulting_states=("Creative:PENDING",),
    ),
    PipelineAction.RUN_PRODUCER: _execution(
        PipelineExecutionBehavior.EXECUTE,
        "request_producer",
        "POST /v1/products/{product_id}/pipeline/execute-next",
        provider_cost=True,
        explicit_approval_required=True,
        provider_execution_permitted=True,
        resulting_states=("Producer:PENDING",),
    ),
    PipelineAction.WAIT_FOR_PRODUCER: _execution(
        PipelineExecutionBehavior.WAIT,
        "await_producer_worker",
        "GET /v1/agent-runs/{producer_run_id}",
        resulting_states=("Producer:RUNNING", "Producer:SUCCEEDED", "Producer:FAILED"),
    ),
    PipelineAction.RETRY_PRODUCER: _execution(
        PipelineExecutionBehavior.RECOVERY_GATE,
        "rerun_producer_after_safe_failure",
        "operator AgentRun recovery boundary",
        provider_cost=True,
        explicit_approval_required=True,
        resulting_states=("Producer:PENDING",),
    ),
    PipelineAction.RERUN_PRODUCER: _execution(
        PipelineExecutionBehavior.EXECUTE,
        "request_bounded_fresh_producer_run",
        "POST /v1/products/{product_id}/pipeline/execute-next",
        provider_cost=True,
        explicit_approval_required=True,
        provider_execution_permitted=True,
        resulting_states=("Producer:PENDING",),
    ),
    PipelineAction.RECONCILE_PRODUCER_OUTCOME: _execution(
        PipelineExecutionBehavior.RECOVERY_GATE,
        "reconcile_producer_provider_outcome",
        "operator AgentRun reconciliation boundary",
        explicit_approval_required=True,
        resulting_states=("Producer:OUTCOME_UNKNOWN", "Producer:FAILED"),
    ),
    PipelineAction.ESCALATE_PRODUCER_INVALID_OUTPUT: _execution(
        PipelineExecutionBehavior.RECOVERY_GATE,
        "diagnose_repeated_current_contract_invalid_output",
        "operator Producer contract/prompt diagnostic boundary",
        resulting_states=("Producer:CURRENT_CONTRACT_RETRY_EXHAUSTED",),
    ),
    PipelineAction.RECOVER_PRODUCER_INVARIANT: _execution(
        PipelineExecutionBehavior.RECOVERY_GATE,
        "recover_succeeded_producer_missing_plan",
        "operator AgentRun materialization diagnostic boundary",
        resulting_states=("Producer:INVARIANT_VIOLATION",),
    ),
    PipelineAction.UPGRADE_PRODUCER_CONTRACT: _execution(
        PipelineExecutionBehavior.EXECUTE,
        "upgrade_producer_contract",
        "POST /v1/products/{product_id}/pipeline/execute-next",
        provider_cost=True,
        explicit_approval_required=True,
        provider_execution_permitted=True,
        resulting_states=("Producer:PENDING",),
    ),
    PipelineAction.REVIEW_PRODUCTION_PLAN: _execution(
        PipelineExecutionBehavior.HUMAN_GATE,
        "review_and_decide_production_plan",
        "POST /v1/production/plans/{production_plan_id}/approve-generation|reject",
        explicit_approval_required=True,
        resulting_states=(
            "Producer:APPROVED_FOR_GENERATION",
            "Producer:PRODUCTION_PLAN_REJECTED",
        ),
    ),
    PipelineAction.READY_FOR_GENERATION: _execution(
        PipelineExecutionBehavior.TERMINAL,
        "handoff_to_media_production",
        "production.plan.approved_for_generation.v1",
        resulting_states=("Media:READY",),
    ),
}

EXECUTION_BEHAVIOR_REGISTRY: Mapping[PipelineAction, PipelineActionExecution] = MappingProxyType(
    _EXECUTION_BEHAVIOR_REGISTRY
)


class PipelineFailureCategory(StrEnum):
    BEFORE_PROVIDER = "BEFORE_PROVIDER"
    NO_RESPONSE = "NO_RESPONSE"
    RESPONSE = "RESPONSE"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


def classify_pipeline_failure(
    operational_status: str,
    attempt_status: str | None,
) -> PipelineFailureCategory:
    if operational_status == "recovery_required":
        return PipelineFailureCategory.OUTCOME_UNKNOWN
    if attempt_status is None or attempt_status == "CLAIMED":
        return PipelineFailureCategory.BEFORE_PROVIDER
    if attempt_status == "FAILED_NO_RESPONSE":
        return PipelineFailureCategory.NO_RESPONSE
    if attempt_status in {"PROVIDER_STARTED", "UNKNOWN"}:
        return PipelineFailureCategory.OUTCOME_UNKNOWN
    return PipelineFailureCategory.RESPONSE


def classify_creative_failure(
    category: PipelineFailureCategory,
    provider_failure_reason: str | None,
) -> CreativePipelineState:
    if category is PipelineFailureCategory.OUTCOME_UNKNOWN:
        return CreativePipelineState.OUTCOME_UNKNOWN
    if provider_failure_reason == "MAX_OUTPUT_TOKENS":
        return CreativePipelineState.OUTPUT_LIMITED
    return CreativePipelineState.FAILED


def classify_producer_failure(
    category: PipelineFailureCategory,
    failure_code: str | None,
    output_contract_version: int,
    current_contract_version: int,
) -> ProducerPipelineState:
    if category is PipelineFailureCategory.OUTCOME_UNKNOWN:
        return ProducerPipelineState.OUTCOME_UNKNOWN
    if category in {
        PipelineFailureCategory.BEFORE_PROVIDER,
        PipelineFailureCategory.NO_RESPONSE,
    }:
        return ProducerPipelineState.FAILED_NO_RESPONSE
    if failure_code in {"MODEL_INVALID_OUTPUT", "PRODUCTION_PLAN_INVALID"}:
        return (
            ProducerPipelineState.CONTRACT_UPGRADE_REQUIRED
            if output_contract_version < current_contract_version
            else ProducerPipelineState.PRODUCTION_PLAN_INVALID
        )
    return ProducerPipelineState.FAILED_RESPONSE


@dataclass(frozen=True, slots=True)
class PipelineLocator:
    product_id: UUID
    research_run_id: UUID | None = None
    creative_run_id: UUID | None = None
    concept_id: UUID | None = None
    creative_revalidation_id: UUID | None = None
    producer_run_id: UUID | None = None
    production_plan_id: UUID | None = None
    use_latest_when_unbound: bool = True


@dataclass(frozen=True, slots=True)
class PipelineObservation:
    product_id: UUID
    research: ResearchPipelineState
    creative: CreativePipelineState = CreativePipelineState.NOT_STARTED
    producer: ProducerPipelineState = ProducerPipelineState.NOT_STARTED
    research_run_id: UUID | None = None
    research_snapshot_id: UUID | None = None
    creative_run_id: UUID | None = None
    concept_id: UUID | None = None
    creative_revalidation_id: UUID | None = None
    producer_run_id: UUID | None = None
    production_plan_id: UUID | None = None
    blocking_reason: str | None = None
    producer_current_contract_invalid_attempts: int = 0

    def __post_init__(self) -> None:
        if self.producer_current_contract_invalid_attempts < 0:
            raise ValueError("Producer invalid attempt count cannot be negative")


@dataclass(frozen=True, slots=True)
class NextPipelineAction:
    stage: PipelineStage
    action: PipelineAction
    blocking_reason: str | None
    costs_money: bool
    human_approval_required: bool
    provider_execution_permitted: bool
    research_state: ResearchPipelineState
    creative_state: CreativePipelineState
    producer_state: ProducerPipelineState
    research_run_id: UUID | None = None
    research_snapshot_id: UUID | None = None
    creative_run_id: UUID | None = None
    concept_id: UUID | None = None
    creative_revalidation_id: UUID | None = None
    producer_run_id: UUID | None = None
    production_plan_id: UUID | None = None


class PipelineStateResolver:
    """The sole deterministic Research -> Creative -> Producer transition table."""

    def resolve(self, state: PipelineObservation) -> NextPipelineAction:
        research = {
            ResearchPipelineState.NOT_STARTED: (
                PipelineStage.RESEARCH,
                PipelineAction.RUN_RESEARCH,
                None,
            ),
            ResearchPipelineState.PENDING: (
                PipelineStage.RESEARCH,
                PipelineAction.WAIT_FOR_RESEARCH,
                "RESEARCH_RUN_PENDING",
            ),
            ResearchPipelineState.RUNNING: (
                PipelineStage.RESEARCH,
                PipelineAction.WAIT_FOR_RESEARCH,
                "RESEARCH_RUN_IN_PROGRESS",
            ),
            ResearchPipelineState.SUCCEEDED_EXPIRED: (
                PipelineStage.CREATIVE_AUTHORITY_REFRESH,
                PipelineAction.REFRESH_RESEARCH,
                "RESEARCH_AUTHORITY_EXPIRED",
            ),
            ResearchPipelineState.FAILED_BEFORE_PROVIDER: (
                PipelineStage.RESEARCH_RECOVERY,
                PipelineAction.RETRY_RESEARCH,
                "RESEARCH_FAILED_BEFORE_PROVIDER",
            ),
            ResearchPipelineState.FAILED_NO_RESPONSE: (
                PipelineStage.RESEARCH_RECOVERY,
                PipelineAction.RETRY_RESEARCH,
                "RESEARCH_PROVIDER_RETURNED_NO_RESPONSE",
            ),
            ResearchPipelineState.FAILED_RESPONSE: (
                PipelineStage.RESEARCH_RECOVERY,
                PipelineAction.RERUN_RESEARCH,
                "RESEARCH_RESPONSE_FAILED_VALIDATION",
            ),
            ResearchPipelineState.OUTCOME_UNKNOWN: (
                PipelineStage.RESEARCH_RECOVERY,
                PipelineAction.RECONCILE_RESEARCH_OUTCOME,
                "RESEARCH_PROVIDER_OUTCOME_UNKNOWN",
            ),
        }
        if state.research is not ResearchPipelineState.SUCCEEDED_CURRENT:
            return self._result(state, research[state.research])

        creative = {
            CreativePipelineState.NOT_STARTED: (
                PipelineStage.CREATIVE,
                PipelineAction.RUN_CREATIVE,
                None,
            ),
            CreativePipelineState.PENDING: (
                PipelineStage.CREATIVE,
                PipelineAction.WAIT_FOR_CREATIVE,
                "CREATIVE_RUN_PENDING",
            ),
            CreativePipelineState.RUNNING: (
                PipelineStage.CREATIVE,
                PipelineAction.WAIT_FOR_CREATIVE,
                "CREATIVE_RUN_IN_PROGRESS",
            ),
            CreativePipelineState.FAILED: (
                PipelineStage.CREATIVE_RECOVERY,
                PipelineAction.RERUN_CREATIVE,
                "CREATIVE_RUN_FAILED",
            ),
            CreativePipelineState.OUTCOME_UNKNOWN: (
                PipelineStage.CREATIVE_RECOVERY,
                PipelineAction.RECONCILE_CREATIVE_OUTCOME,
                "CREATIVE_PROVIDER_OUTCOME_UNKNOWN",
            ),
            CreativePipelineState.OUTPUT_LIMITED: (
                PipelineStage.CREATIVE_RECOVERY,
                PipelineAction.REPLACE_OUTPUT_LIMITED_CREATIVE,
                "CREATIVE_OUTPUT_TOKEN_LIMIT_REACHED",
            ),
            CreativePipelineState.SUCCEEDED: (
                PipelineStage.CREATIVE_APPROVAL,
                PipelineAction.APPROVE_CREATIVE,
                "CREATIVE_CONCEPT_SELECTION_REQUIRED",
            ),
            CreativePipelineState.APPROVAL_REQUIRED: (
                PipelineStage.CREATIVE_APPROVAL,
                PipelineAction.APPROVE_CREATIVE,
                "CREATIVE_APPROVAL_REQUIRED",
            ),
            CreativePipelineState.REJECTED: (
                PipelineStage.CREATIVE_RESTRATEGY,
                PipelineAction.RESTRATEGIZE_CREATIVE,
                "CREATIVE_CONCEPT_REJECTED",
            ),
            CreativePipelineState.STALE_RESEARCH: (
                PipelineStage.CREATIVE_REVALIDATION,
                PipelineAction.REVALIDATE_CREATIVE,
                "APPROVED_CREATIVE_USES_HISTORICAL_RESEARCH",
            ),
            CreativePipelineState.REQUIRES_RESTRATEGY: (
                PipelineStage.CREATIVE_RESTRATEGY,
                PipelineAction.RESTRATEGIZE_CREATIVE,
                "CURRENT_AUTHORITY_MATERIALLY_DIFFERS",
            ),
        }
        if state.creative not in {
            CreativePipelineState.APPROVED_FOR_PRODUCTION,
            CreativePipelineState.REVALIDATED_FOR_PRODUCTION,
        }:
            return self._result(state, creative[state.creative])

        producer = {
            ProducerPipelineState.NOT_STARTED: (
                PipelineStage.PRODUCER,
                PipelineAction.RUN_PRODUCER,
                None,
            ),
            ProducerPipelineState.PENDING: (
                PipelineStage.PRODUCER,
                PipelineAction.WAIT_FOR_PRODUCER,
                "PRODUCER_RUN_PENDING",
            ),
            ProducerPipelineState.RUNNING: (
                PipelineStage.PRODUCER,
                PipelineAction.WAIT_FOR_PRODUCER,
                "PRODUCER_RUN_IN_PROGRESS",
            ),
            ProducerPipelineState.FAILED_NO_RESPONSE: (
                PipelineStage.PRODUCER_RECOVERY,
                PipelineAction.RETRY_PRODUCER,
                "PRODUCER_PROVIDER_RETURNED_NO_RESPONSE",
            ),
            ProducerPipelineState.FAILED_RESPONSE: (
                PipelineStage.PRODUCER_RECOVERY,
                PipelineAction.RERUN_PRODUCER,
                "PRODUCER_RESPONSE_FAILED",
            ),
            ProducerPipelineState.OUTCOME_UNKNOWN: (
                PipelineStage.PRODUCER_RECOVERY,
                PipelineAction.RECONCILE_PRODUCER_OUTCOME,
                "PRODUCER_PROVIDER_OUTCOME_UNKNOWN",
            ),
            ProducerPipelineState.PRODUCTION_PLAN_INVALID: (
                PipelineStage.PRODUCER_RECOVERY,
                PipelineAction.RERUN_PRODUCER,
                "PRODUCTION_PLAN_FAILED_CURRENT_CONTRACT_VALIDATION",
            ),
            ProducerPipelineState.CURRENT_CONTRACT_RETRY_EXHAUSTED: (
                PipelineStage.PRODUCER_RECOVERY,
                PipelineAction.ESCALATE_PRODUCER_INVALID_OUTPUT,
                "PRODUCER_CURRENT_CONTRACT_RETRY_LIMIT_REACHED",
            ),
            ProducerPipelineState.CONTRACT_UPGRADE_REQUIRED: (
                PipelineStage.PRODUCER_RECOVERY,
                PipelineAction.UPGRADE_PRODUCER_CONTRACT,
                "PRODUCTION_PLAN_USED_HISTORICAL_CONTRACT",
            ),
            ProducerPipelineState.INVARIANT_VIOLATION: (
                PipelineStage.PRODUCER_RECOVERY,
                PipelineAction.RECOVER_PRODUCER_INVARIANT,
                "PRODUCER_SUCCESS_MISSING_PRODUCTION_PLAN",
            ),
            ProducerPipelineState.SUCCEEDED: (
                PipelineStage.PRODUCER_RECOVERY,
                PipelineAction.RECOVER_PRODUCER_INVARIANT,
                "PRODUCER_SUCCESS_MISSING_PRODUCTION_PLAN",
            ),
            ProducerPipelineState.PRODUCTION_PLAN_REVIEW_REQUIRED: (
                PipelineStage.PRODUCTION_PLAN_REVIEW,
                PipelineAction.REVIEW_PRODUCTION_PLAN,
                "PRODUCTION_PLAN_REVIEW_REQUIRED",
            ),
            ProducerPipelineState.PRODUCTION_PLAN_REJECTED: (
                PipelineStage.PRODUCER_RECOVERY,
                PipelineAction.RERUN_PRODUCER,
                "PRODUCTION_PLAN_REJECTED",
            ),
            ProducerPipelineState.APPROVED_FOR_GENERATION: (
                PipelineStage.READY_FOR_GENERATION,
                PipelineAction.READY_FOR_GENERATION,
                None,
            ),
        }
        return self._result(state, producer[state.producer])

    @staticmethod
    def _result(
        state: PipelineObservation,
        rule: tuple[PipelineStage, PipelineAction, str | None],
    ) -> NextPipelineAction:
        stage, action, default_reason = rule
        execution = EXECUTION_BEHAVIOR_REGISTRY[action]
        return NextPipelineAction(
            stage,
            action,
            state.blocking_reason or default_reason,
            execution.provider_cost,
            execution.explicit_approval_required,
            execution.provider_execution_permitted,
            state.research,
            state.creative,
            state.producer,
            state.research_run_id,
            state.research_snapshot_id,
            state.creative_run_id,
            state.concept_id,
            state.creative_revalidation_id,
            state.producer_run_id,
            state.production_plan_id,
        )
