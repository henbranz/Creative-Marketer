from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from creative_marketer.agent_runtime.domain import canonical_digest

CYCLE_STATE_MACHINE_VERSION = "creative-cycle-v1"
SUPERVISOR_CONTEXT_VERSION = 1
SUPERVISOR_REPORT_VERSION = 1


class OrchestrationError(Exception):
    code = "ORCHESTRATION_ERROR"


class CycleNotFound(OrchestrationError):
    code = "CYCLE_NOT_FOUND"


class CyclePermissionDenied(OrchestrationError):
    code = "CYCLE_PERMISSION_DENIED"


class CycleConflict(OrchestrationError):
    code = "CYCLE_CONFLICT"


class CycleNotReady(OrchestrationError):
    code = "CYCLE_NOT_READY"


class IllegalCycleTransition(OrchestrationError):
    code = "ILLEGAL_CYCLE_TRANSITION"


class CycleStatus(StrEnum):
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"
    NEEDS_RECOVERY = "NEEDS_RECOVERY"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class CycleMode(StrEnum):
    ASSISTED = "ASSISTED"


class CycleStage(StrEnum):
    CREATED = "CREATED"
    CHECKING_READINESS = "CHECKING_READINESS"
    RESEARCHING = "RESEARCHING"
    CREATIVE_STRATEGY = "CREATIVE_STRATEGY"
    AWAITING_CONCEPT_APPROVAL = "AWAITING_CONCEPT_APPROVAL"
    PRODUCTION_PLANNING = "PRODUCTION_PLANNING"
    AWAITING_PRODUCTION_APPROVAL = "AWAITING_PRODUCTION_APPROVAL"
    GENERATING_MEDIA = "GENERATING_MEDIA"
    ASSEMBLING_FINAL_CREATIVE = "ASSEMBLING_FINAL_CREATIVE"
    AWAITING_FINAL_CREATIVE_APPROVAL = "AWAITING_FINAL_CREATIVE_APPROVAL"
    AWAITING_PUBLICATION_INPUT = "AWAITING_PUBLICATION_INPUT"
    AWAITING_PUBLICATION_APPROVAL = "AWAITING_PUBLICATION_APPROVAL"
    PUBLISHING = "PUBLISHING"
    MEASURING = "MEASURING"
    INTELLIGENCE = "INTELLIGENCE"
    AWAITING_EXPERIMENT_DECISION = "AWAITING_EXPERIMENT_DECISION"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ReadinessState(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"


class StepStatus(StrEnum):
    STARTED = "STARTED"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    NEEDS_RECOVERY = "NEEDS_RECOVERY"


class SupervisorAction(StrEnum):
    COMPLETE_BRIEF = "COMPLETE_BRIEF"
    ADD_RESEARCH_SOURCE = "ADD_RESEARCH_SOURCE"
    APPROVE_CONCEPT = "APPROVE_CONCEPT"
    APPROVE_PRODUCTION_PLAN = "APPROVE_PRODUCTION_PLAN"
    PREPARE_FINAL_ASSEMBLY = "PREPARE_FINAL_ASSEMBLY"
    REVIEW_FINAL_CREATIVE = "REVIEW_FINAL_CREATIVE"
    PREPARE_PUBLICATION = "PREPARE_PUBLICATION"
    APPROVE_PUBLICATION = "APPROVE_PUBLICATION"
    WAIT_FOR_MEASUREMENT = "WAIT_FOR_MEASUREMENT"
    REVIEW_INSIGHTS = "REVIEW_INSIGHTS"
    REVIEW_EXPERIMENT = "REVIEW_EXPERIMENT"
    RECOVER_AGENT_RUN = "RECOVER_AGENT_RUN"


_LEGAL_NEXT: Mapping[CycleStage, frozenset[CycleStage]] = {
    CycleStage.CREATED: frozenset({CycleStage.CHECKING_READINESS, CycleStage.CANCELLED}),
    CycleStage.CHECKING_READINESS: frozenset(
        {CycleStage.RESEARCHING, CycleStage.BLOCKED, CycleStage.CANCELLED}
    ),
    CycleStage.BLOCKED: frozenset({CycleStage.CHECKING_READINESS, CycleStage.CANCELLED}),
    CycleStage.RESEARCHING: frozenset(
        {CycleStage.CREATIVE_STRATEGY, CycleStage.FAILED, CycleStage.CANCELLED}
    ),
    CycleStage.CREATIVE_STRATEGY: frozenset(
        {CycleStage.AWAITING_CONCEPT_APPROVAL, CycleStage.FAILED, CycleStage.CANCELLED}
    ),
    CycleStage.AWAITING_CONCEPT_APPROVAL: frozenset(
        {CycleStage.PRODUCTION_PLANNING, CycleStage.CREATIVE_STRATEGY, CycleStage.CANCELLED}
    ),
    CycleStage.PRODUCTION_PLANNING: frozenset(
        {CycleStage.AWAITING_PRODUCTION_APPROVAL, CycleStage.FAILED, CycleStage.CANCELLED}
    ),
    CycleStage.AWAITING_PRODUCTION_APPROVAL: frozenset(
        {CycleStage.GENERATING_MEDIA, CycleStage.CANCELLED}
    ),
    CycleStage.GENERATING_MEDIA: frozenset(
        {CycleStage.ASSEMBLING_FINAL_CREATIVE, CycleStage.FAILED, CycleStage.CANCELLED}
    ),
    CycleStage.ASSEMBLING_FINAL_CREATIVE: frozenset(
        {CycleStage.AWAITING_FINAL_CREATIVE_APPROVAL, CycleStage.FAILED, CycleStage.CANCELLED}
    ),
    CycleStage.AWAITING_FINAL_CREATIVE_APPROVAL: frozenset(
        {CycleStage.AWAITING_PUBLICATION_INPUT, CycleStage.CANCELLED}
    ),
    CycleStage.AWAITING_PUBLICATION_INPUT: frozenset(
        {CycleStage.AWAITING_PUBLICATION_APPROVAL, CycleStage.CANCELLED}
    ),
    CycleStage.AWAITING_PUBLICATION_APPROVAL: frozenset(
        {CycleStage.PUBLISHING, CycleStage.CANCELLED}
    ),
    CycleStage.PUBLISHING: frozenset(
        {CycleStage.MEASURING, CycleStage.FAILED, CycleStage.CANCELLED}
    ),
    CycleStage.MEASURING: frozenset(
        {CycleStage.INTELLIGENCE, CycleStage.FAILED, CycleStage.CANCELLED}
    ),
    CycleStage.INTELLIGENCE: frozenset(
        {
            CycleStage.AWAITING_EXPERIMENT_DECISION,
            CycleStage.COMPLETED,
            CycleStage.FAILED,
            CycleStage.CANCELLED,
        }
    ),
    CycleStage.AWAITING_EXPERIMENT_DECISION: frozenset(
        {CycleStage.COMPLETED, CycleStage.CANCELLED}
    ),
}


def validate_transition(current: CycleStage, target: CycleStage) -> None:
    if target not in _LEGAL_NEXT.get(current, frozenset()):
        raise IllegalCycleTransition(f"{current.value} cannot transition to {target.value}")


@dataclass(frozen=True, slots=True)
class ReadinessRequirement:
    key: str
    state: ReadinessState
    message: str
    required: bool = True
    resource_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.key or len(self.key) > 100 or not self.message or len(self.message) > 500:
            raise ValueError("readiness requirement must be bounded")


@dataclass(frozen=True, slots=True)
class CycleReadiness:
    state: ReadinessState
    requirements: tuple[ReadinessRequirement, ...]
    allowed_actions: tuple[SupervisorAction, ...] = ()


@dataclass(frozen=True, slots=True)
class CycleArtifacts:
    research_run_id: UUID | None = None
    research_snapshot_id: UUID | None = None
    creative_run_id: UUID | None = None
    creative_concept_set_id: UUID | None = None
    approved_concept_id: UUID | None = None
    producer_run_id: UUID | None = None
    production_plan_id: UUID | None = None
    final_creative_id: UUID | None = None
    publication_draft_id: UUID | None = None
    publication_id: UUID | None = None
    performance_snapshot_id: UUID | None = None
    intelligence_run_id: UUID | None = None
    intelligence_report_id: UUID | None = None
    experiment_proposal_id: UUID | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            key: str(value) if value else None
            for key, value in {
                name: getattr(self, name) for name in self.__dataclass_fields__
            }.items()
        }


@dataclass(frozen=True, slots=True)
class CreativeCycle:
    tenant_id: UUID
    product_id: UUID
    initiated_by: UUID
    product_snapshot_id: UUID
    product_snapshot_digest: str
    id: UUID = field(default_factory=uuid4)
    status: CycleStatus = CycleStatus.ACTIVE
    current_stage: CycleStage = CycleStage.CREATED
    mode: CycleMode = CycleMode.ASSISTED
    artifacts: CycleArtifacts = field(default_factory=CycleArtifacts)
    parent_cycle_id: UUID | None = None
    source_experiment_proposal_id: UUID | None = None
    blocker_code: str | None = None
    failure_code: str | None = None
    state_machine_version: str = CYCLE_STATE_MACHINE_VERSION
    cycle_version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.state_machine_version != CYCLE_STATE_MACHINE_VERSION:
            raise ValueError("unsupported CreativeCycle state machine")
        if not self.product_snapshot_digest.startswith("sha256:"):
            raise ValueError("cycle requires an immutable Product snapshot digest")

    def transition(
        self,
        target: CycleStage,
        *,
        status: CycleStatus | None = None,
        blocker_code: str | None = None,
        failure_code: str | None = None,
        artifacts: CycleArtifacts | None = None,
        now: datetime | None = None,
    ) -> CreativeCycle:
        validate_transition(self.current_stage, target)
        resolved_status = status or (
            CycleStatus.COMPLETED
            if target is CycleStage.COMPLETED
            else CycleStatus.CANCELLED
            if target is CycleStage.CANCELLED
            else CycleStatus.FAILED
            if target is CycleStage.FAILED
            else CycleStatus.BLOCKED
            if target is CycleStage.BLOCKED
            else CycleStatus.ACTIVE
        )
        return replace(
            self,
            status=resolved_status,
            current_stage=target,
            artifacts=artifacts or self.artifacts,
            blocker_code=blocker_code,
            failure_code=failure_code,
            cycle_version=self.cycle_version + 1,
            updated_at=now or datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class CycleTransition:
    tenant_id: UUID
    cycle_id: UUID
    from_stage: CycleStage | None
    to_stage: CycleStage
    status: CycleStatus
    reason_code: str
    actor_kind: str
    actor_id: UUID
    correlation_id: UUID
    id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class CreativeCycleStep:
    tenant_id: UUID
    cycle_id: UUID
    step_key: str
    attempt: int
    status: StepStatus
    idempotency_key: str
    agent_run_id: UUID | None = None
    workflow_ref: str | None = None
    artifact_ref: str | None = None
    failure_code: str | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SupervisorContextManifest:
    tenant_id: UUID
    product_id: UUID
    cycle_id: UUID
    cycle_version: int
    current_stage: CycleStage
    readiness: CycleReadiness
    product_snapshot_id: UUID
    product_snapshot_digest: str
    artifacts: CycleArtifacts
    pending_approvals: tuple[str, ...]
    provider_mode: str = "DEMO_FAKE"
    schema_version: int = SUPERVISOR_CONTEXT_VERSION
    id: UUID = field(default_factory=uuid4)
    semantic_digest: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.schema_version != SUPERVISOR_CONTEXT_VERSION or self.provider_mode != "DEMO_FAKE":
            raise ValueError("unsupported Supervisor context")
        content = {
            "schema_version": self.schema_version,
            "cycle_id": str(self.cycle_id),
            "cycle_version": self.cycle_version,
            "current_stage": self.current_stage.value,
            "product_snapshot_id": str(self.product_snapshot_id),
            "product_snapshot_digest": self.product_snapshot_digest,
            "artifacts": self.artifacts.as_dict(),
            "requirements": [
                {"key": item.key, "state": item.state.value, "required": item.required}
                for item in self.readiness.requirements
            ],
            "allowed_actions": [item.value for item in self.readiness.allowed_actions],
            "pending_approvals": list(self.pending_approvals),
            "provider_mode": self.provider_mode,
        }
        expected = canonical_digest(content)
        if self.semantic_digest and self.semantic_digest != expected:
            raise ValueError("Supervisor context digest mismatch")
        object.__setattr__(self, "semantic_digest", expected)


@dataclass(frozen=True, slots=True)
class SupervisorReport:
    tenant_id: UUID
    product_id: UUID
    cycle_id: UUID
    context_manifest_id: UUID
    context_manifest_digest: str
    summary: str
    current_stage_explanation: str
    blockers: tuple[str, ...]
    attention_items: tuple[str, ...]
    suggested_next_actions: tuple[SupervisorAction, ...]
    completion_summary: str | None = None
    agent_run_id: UUID | None = None
    schema_version: int = SUPERVISOR_REPORT_VERSION
    id: UUID = field(default_factory=uuid4)
    semantic_digest: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.schema_version != SUPERVISOR_REPORT_VERSION:
            raise ValueError("unsupported Supervisor report")
        for text in (
            self.summary,
            self.current_stage_explanation,
            *self.blockers,
            *self.attention_items,
        ):
            if not text.strip() or len(text) > 1000:
                raise ValueError("Supervisor report text must be bounded")
        content = {
            "schema_version": self.schema_version,
            "cycle_id": str(self.cycle_id),
            "context_manifest_id": str(self.context_manifest_id),
            "context_manifest_digest": self.context_manifest_digest,
            "summary": self.summary,
            "current_stage_explanation": self.current_stage_explanation,
            "blockers": list(self.blockers),
            "attention_items": list(self.attention_items),
            "suggested_next_actions": [item.value for item in self.suggested_next_actions],
            "completion_summary": self.completion_summary,
        }
        expected = canonical_digest(content)
        if self.semantic_digest and self.semantic_digest != expected:
            raise ValueError("Supervisor report digest mismatch")
        object.__setattr__(self, "semantic_digest", expected)


def validate_supervisor_actions(
    report: SupervisorReport, allowed: tuple[SupervisorAction, ...]
) -> None:
    if not set(report.suggested_next_actions).issubset(set(allowed)):
        raise ValueError("Supervisor suggested an action outside deterministic readiness")
