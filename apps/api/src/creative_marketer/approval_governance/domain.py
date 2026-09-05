import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from creative_marketer.action_binding import ActionBindingV1, reject_sensitive_text
from creative_marketer.permission_governance.domain import Decision, PermissionDecision
from creative_marketer.tool_governance.domain import RiskLevel


class HumanDecision(StrEnum):
    APPROVE = "APPROVE"
    DENY = "DENY"


class ApprovalState(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class ApprovalValidationReason(StrEnum):
    VALID = "valid"
    NOT_FOUND = "not_found"
    PENDING = "pending"
    DENIED = "denied"
    REVOKED = "revoked"
    EXPIRED = "expired"
    TENANT_MISMATCH = "tenant_mismatch"
    ACTION_MISMATCH = "action_mismatch"
    POLICY_CHANGED = "policy_changed"
    AGENT_VERSION_CHANGED = "agent_version_changed"
    TOOL_VERSION_CHANGED = "tool_version_changed"
    ENVIRONMENT_CHANGED = "environment_changed"
    IDEMPOTENCY_MISMATCH = "idempotency_mismatch"
    CURRENT_PERMISSION_DENIED = "current_permission_denied"


class ApprovalError(Exception):
    code = "approval_error"


class ApprovalConflict(ApprovalError):
    code = "approval_conflict"


class ApprovalNotFound(ApprovalError):
    code = "approval_not_found"


class ApprovalForbidden(ApprovalError):
    code = "approval_forbidden"


class ApprovalExpired(ApprovalError):
    code = "approval_expired"


def approval_ttl(risk: RiskLevel) -> timedelta:
    if risk in {RiskLevel.R0, RiskLevel.R1, RiskLevel.R2, RiskLevel.R3, RiskLevel.R4}:
        return timedelta(days=7)
    if risk is RiskLevel.R5:
        return timedelta(hours=24)
    if risk is RiskLevel.R6:
        return timedelta(hours=1)
    raise ValueError("R7 Agent operations cannot enter approval")


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    binding: ActionBindingV1
    requested_by_actor_kind: str
    requested_by_actor_id: UUID
    created_at: datetime
    expires_at: datetime
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if self.requested_by_actor_kind not in {"agent", "user", "workload", "system"}:
            raise ValueError("invalid approval requester kind")
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("approval timestamps must be timezone-aware")
        if self.expires_at != self.created_at + approval_ttl(self.binding.risk_level):
            raise ValueError("approval expiry must use the bounded risk TTL")

    @property
    def tenant_id(self) -> UUID:
        return self.binding.tenant_id

    @property
    def action_digest(self) -> str:
        return self.binding.action_digest


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    approval_request_id: UUID
    tenant_id: UUID
    decision: HumanDecision
    decided_by_user_id: UUID
    decided_by_actor_kind: str
    decided_at: datetime
    reason_code: str | None = None
    safe_note: str | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if self.decided_by_actor_kind != "user":
            raise ValueError("approval decision requires a human user actor")
        if self.safe_note is not None and len(self.safe_note.encode()) > 500:
            raise ValueError("approval note must be at most 500 bytes")
        if self.safe_note is not None:
            reject_sensitive_text(self.safe_note, field_name="approval safe_note")
        if (
            self.reason_code is not None
            and re.fullmatch(r"[a-z][a-z0-9_.-]{0,99}", self.reason_code) is None
        ):
            raise ValueError("approval reason code must be canonical and bounded")
        if self.decided_at.tzinfo is None:
            raise ValueError("approval decision timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ApprovalRevocation:
    approval_request_id: UUID
    tenant_id: UUID
    revoked_by_user_id: UUID
    revoked_at: datetime
    reason_code: str
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_.-]{0,99}", self.reason_code) is None:
            raise ValueError("revocation reason code must be canonical and bounded")
        if self.revoked_at.tzinfo is None:
            raise ValueError("approval revocation timestamp must be timezone-aware")


def effective_approval_state(
    request: ApprovalRequest,
    decision: ApprovalDecision | None,
    revocation: ApprovalRevocation | None,
    now: datetime,
) -> ApprovalState:
    if revocation is not None:
        return ApprovalState.REVOKED
    if decision is not None and decision.decision is HumanDecision.DENY:
        return ApprovalState.DENIED
    if now >= request.expires_at:
        return ApprovalState.EXPIRED
    if decision is not None and decision.decision is HumanDecision.APPROVE:
        return ApprovalState.APPROVED
    return ApprovalState.PENDING


@dataclass(frozen=True, slots=True)
class ApprovalValidation:
    valid: bool
    reason: ApprovalValidationReason
    state: ApprovalState | None


class ApprovalValidator:
    def validate(
        self,
        current_permission: PermissionDecision,
        current_binding: ActionBindingV1,
        request: ApprovalRequest | None,
        decision: ApprovalDecision | None,
        revocation: ApprovalRevocation | None,
        now: datetime,
    ) -> ApprovalValidation:
        if request is None:
            return ApprovalValidation(False, ApprovalValidationReason.NOT_FOUND, None)
        if current_permission.decision is Decision.DENY:
            return ApprovalValidation(
                False,
                ApprovalValidationReason.CURRENT_PERMISSION_DENIED,
                effective_approval_state(request, decision, revocation, now),
            )
        state = effective_approval_state(request, decision, revocation, now)
        if request.tenant_id != current_binding.tenant_id:
            return ApprovalValidation(False, ApprovalValidationReason.TENANT_MISMATCH, state)
        old = request.binding
        if old.agent_version_id != current_binding.agent_version_id:
            return ApprovalValidation(False, ApprovalValidationReason.AGENT_VERSION_CHANGED, state)
        if old.tool_version_id != current_binding.tool_version_id:
            return ApprovalValidation(False, ApprovalValidationReason.TOOL_VERSION_CHANGED, state)
        if old.permission_version_id != current_binding.permission_version_id:
            return ApprovalValidation(False, ApprovalValidationReason.POLICY_CHANGED, state)
        if old.environment != current_binding.environment:
            return ApprovalValidation(False, ApprovalValidationReason.ENVIRONMENT_CHANGED, state)
        if old.idempotency_key != current_binding.idempotency_key:
            return ApprovalValidation(False, ApprovalValidationReason.IDEMPOTENCY_MISMATCH, state)
        if old.action_digest != current_binding.action_digest:
            return ApprovalValidation(False, ApprovalValidationReason.ACTION_MISMATCH, state)
        reason = {
            ApprovalState.PENDING: ApprovalValidationReason.PENDING,
            ApprovalState.DENIED: ApprovalValidationReason.DENIED,
            ApprovalState.REVOKED: ApprovalValidationReason.REVOKED,
            ApprovalState.EXPIRED: ApprovalValidationReason.EXPIRED,
            ApprovalState.APPROVED: ApprovalValidationReason.VALID,
        }[state]
        return ApprovalValidation(state is ApprovalState.APPROVED, reason, state)


def utc_now() -> datetime:
    return datetime.now(UTC)
