from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Protocol
from uuid import UUID

from creative_marketer.action_binding import ActionBindingV1
from creative_marketer.audit.application import AuditWriter
from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditOutcome, AuditRecord
from creative_marketer.audit.safety import safe_metadata
from creative_marketer.execution_control.domain import (
    DEFAULT_EXECUTION_LEASE,
    AttemptAcquisitionOutcome,
    IdempotencyConflict,
    IdempotencyNotFound,
    IdempotencyRecord,
    IdempotencyState,
    InvalidIdempotencyTransition,
    ReconciliationOutcome,
    ReservationOutcome,
    begin_attempt,
    complete_attempt,
    reconcile,
    reservation_outcome,
)
from creative_marketer.identity.application.authentication import ActorKind, ExecutionContext
from creative_marketer.identity.application.context import TenantContext
from creative_marketer.identity.domain import MembershipRole, MembershipStatus


class IdempotencyRepository(Protocol):
    async def reserve(self, candidate: IdempotencyRecord) -> tuple[IdempotencyRecord, bool]: ...
    async def get(
        self, record_id: UUID, *, for_update: bool = False
    ) -> IdempotencyRecord | None: ...
    async def get_by_key(
        self, tool_definition_id: UUID, idempotency_key: str
    ) -> IdempotencyRecord | None: ...
    async def update(self, record: IdempotencyRecord) -> None: ...


class IdempotencyUnitOfWork(Protocol):
    records: IdempotencyRepository
    audit: AuditWriter

    async def __aenter__(self) -> "IdempotencyUnitOfWork": ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class IdempotencyUnitOfWorkFactory(Protocol):
    def __call__(self, context: TenantContext) -> IdempotencyUnitOfWork: ...


@dataclass(frozen=True, slots=True)
class ReservationResult:
    record: IdempotencyRecord
    outcome: ReservationOutcome


@dataclass(slots=True)
class ReserveIdempotentOperation:
    uow_factory: IdempotencyUnitOfWorkFactory

    async def __call__(
        self, context: ExecutionContext, binding: ActionBindingV1
    ) -> ReservationResult:
        if binding.tenant_id != context.tenant_id:
            raise IdempotencyConflict("action binding tenant does not match trusted context")
        candidate = IdempotencyRecord.from_binding(binding)
        async with self.uow_factory(context.tenant_context()) as uow:
            record, created = await uow.records.reserve(candidate)
            outcome = (
                ReservationOutcome.NEW_RESERVATION
                if created
                else reservation_outcome(record, binding.action_digest)
            )
            action = (
                "idempotency.conflict"
                if outcome is ReservationOutcome.CONFLICT
                else "idempotency.reserved"
            )
            await uow.audit.append(
                _audit(
                    context,
                    record,
                    action,
                    AuditOutcome.DENIED
                    if outcome is ReservationOutcome.CONFLICT
                    else AuditOutcome.SUCCESS,
                    outcome.value.lower(),
                )
            )
            await uow.commit()
            return ReservationResult(record, outcome)


@dataclass(frozen=True, slots=True)
class AttemptAcquisition:
    record: IdempotencyRecord
    outcome: AttemptAcquisitionOutcome


@dataclass(slots=True)
class BeginExecutionAttempt:
    uow_factory: IdempotencyUnitOfWorkFactory
    lease_duration: timedelta = DEFAULT_EXECUTION_LEASE
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    async def __call__(self, context: ExecutionContext, record_id: UUID) -> AttemptAcquisition:
        async with self.uow_factory(context.tenant_context()) as uow:
            current = await uow.records.get(record_id, for_update=True)
            if current is None:
                raise IdempotencyNotFound("idempotency record was not found")
            changed, outcome = begin_attempt(current, self.clock(), self.lease_duration)
            if outcome is AttemptAcquisitionOutcome.ACQUIRED:
                await uow.records.update(changed)
                await uow.audit.append(
                    _audit(
                        context,
                        changed,
                        "idempotency.execution.started",
                        AuditOutcome.SUCCESS,
                        outcome.value.lower(),
                    )
                )
                await uow.commit()
            return AttemptAcquisition(changed, outcome)


@dataclass(slots=True)
class CompleteExecutionAttempt:
    uow_factory: IdempotencyUnitOfWorkFactory
    target: IdempotencyState
    audit_action: str
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    async def __call__(
        self,
        context: ExecutionContext,
        record_id: UUID,
        attempt_id: UUID,
        *,
        result_ref: str | None = None,
    ) -> IdempotencyRecord:
        async with self.uow_factory(context.tenant_context()) as uow:
            current = await uow.records.get(record_id, for_update=True)
            if current is None:
                raise IdempotencyNotFound("idempotency record was not found")
            changed = complete_attempt(
                current, attempt_id, self.target, self.clock(), result_ref=result_ref
            )
            await uow.records.update(changed)
            await uow.audit.append(
                _audit(
                    context,
                    changed,
                    self.audit_action,
                    AuditOutcome.SUCCESS,
                    self.target.value,
                    attempt_id=attempt_id,
                )
            )
            await uow.commit()
            return changed


def MarkSucceeded(factory: IdempotencyUnitOfWorkFactory) -> CompleteExecutionAttempt:
    return CompleteExecutionAttempt(factory, IdempotencyState.SUCCEEDED, "idempotency.succeeded")


def MarkFailedPreEffect(factory: IdempotencyUnitOfWorkFactory) -> CompleteExecutionAttempt:
    return CompleteExecutionAttempt(
        factory, IdempotencyState.FAILED_PRE_EFFECT, "idempotency.failed_pre_effect"
    )


def MarkUnknownExternalOutcome(factory: IdempotencyUnitOfWorkFactory) -> CompleteExecutionAttempt:
    return CompleteExecutionAttempt(
        factory, IdempotencyState.UNKNOWN_EXTERNAL_OUTCOME, "idempotency.outcome_unknown"
    )


def _require_reconciler(context: ExecutionContext) -> None:
    if (
        context.actor.kind is not ActorKind.USER
        or context.actor.id != context.user_id
        or context.membership_status is not MembershipStatus.ACTIVE
        or context.membership_role not in {MembershipRole.OWNER, MembershipRole.ADMIN}
    ):
        raise InvalidIdempotencyTransition("reconciliation requires an active owner or admin")


@dataclass(slots=True)
class ReconcileUnknownOutcome:
    uow_factory: IdempotencyUnitOfWorkFactory
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    async def __call__(
        self,
        context: ExecutionContext,
        record_id: UUID,
        outcome: ReconciliationOutcome,
        *,
        result_ref: str | None = None,
    ) -> IdempotencyRecord:
        _require_reconciler(context)
        async with self.uow_factory(context.tenant_context()) as uow:
            current = await uow.records.get(record_id, for_update=True)
            if current is None:
                raise IdempotencyNotFound("idempotency record was not found")
            changed = reconcile(current, outcome, self.clock(), result_ref=result_ref)
            await uow.records.update(changed)
            await uow.audit.append(
                _audit(
                    context,
                    changed,
                    "idempotency.reconciled",
                    AuditOutcome.SUCCESS,
                    outcome.value.lower(),
                )
            )
            await uow.commit()
            return changed


@dataclass(slots=True)
class InspectIdempotencyState:
    uow_factory: IdempotencyUnitOfWorkFactory

    async def __call__(self, context: ExecutionContext, record_id: UUID) -> IdempotencyRecord:
        async with self.uow_factory(context.tenant_context()) as uow:
            record = await uow.records.get(record_id)
            if record is None:
                raise IdempotencyNotFound("idempotency record was not found")
            return record


def _audit(
    context: ExecutionContext,
    record: IdempotencyRecord,
    action: str,
    outcome: AuditOutcome,
    reason: str,
    *,
    attempt_id: UUID | None = None,
) -> AuditRecord:
    return tenant_audit(
        context,
        action=action,
        outcome=outcome,
        reason_code=reason,
        resource_type="idempotency_record",
        resource_id=str(record.id),
        tool_definition_id=record.tool_definition_id,
        tool_version_id=record.tool_version_id,
        idempotency_record_id=record.id,
        attempt_id=attempt_id,
        after_digest=record.request_digest,
        metadata=safe_metadata(
            {
                "state": record.state.value,
                "attempt_count": record.attempt_count,
                "result_ref": record.result_ref,
            }
        ),
    )
