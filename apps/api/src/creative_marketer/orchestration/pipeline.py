from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from creative_marketer.production.domain import MediaSpendRequirement


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


class MediaPipelineState(StrEnum):
    NOT_OBSERVED = "NOT_OBSERVED"
    READY = "READY"
    BLOCKED_SPEND_CAP = "BLOCKED_SPEND_CAP"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    INVARIANT_VIOLATION = "INVARIANT_VIOLATION"


class AssemblyPipelineState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    INPUT_REQUIRED = "INPUT_REQUIRED"
    BLOCKED = "BLOCKED"
    READY_TO_PLAN = "READY_TO_PLAN"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INVARIANT_VIOLATION = "INVARIANT_VIOLATION"


class FinalCreativePipelineState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


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
    MEDIA_GENERATION = "MEDIA_GENERATION"
    MEDIA_RECOVERY = "MEDIA_RECOVERY"
    ASSEMBLY_INPUT = "ASSEMBLY_INPUT"
    ASSEMBLY = "ASSEMBLY"
    ASSEMBLY_RECOVERY = "ASSEMBLY_RECOVERY"
    FINAL_CREATIVE_REVIEW = "FINAL_CREATIVE_REVIEW"
    FINAL_CREATIVE_READY = "FINAL_CREATIVE_READY"


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
    WAIT_FOR_MEDIA = "WAIT_FOR_MEDIA"
    RETRY_MEDIA_AFTER_SPEND_CAP_INCREASE = "RETRY_MEDIA_AFTER_SPEND_CAP_INCREASE"
    RECOVER_MEDIA_FAILURE = "RECOVER_MEDIA_FAILURE"
    RECONCILE_MEDIA_OUTCOME = "RECONCILE_MEDIA_OUTCOME"
    RECOVER_MEDIA_INVARIANT = "RECOVER_MEDIA_INVARIANT"
    BIND_MANUAL_ASSEMBLY_INPUT = "BIND_MANUAL_ASSEMBLY_INPUT"
    RECOVER_ASSEMBLY_INPUT = "RECOVER_ASSEMBLY_INPUT"
    CREATE_ASSEMBLY_PLAN = "CREATE_ASSEMBLY_PLAN"
    WAIT_FOR_ASSEMBLY = "WAIT_FOR_ASSEMBLY"
    RECOVER_ASSEMBLY_FAILURE = "RECOVER_ASSEMBLY_FAILURE"
    RECOVER_ASSEMBLY_INVARIANT = "RECOVER_ASSEMBLY_INVARIANT"
    REVIEW_FINAL_CREATIVE = "REVIEW_FINAL_CREATIVE"
    RECOVER_REJECTED_FINAL_CREATIVE = "RECOVER_REJECTED_FINAL_CREATIVE"
    FINAL_CREATIVE_READY = "FINAL_CREATIVE_READY"


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
    PipelineAction.WAIT_FOR_MEDIA: _execution(
        PipelineExecutionBehavior.WAIT,
        "await_generation_workers",
        "GET /v1/production/plans/{production_plan_id}/jobs",
        resulting_states=("Media:RUNNING", "Media:SUCCEEDED", "Media:FAILED"),
    ),
    PipelineAction.RETRY_MEDIA_AFTER_SPEND_CAP_INCREASE: _execution(
        PipelineExecutionBehavior.EXECUTE,
        "revalidate_and_resume_spend_cap_blocked_media",
        "POST /v1/products/{product_id}/pipeline/execute-next",
        provider_cost=True,
        explicit_approval_required=True,
        provider_execution_permitted=True,
        resulting_states=("Media:READY", "Media:RUNNING"),
    ),
    PipelineAction.RECOVER_MEDIA_FAILURE: _execution(
        PipelineExecutionBehavior.RECOVERY_GATE,
        "recover_failed_generation_job",
        "operator GenerationJob recovery boundary",
        explicit_approval_required=True,
        resulting_states=("Media:READY", "Media:FAILED"),
    ),
    PipelineAction.RECONCILE_MEDIA_OUTCOME: _execution(
        PipelineExecutionBehavior.RECOVERY_GATE,
        "reconcile_generation_provider_outcome",
        "operator GenerationJob reconciliation boundary",
        explicit_approval_required=True,
        resulting_states=("Media:RUNNING", "Media:SUCCEEDED", "Media:FAILED"),
    ),
    PipelineAction.RECOVER_MEDIA_INVARIANT: _execution(
        PipelineExecutionBehavior.RECOVERY_GATE,
        "recover_approved_plan_missing_generation_jobs",
        "operator ProductionPlan materialization diagnostic boundary",
        resulting_states=("Media:INVARIANT_VIOLATION",),
    ),
    PipelineAction.BIND_MANUAL_ASSEMBLY_INPUT: _execution(
        PipelineExecutionBehavior.HUMAN_GATE,
        "select_and_bind_manual_source_asset",
        "POST /v1/production/shots/{shot_id}/manual-source",
        explicit_approval_required=True,
        resulting_states=("Assembly:INPUT_REQUIRED", "Assembly:READY_TO_PLAN"),
    ),
    PipelineAction.RECOVER_ASSEMBLY_INPUT: _execution(
        PipelineExecutionBehavior.RECOVERY_GATE,
        "recover_invalid_assembly_source",
        "operator Asset rights/current-authority boundary",
        resulting_states=("Assembly:BLOCKED", "Assembly:READY_TO_PLAN"),
    ),
    PipelineAction.CREATE_ASSEMBLY_PLAN: _execution(
        PipelineExecutionBehavior.EXECUTE,
        "create_assembly_plan",
        "POST /v1/products/{product_id}/pipeline/execute-next",
        resulting_states=("Assembly:RUNNING",),
    ),
    PipelineAction.WAIT_FOR_ASSEMBLY: _execution(
        PipelineExecutionBehavior.WAIT,
        "await_assembly_worker",
        "GET /v1/assembly/jobs/{assembly_job_id}",
        resulting_states=("Assembly:RUNNING", "Assembly:SUCCEEDED", "Assembly:FAILED"),
    ),
    PipelineAction.RECOVER_ASSEMBLY_FAILURE: _execution(
        PipelineExecutionBehavior.RECOVERY_GATE,
        "recover_failed_assembly_job",
        "operator AssemblyJob recovery boundary",
        resulting_states=("Assembly:RUNNING", "Assembly:FAILED"),
    ),
    PipelineAction.RECOVER_ASSEMBLY_INVARIANT: _execution(
        PipelineExecutionBehavior.RECOVERY_GATE,
        "recover_assembly_persistence_invariant",
        "operator Assembly persistence diagnostic boundary",
        resulting_states=("Assembly:INVARIANT_VIOLATION",),
    ),
    PipelineAction.REVIEW_FINAL_CREATIVE: _execution(
        PipelineExecutionBehavior.HUMAN_GATE,
        "review_and_decide_final_creative",
        "POST /v1/final-creatives/{final_creative_id}/approve-publishing|reject",
        explicit_approval_required=True,
        resulting_states=("FinalCreative:APPROVED", "FinalCreative:REJECTED"),
    ),
    PipelineAction.RECOVER_REJECTED_FINAL_CREATIVE: _execution(
        PipelineExecutionBehavior.RECOVERY_GATE,
        "plan_revision_after_final_creative_rejection",
        "operator ProductionPlan/Assembly revision boundary",
        resulting_states=("FinalCreative:REJECTED",),
    ),
    PipelineAction.FINAL_CREATIVE_READY: _execution(
        PipelineExecutionBehavior.TERMINAL,
        "final_creative_approved_for_publishing",
        "GET /v1/final-creatives/{final_creative_id}",
        resulting_states=("FinalCreative:APPROVED",),
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
    assembly_plan_id: UUID | None = None
    final_creative_id: UUID | None = None


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
    assembly_plan_id: UUID | None = None
    final_creative_id: UUID | None = None
    media: MediaPipelineState = MediaPipelineState.NOT_OBSERVED
    assembly: AssemblyPipelineState = AssemblyPipelineState.NOT_STARTED
    final_creative: FinalCreativePipelineState = FinalCreativePipelineState.NOT_STARTED
    media_spend_requirement: MediaSpendRequirement | None = None

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
    assembly_plan_id: UUID | None = None
    final_creative_id: UUID | None = None
    media_state: MediaPipelineState = MediaPipelineState.NOT_OBSERVED
    assembly_state: AssemblyPipelineState = AssemblyPipelineState.NOT_STARTED
    final_creative_state: FinalCreativePipelineState = FinalCreativePipelineState.NOT_STARTED
    media_spend_requirement: MediaSpendRequirement | None = None


class PipelineStateResolver:
    """The sole deterministic Product-to-FinalCreative transition table."""

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
        if state.producer is not ProducerPipelineState.APPROVED_FOR_GENERATION:
            return self._result(state, producer[state.producer])

        if state.media is MediaPipelineState.NOT_OBSERVED:
            return self._result(state, producer[state.producer])
        media = {
            MediaPipelineState.READY: (
                PipelineStage.MEDIA_GENERATION,
                PipelineAction.WAIT_FOR_MEDIA,
                "GENERATION_JOBS_READY_FOR_WORKER",
            ),
            MediaPipelineState.RUNNING: (
                PipelineStage.MEDIA_GENERATION,
                PipelineAction.WAIT_FOR_MEDIA,
                "GENERATION_JOBS_IN_PROGRESS",
            ),
            MediaPipelineState.BLOCKED_SPEND_CAP: (
                PipelineStage.MEDIA_RECOVERY,
                PipelineAction.RETRY_MEDIA_AFTER_SPEND_CAP_INCREASE,
                "LIVE_E2E_SPEND_CAP_REACHED",
            ),
            MediaPipelineState.FAILED: (
                PipelineStage.MEDIA_RECOVERY,
                PipelineAction.RECOVER_MEDIA_FAILURE,
                "GENERATION_JOB_FAILED",
            ),
            MediaPipelineState.OUTCOME_UNKNOWN: (
                PipelineStage.MEDIA_RECOVERY,
                PipelineAction.RECONCILE_MEDIA_OUTCOME,
                "GENERATION_PROVIDER_OUTCOME_UNKNOWN",
            ),
            MediaPipelineState.INVARIANT_VIOLATION: (
                PipelineStage.MEDIA_RECOVERY,
                PipelineAction.RECOVER_MEDIA_INVARIANT,
                "APPROVED_PLAN_MISSING_GENERATION_JOBS",
            ),
        }
        if state.media is not MediaPipelineState.SUCCEEDED:
            return self._result(state, media[state.media])

        assembly = {
            AssemblyPipelineState.NOT_STARTED: (
                PipelineStage.ASSEMBLY_RECOVERY,
                PipelineAction.RECOVER_ASSEMBLY_INVARIANT,
                "ASSEMBLY_STATE_NOT_PROJECTED",
            ),
            AssemblyPipelineState.INPUT_REQUIRED: (
                PipelineStage.ASSEMBLY_INPUT,
                PipelineAction.BIND_MANUAL_ASSEMBLY_INPUT,
                "MANUAL_ASSEMBLY_SOURCE_REQUIRED",
            ),
            AssemblyPipelineState.BLOCKED: (
                PipelineStage.ASSEMBLY_RECOVERY,
                PipelineAction.RECOVER_ASSEMBLY_INPUT,
                "ASSEMBLY_SOURCE_INVALID",
            ),
            AssemblyPipelineState.READY_TO_PLAN: (
                PipelineStage.ASSEMBLY,
                PipelineAction.CREATE_ASSEMBLY_PLAN,
                None,
            ),
            AssemblyPipelineState.RUNNING: (
                PipelineStage.ASSEMBLY,
                PipelineAction.WAIT_FOR_ASSEMBLY,
                "ASSEMBLY_JOB_IN_PROGRESS",
            ),
            AssemblyPipelineState.FAILED: (
                PipelineStage.ASSEMBLY_RECOVERY,
                PipelineAction.RECOVER_ASSEMBLY_FAILURE,
                "ASSEMBLY_JOB_FAILED",
            ),
            AssemblyPipelineState.INVARIANT_VIOLATION: (
                PipelineStage.ASSEMBLY_RECOVERY,
                PipelineAction.RECOVER_ASSEMBLY_INVARIANT,
                "ASSEMBLY_SUCCESS_MISSING_FINAL_CREATIVE",
            ),
        }
        if state.assembly is not AssemblyPipelineState.SUCCEEDED:
            return self._result(state, assembly[state.assembly])

        final = {
            FinalCreativePipelineState.NOT_STARTED: (
                PipelineStage.ASSEMBLY_RECOVERY,
                PipelineAction.RECOVER_ASSEMBLY_INVARIANT,
                "ASSEMBLY_SUCCESS_MISSING_FINAL_CREATIVE",
            ),
            FinalCreativePipelineState.REVIEW_REQUIRED: (
                PipelineStage.FINAL_CREATIVE_REVIEW,
                PipelineAction.REVIEW_FINAL_CREATIVE,
                "FINAL_CREATIVE_REVIEW_REQUIRED",
            ),
            FinalCreativePipelineState.REJECTED: (
                PipelineStage.ASSEMBLY_RECOVERY,
                PipelineAction.RECOVER_REJECTED_FINAL_CREATIVE,
                "FINAL_CREATIVE_REJECTED",
            ),
            FinalCreativePipelineState.APPROVED: (
                PipelineStage.FINAL_CREATIVE_READY,
                PipelineAction.FINAL_CREATIVE_READY,
                None,
            ),
        }
        return self._result(state, final[state.final_creative])

    @staticmethod
    def _result(
        state: PipelineObservation,
        rule: tuple[PipelineStage, PipelineAction, str | None],
    ) -> NextPipelineAction:
        stage, action, default_reason = rule
        execution = EXECUTION_BEHAVIOR_REGISTRY[action]
        return NextPipelineAction(
            stage=stage,
            action=action,
            blocking_reason=state.blocking_reason or default_reason,
            costs_money=execution.provider_cost,
            human_approval_required=execution.explicit_approval_required,
            provider_execution_permitted=execution.provider_execution_permitted,
            research_state=state.research,
            creative_state=state.creative,
            producer_state=state.producer,
            research_run_id=state.research_run_id,
            research_snapshot_id=state.research_snapshot_id,
            creative_run_id=state.creative_run_id,
            concept_id=state.concept_id,
            creative_revalidation_id=state.creative_revalidation_id,
            producer_run_id=state.producer_run_id,
            production_plan_id=state.production_plan_id,
            assembly_plan_id=state.assembly_plan_id,
            final_creative_id=state.final_creative_id,
            media_state=state.media,
            assembly_state=state.assembly,
            final_creative_state=state.final_creative,
            media_spend_requirement=state.media_spend_requirement,
        )
