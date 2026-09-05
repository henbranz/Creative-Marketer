import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from creative_marketer.action_binding import ActionBindingV1, reject_sensitive_text

DEFAULT_EXECUTION_LEASE = timedelta(minutes=5)
RESULT_REF = re.compile(r"^result://[A-Za-z0-9][A-Za-z0-9._~:/-]{0,479}$")


class IdempotencyState(StrEnum):
    RESERVED = "RESERVED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_PRE_EFFECT = "FAILED_PRE_EFFECT"
    UNKNOWN_EXTERNAL_OUTCOME = "UNKNOWN_EXTERNAL_OUTCOME"
    RECONCILED = "RECONCILED"


class ReconciliationOutcome(StrEnum):
    EFFECT_CONFIRMED = "EFFECT_CONFIRMED"
    NO_EFFECT_CONFIRMED = "NO_EFFECT_CONFIRMED"


class ReservationOutcome(StrEnum):
    NEW_RESERVATION = "NEW_RESERVATION"
    EXISTING_PENDING = "EXISTING_PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    REPLAY_SUCCEEDED = "REPLAY_SUCCEEDED"
    RETRY_ALLOWED = "RETRY_ALLOWED"
    UNKNOWN_REQUIRES_RECONCILIATION = "UNKNOWN_REQUIRES_RECONCILIATION"
    CONFLICT = "CONFLICT"


class AttemptAcquisitionOutcome(StrEnum):
    ACQUIRED = "ACQUIRED"
    IN_PROGRESS = "IN_PROGRESS"
    REPLAY_SUCCEEDED = "REPLAY_SUCCEEDED"
    UNKNOWN_REQUIRES_RECONCILIATION = "UNKNOWN_REQUIRES_RECONCILIATION"


class IdempotencyError(Exception):
    code = "idempotency_error"


class IdempotencyNotFound(IdempotencyError):
    code = "idempotency_not_found"


class IdempotencyConflict(IdempotencyError):
    code = "idempotency_conflict"


class InvalidIdempotencyTransition(IdempotencyError):
    code = "invalid_idempotency_transition"


class StaleExecutionAttempt(IdempotencyError):
    code = "stale_execution_attempt"


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    tenant_id: UUID
    tool_definition_id: UUID
    tool_version_id: UUID
    idempotency_key: str
    request_digest: str
    state: IdempotencyState = IdempotencyState.RESERVED
    attempt_count: int = 0
    current_attempt_id: UUID | None = None
    lease_expires_at: datetime | None = None
    result_ref: str | None = None
    reconciliation_outcome: ReconciliationOutcome | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_binding(cls, binding: ActionBindingV1) -> "IdempotencyRecord":
        return cls(
            tenant_id=binding.tenant_id,
            tool_definition_id=binding.tool_definition_id,
            tool_version_id=binding.tool_version_id,
            idempotency_key=binding.idempotency_key,
            request_digest=binding.action_digest,
        )

    def __post_init__(self) -> None:
        if self.attempt_count < 0:
            raise ValueError("attempt_count cannot be negative")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.request_digest):
            raise ValueError("request_digest must be SHA-256")
        if self.result_ref is not None and not RESULT_REF.fullmatch(self.result_ref):
            raise ValueError("result_ref must be a bounded internal result URI")
        if self.result_ref is not None:
            reject_sensitive_text(self.result_ref, field_name="idempotency result_ref")
        if self.state is IdempotencyState.EXECUTING:
            if self.current_attempt_id is None or self.lease_expires_at is None:
                raise ValueError("executing state requires attempt and lease")
        elif self.current_attempt_id is not None or self.lease_expires_at is not None:
            raise ValueError("only executing state may carry attempt ownership")
        if (self.state is IdempotencyState.RECONCILED) != (self.reconciliation_outcome is not None):
            raise ValueError("reconciled state and outcome must appear together")


def reservation_outcome(record: IdempotencyRecord, request_digest: str) -> ReservationOutcome:
    if record.request_digest != request_digest:
        return ReservationOutcome.CONFLICT
    if record.state is IdempotencyState.RESERVED:
        return ReservationOutcome.EXISTING_PENDING
    if record.state is IdempotencyState.EXECUTING:
        return ReservationOutcome.IN_PROGRESS
    if record.state is IdempotencyState.SUCCEEDED or (
        record.state is IdempotencyState.RECONCILED
        and record.reconciliation_outcome is ReconciliationOutcome.EFFECT_CONFIRMED
    ):
        return ReservationOutcome.REPLAY_SUCCEEDED
    if record.state is IdempotencyState.FAILED_PRE_EFFECT or (
        record.state is IdempotencyState.RECONCILED
        and record.reconciliation_outcome is ReconciliationOutcome.NO_EFFECT_CONFIRMED
    ):
        return ReservationOutcome.RETRY_ALLOWED
    return ReservationOutcome.UNKNOWN_REQUIRES_RECONCILIATION


def begin_attempt(
    record: IdempotencyRecord,
    now: datetime,
    lease_duration: timedelta = DEFAULT_EXECUTION_LEASE,
) -> tuple[IdempotencyRecord, AttemptAcquisitionOutcome]:
    outcome = reservation_outcome(record, record.request_digest)
    if outcome in {ReservationOutcome.EXISTING_PENDING, ReservationOutcome.RETRY_ALLOWED}:
        return (
            replace(
                record,
                state=IdempotencyState.EXECUTING,
                attempt_count=record.attempt_count + 1,
                current_attempt_id=uuid4(),
                lease_expires_at=now + lease_duration,
                result_ref=None,
                reconciliation_outcome=None,
                updated_at=now,
            ),
            AttemptAcquisitionOutcome.ACQUIRED,
        )
    mapped = {
        ReservationOutcome.IN_PROGRESS: AttemptAcquisitionOutcome.IN_PROGRESS,
        ReservationOutcome.REPLAY_SUCCEEDED: AttemptAcquisitionOutcome.REPLAY_SUCCEEDED,
        ReservationOutcome.UNKNOWN_REQUIRES_RECONCILIATION: (
            AttemptAcquisitionOutcome.UNKNOWN_REQUIRES_RECONCILIATION
        ),
    }
    return record, mapped[outcome]


def complete_attempt(
    record: IdempotencyRecord,
    attempt_id: UUID,
    target: IdempotencyState,
    now: datetime,
    *,
    result_ref: str | None = None,
) -> IdempotencyRecord:
    if record.state is not IdempotencyState.EXECUTING or record.current_attempt_id != attempt_id:
        raise StaleExecutionAttempt("only the current execution attempt may complete")
    if target not in {
        IdempotencyState.SUCCEEDED,
        IdempotencyState.FAILED_PRE_EFFECT,
        IdempotencyState.UNKNOWN_EXTERNAL_OUTCOME,
    }:
        raise InvalidIdempotencyTransition("invalid execution completion state")
    if target is IdempotencyState.SUCCEEDED and result_ref is None:
        raise InvalidIdempotencyTransition("success requires a safe result reference")
    if target is not IdempotencyState.SUCCEEDED and result_ref is not None:
        raise InvalidIdempotencyTransition("only success may store result_ref")
    return replace(
        record,
        state=target,
        current_attempt_id=None,
        lease_expires_at=None,
        result_ref=result_ref,
        updated_at=now,
    )


def reconcile(
    record: IdempotencyRecord,
    outcome: ReconciliationOutcome,
    now: datetime,
    *,
    result_ref: str | None = None,
) -> IdempotencyRecord:
    if record.state is not IdempotencyState.UNKNOWN_EXTERNAL_OUTCOME:
        raise InvalidIdempotencyTransition("only unknown outcomes may be reconciled")
    if outcome is ReconciliationOutcome.EFFECT_CONFIRMED and result_ref is None:
        raise InvalidIdempotencyTransition("confirmed effect requires a result reference")
    if outcome is ReconciliationOutcome.NO_EFFECT_CONFIRMED and result_ref is not None:
        raise InvalidIdempotencyTransition("no-effect reconciliation cannot store a result")
    return replace(
        record,
        state=IdempotencyState.RECONCILED,
        reconciliation_outcome=outcome,
        result_ref=result_ref,
        updated_at=now,
    )
