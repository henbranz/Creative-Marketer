import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID, uuid4

from creative_marketer.action_binding import ActionBindingV1
from creative_marketer.identity.application.authentication import ExecutionContext


class ToolCallStatus(StrEnum):
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    READY = "READY"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_PRE_EFFECT = "FAILED_PRE_EFFECT"
    UNKNOWN_EXTERNAL_OUTCOME = "UNKNOWN_EXTERNAL_OUTCOME"


class ExternalOutcome(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    CONFIRMED = "CONFIRMED"
    UNKNOWN = "UNKNOWN"
    RECONCILED = "RECONCILED"


class GatewayStatus(StrEnum):
    EXECUTED = "EXECUTED"
    REPLAYED = "REPLAYED"
    DENIED = "DENIED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    IN_PROGRESS = "IN_PROGRESS"
    FAILED_PRE_EFFECT = "FAILED_PRE_EFFECT"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    BLOCKED_RECONCILIATION = "BLOCKED_RECONCILIATION"
    INVALID_INPUT = "INVALID_INPUT"
    EXECUTOR_UNAVAILABLE = "EXECUTOR_UNAVAILABLE"
    UNSUPPORTED_OBLIGATION = "UNSUPPORTED_OBLIGATION"
    BUDGET_GUARD_UNAVAILABLE = "BUDGET_GUARD_UNAVAILABLE"
    CREDENTIAL_EXECUTION_UNAVAILABLE = "CREDENTIAL_EXECUTION_UNAVAILABLE"
    OPERATION_CONFLICT = "OPERATION_CONFLICT"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    RESULT_CONTRACT_INVALID = "RESULT_CONTRACT_INVALID"
    CRITICAL_AMBIGUOUS = "CRITICAL_AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class TrustedAgentInvocation:
    initiating_context: ExecutionContext
    requested_agent_definition_id: UUID


@dataclass(frozen=True, slots=True)
class ToolInvocationRequest:
    tool_key: str
    raw_input: object
    operation_id: str | None = None

    def __post_init__(self) -> None:
        if (
            self.operation_id is not None
            and re.fullmatch(r"op_[0-9a-f]{32}", self.operation_id) is None
        ):
            raise ValueError("operation_id must be a trusted platform operation identifier")


@dataclass(frozen=True, slots=True)
class ToolCall:
    binding: ActionBindingV1
    correlation_id: UUID
    status: ToolCallStatus
    external_outcome: ExternalOutcome = ExternalOutcome.NOT_STARTED
    approval_request_id: UUID | None = None
    idempotency_record_id: UUID | None = None
    result_ref: str | None = None
    error_code: str | None = None
    attempt_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def tenant_id(self) -> UUID:
        return self.binding.tenant_id

    @property
    def operation_id(self) -> str:
        return self.binding.idempotency_key

    @property
    def action_digest(self) -> str:
        return self.binding.action_digest


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    tenant_id: UUID
    tool_call_id: UUID
    operation_id: str
    attempt_id: UUID
    tool_definition_id: UUID
    tool_version_id: UUID
    correlation_id: UUID


@dataclass(frozen=True, slots=True)
class ToolExecutorResult:
    output: object
    result_ref: str

    def __post_init__(self) -> None:
        if re.fullmatch(r"result://[A-Za-z0-9][A-Za-z0-9._~:/-]{0,479}", self.result_ref) is None:
            raise ValueError("result_ref must be an opaque bounded result reference")


@dataclass(frozen=True, slots=True)
class GatewayResult:
    status: GatewayStatus
    operation_id: str
    tool_call_id: UUID | None = None
    approval_request_id: UUID | None = None
    idempotency_record_id: UUID | None = None
    attempt_id: UUID | None = None
    result_ref: str | None = None
    output: object | None = None
    reason_code: str | None = None


class PreEffectFailure(Exception):
    pass


class OutcomeUnknown(Exception):
    pass


def transition_call(
    call: ToolCall,
    target: ToolCallStatus,
    now: datetime,
    *,
    idempotency_record_id: UUID | None = None,
    attempt_id: UUID | None = None,
    result_ref: str | None = None,
    error_code: str | None = None,
) -> ToolCall:
    allowed = {
        ToolCallStatus.READY: {ToolCallStatus.EXECUTING},
        ToolCallStatus.AWAITING_APPROVAL: {ToolCallStatus.EXECUTING},
        ToolCallStatus.FAILED_PRE_EFFECT: {ToolCallStatus.EXECUTING},
        ToolCallStatus.EXECUTING: {
            ToolCallStatus.SUCCEEDED,
            ToolCallStatus.FAILED_PRE_EFFECT,
            ToolCallStatus.UNKNOWN_EXTERNAL_OUTCOME,
        },
        ToolCallStatus.UNKNOWN_EXTERNAL_OUTCOME: {ToolCallStatus.EXECUTING},
    }
    if target not in allowed.get(call.status, set()):
        raise ValueError("invalid ToolCall transition")
    if target is ToolCallStatus.EXECUTING:
        if idempotency_record_id is None or attempt_id is None:
            raise ValueError("execution requires idempotency and attempt ownership")
        return replace(
            call,
            status=target,
            external_outcome=ExternalOutcome.NOT_STARTED,
            idempotency_record_id=idempotency_record_id,
            attempt_id=attempt_id,
            started_at=now,
            updated_at=now,
        )
    outcome = {
        ToolCallStatus.SUCCEEDED: ExternalOutcome.CONFIRMED,
        ToolCallStatus.FAILED_PRE_EFFECT: ExternalOutcome.NOT_STARTED,
        ToolCallStatus.UNKNOWN_EXTERNAL_OUTCOME: ExternalOutcome.UNKNOWN,
    }[target]
    return replace(
        call,
        status=target,
        external_outcome=outcome,
        attempt_id=None,
        result_ref=result_ref,
        error_code=error_code,
        completed_at=now,
        updated_at=now,
    )


def immutable_output(value: object) -> object:
    return MappingProxyType(value) if isinstance(value, dict) else value
