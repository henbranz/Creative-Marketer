from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
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
    CONTRACT_UPGRADE_REQUIRED = "CONTRACT_UPGRADE_REQUIRED"
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
    UPGRADE_PRODUCER_CONTRACT = "UPGRADE_PRODUCER_CONTRACT"
    REVIEW_PRODUCTION_PLAN = "REVIEW_PRODUCTION_PLAN"
    READY_FOR_GENERATION = "READY_FOR_GENERATION"


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
                True,
                True,
                True,
            ),
            ResearchPipelineState.PENDING: (
                PipelineStage.RESEARCH,
                PipelineAction.WAIT_FOR_RESEARCH,
                "RESEARCH_RUN_PENDING",
                False,
                False,
                False,
            ),
            ResearchPipelineState.RUNNING: (
                PipelineStage.RESEARCH,
                PipelineAction.WAIT_FOR_RESEARCH,
                "RESEARCH_RUN_IN_PROGRESS",
                False,
                False,
                False,
            ),
            ResearchPipelineState.SUCCEEDED_EXPIRED: (
                PipelineStage.CREATIVE_AUTHORITY_REFRESH,
                PipelineAction.REFRESH_RESEARCH,
                "RESEARCH_AUTHORITY_EXPIRED",
                True,
                True,
                True,
            ),
            ResearchPipelineState.FAILED_BEFORE_PROVIDER: (
                PipelineStage.RESEARCH_RECOVERY,
                PipelineAction.RETRY_RESEARCH,
                "RESEARCH_FAILED_BEFORE_PROVIDER",
                True,
                True,
                True,
            ),
            ResearchPipelineState.FAILED_NO_RESPONSE: (
                PipelineStage.RESEARCH_RECOVERY,
                PipelineAction.RETRY_RESEARCH,
                "RESEARCH_PROVIDER_RETURNED_NO_RESPONSE",
                True,
                True,
                True,
            ),
            ResearchPipelineState.FAILED_RESPONSE: (
                PipelineStage.RESEARCH_RECOVERY,
                PipelineAction.RERUN_RESEARCH,
                "RESEARCH_RESPONSE_FAILED_VALIDATION",
                True,
                True,
                True,
            ),
            ResearchPipelineState.OUTCOME_UNKNOWN: (
                PipelineStage.RESEARCH_RECOVERY,
                PipelineAction.RECONCILE_RESEARCH_OUTCOME,
                "RESEARCH_PROVIDER_OUTCOME_UNKNOWN",
                False,
                True,
                False,
            ),
        }
        if state.research is not ResearchPipelineState.SUCCEEDED_CURRENT:
            return self._result(state, research[state.research])

        creative = {
            CreativePipelineState.NOT_STARTED: (
                PipelineStage.CREATIVE,
                PipelineAction.RUN_CREATIVE,
                None,
                True,
                True,
                True,
            ),
            CreativePipelineState.PENDING: (
                PipelineStage.CREATIVE,
                PipelineAction.WAIT_FOR_CREATIVE,
                "CREATIVE_RUN_PENDING",
                False,
                False,
                False,
            ),
            CreativePipelineState.RUNNING: (
                PipelineStage.CREATIVE,
                PipelineAction.WAIT_FOR_CREATIVE,
                "CREATIVE_RUN_IN_PROGRESS",
                False,
                False,
                False,
            ),
            CreativePipelineState.FAILED: (
                PipelineStage.CREATIVE_RECOVERY,
                PipelineAction.RERUN_CREATIVE,
                "CREATIVE_RUN_FAILED",
                True,
                True,
                True,
            ),
            CreativePipelineState.OUTCOME_UNKNOWN: (
                PipelineStage.CREATIVE_RECOVERY,
                PipelineAction.RECONCILE_CREATIVE_OUTCOME,
                "CREATIVE_PROVIDER_OUTCOME_UNKNOWN",
                False,
                True,
                False,
            ),
            CreativePipelineState.OUTPUT_LIMITED: (
                PipelineStage.CREATIVE_RECOVERY,
                PipelineAction.REPLACE_OUTPUT_LIMITED_CREATIVE,
                "CREATIVE_OUTPUT_TOKEN_LIMIT_REACHED",
                True,
                True,
                True,
            ),
            CreativePipelineState.SUCCEEDED: (
                PipelineStage.CREATIVE_APPROVAL,
                PipelineAction.APPROVE_CREATIVE,
                "CREATIVE_CONCEPT_SELECTION_REQUIRED",
                False,
                True,
                False,
            ),
            CreativePipelineState.APPROVAL_REQUIRED: (
                PipelineStage.CREATIVE_APPROVAL,
                PipelineAction.APPROVE_CREATIVE,
                "CREATIVE_APPROVAL_REQUIRED",
                False,
                True,
                False,
            ),
            CreativePipelineState.REJECTED: (
                PipelineStage.CREATIVE_RESTRATEGY,
                PipelineAction.RESTRATEGIZE_CREATIVE,
                "CREATIVE_CONCEPT_REJECTED",
                True,
                True,
                True,
            ),
            CreativePipelineState.STALE_RESEARCH: (
                PipelineStage.CREATIVE_REVALIDATION,
                PipelineAction.REVALIDATE_CREATIVE,
                "APPROVED_CREATIVE_USES_HISTORICAL_RESEARCH",
                False,
                True,
                False,
            ),
            CreativePipelineState.REQUIRES_RESTRATEGY: (
                PipelineStage.CREATIVE_RESTRATEGY,
                PipelineAction.RESTRATEGIZE_CREATIVE,
                "CURRENT_AUTHORITY_MATERIALLY_DIFFERS",
                True,
                True,
                True,
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
                True,
                True,
                True,
            ),
            ProducerPipelineState.PENDING: (
                PipelineStage.PRODUCER,
                PipelineAction.WAIT_FOR_PRODUCER,
                "PRODUCER_RUN_PENDING",
                False,
                False,
                False,
            ),
            ProducerPipelineState.RUNNING: (
                PipelineStage.PRODUCER,
                PipelineAction.WAIT_FOR_PRODUCER,
                "PRODUCER_RUN_IN_PROGRESS",
                False,
                False,
                False,
            ),
            ProducerPipelineState.FAILED_NO_RESPONSE: (
                PipelineStage.PRODUCER_RECOVERY,
                PipelineAction.RETRY_PRODUCER,
                "PRODUCER_PROVIDER_RETURNED_NO_RESPONSE",
                True,
                True,
                True,
            ),
            ProducerPipelineState.FAILED_RESPONSE: (
                PipelineStage.PRODUCER_RECOVERY,
                PipelineAction.RERUN_PRODUCER,
                "PRODUCER_RESPONSE_FAILED",
                True,
                True,
                True,
            ),
            ProducerPipelineState.OUTCOME_UNKNOWN: (
                PipelineStage.PRODUCER_RECOVERY,
                PipelineAction.RECONCILE_PRODUCER_OUTCOME,
                "PRODUCER_PROVIDER_OUTCOME_UNKNOWN",
                False,
                True,
                False,
            ),
            ProducerPipelineState.PRODUCTION_PLAN_INVALID: (
                PipelineStage.PRODUCER_RECOVERY,
                PipelineAction.RERUN_PRODUCER,
                "PRODUCTION_PLAN_FAILED_CURRENT_CONTRACT_VALIDATION",
                True,
                True,
                True,
            ),
            ProducerPipelineState.CONTRACT_UPGRADE_REQUIRED: (
                PipelineStage.PRODUCER_RECOVERY,
                PipelineAction.UPGRADE_PRODUCER_CONTRACT,
                "PRODUCTION_PLAN_USED_HISTORICAL_CONTRACT",
                True,
                True,
                True,
            ),
            ProducerPipelineState.SUCCEEDED: (
                PipelineStage.PRODUCTION_PLAN_REVIEW,
                PipelineAction.REVIEW_PRODUCTION_PLAN,
                "PRODUCTION_PLAN_REVIEW_REQUIRED",
                False,
                True,
                False,
            ),
            ProducerPipelineState.PRODUCTION_PLAN_REVIEW_REQUIRED: (
                PipelineStage.PRODUCTION_PLAN_REVIEW,
                PipelineAction.REVIEW_PRODUCTION_PLAN,
                "PRODUCTION_PLAN_REVIEW_REQUIRED",
                False,
                True,
                False,
            ),
            ProducerPipelineState.PRODUCTION_PLAN_REJECTED: (
                PipelineStage.PRODUCER_RECOVERY,
                PipelineAction.RERUN_PRODUCER,
                "PRODUCTION_PLAN_REJECTED",
                True,
                True,
                True,
            ),
            ProducerPipelineState.APPROVED_FOR_GENERATION: (
                PipelineStage.READY_FOR_GENERATION,
                PipelineAction.READY_FOR_GENERATION,
                None,
                False,
                False,
                False,
            ),
        }
        return self._result(state, producer[state.producer])

    @staticmethod
    def _result(
        state: PipelineObservation,
        rule: tuple[PipelineStage, PipelineAction, str | None, bool, bool, bool],
    ) -> NextPipelineAction:
        stage, action, default_reason, costs, approval, provider = rule
        return NextPipelineAction(
            stage,
            action,
            state.blocking_reason or default_reason,
            costs,
            approval,
            provider,
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
