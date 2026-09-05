from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from jsonschema import Draft202012Validator

from creative_marketer.action_binding import (
    ActionBindingV1,
    NormalizedToolInput,
    OperationIdempotencyKey,
    canonical_json_v1,
)
from creative_marketer.agent_governance.domain import ResolvedAgentVersion
from creative_marketer.approval_governance.application import (
    ApprovalDecisionRepository,
    ApprovalRequestRepository,
    ApprovalRevocationRepository,
    append_approval_request,
)
from creative_marketer.approval_governance.domain import (
    ApprovalRequest,
    ApprovalValidator,
    approval_ttl,
)
from creative_marketer.audit.application import AuditWriter
from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditOutcome, AuditRecord
from creative_marketer.audit.safety import safe_metadata
from creative_marketer.events.application import OutboxWriter
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.execution_control.application import IdempotencyRepository
from creative_marketer.execution_control.domain import (
    IdempotencyRecord,
    IdempotencyState,
    ReconciliationOutcome,
    ReservationOutcome,
    begin_attempt,
    complete_attempt,
    reservation_outcome,
)
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.identity.application.context import TenantContext
from creative_marketer.permission_governance.application import EvaluateToolPermission
from creative_marketer.permission_governance.domain import (
    Decision,
    Obligation,
    PermissionDecision,
    TrustedScopeRequirements,
)
from creative_marketer.tool_execution.domain import (
    ExternalOutcome,
    GatewayResult,
    GatewayStatus,
    OutcomeUnknown,
    PreEffectFailure,
    ToolCall,
    ToolCallStatus,
    ToolExecutionContext,
    ToolExecutorResult,
    ToolInvocationRequest,
    TrustedAgentInvocation,
    immutable_output,
    transition_call,
)
from creative_marketer.tool_execution.events import tool_outcome_event
from creative_marketer.tool_governance.domain import (
    CredentialBoundary,
    ResolvedToolVersion,
)


@dataclass(frozen=True, slots=True)
class ResourceResolution:
    scopes: TrustedScopeRequirements
    resource_type: str | None = None
    resource_id: str | None = None


class ResourceRequirementResolver(Protocol):
    async def __call__(
        self,
        context: ExecutionContext,
        tool: ResolvedToolVersion,
        normalized_input: NormalizedToolInput,
    ) -> ResourceResolution: ...


class ResourceAccessDenied(Exception):
    """Trusted ownership resolution proved the referenced resource is not accessible."""


class ToolExecutor(Protocol):
    async def execute(
        self, context: ToolExecutionContext, normalized_input: NormalizedToolInput
    ) -> ToolExecutorResult: ...


class BudgetGuard(Protocol):
    async def authorize(self, context: ExecutionContext, tool: ResolvedToolVersion) -> bool: ...


InputNormalizer = Callable[[object], NormalizedToolInput]


class AgentResolver(Protocol):
    async def __call__(
        self, context: ExecutionContext, definition_id: UUID
    ) -> ResolvedAgentVersion: ...


class ToolResolver(Protocol):
    async def __call__(self, tool_key: str) -> ResolvedToolVersion: ...


@dataclass(frozen=True, slots=True)
class ToolExecutionBinding:
    tool_definition_id: UUID
    tool_version_id: UUID
    normalizer: InputNormalizer
    resource_resolver: ResourceRequirementResolver
    executor: ToolExecutor
    credential_capable: bool = False


class ToolExecutionBindingRegistry:
    def __init__(self, bindings: tuple[ToolExecutionBinding, ...]) -> None:
        self._bindings: dict[tuple[UUID, UUID], ToolExecutionBinding] = {}
        for binding in bindings:
            key = (binding.tool_definition_id, binding.tool_version_id)
            if key in self._bindings:
                raise ValueError("duplicate exact Tool execution binding")
            self._bindings[key] = binding

    def resolve(self, tool: ResolvedToolVersion) -> ToolExecutionBinding | None:
        return self._bindings.get((tool.definition_id, tool.version_id))


class ToolCallRepository(Protocol):
    async def reserve(self, call: ToolCall) -> tuple[ToolCall, bool]: ...
    async def get(self, call_id: UUID, *, for_update: bool = False) -> ToolCall | None: ...
    async def update(self, call: ToolCall) -> None: ...


class GatewayUnitOfWork(Protocol):
    tool_calls: ToolCallRepository
    idempotency: IdempotencyRepository
    requests: ApprovalRequestRepository
    decisions: ApprovalDecisionRepository
    revocations: ApprovalRevocationRepository
    audit: AuditWriter
    outbox: OutboxWriter

    async def __aenter__(self) -> "GatewayUnitOfWork": ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...

    async def verify_authorization_snapshot(self, call: ToolCall) -> bool: ...


class GatewayUnitOfWorkFactory(Protocol):
    def __call__(self, context: TenantContext) -> GatewayUnitOfWork: ...


def _operation(request: ToolInvocationRequest) -> OperationIdempotencyKey:
    return (
        OperationIdempotencyKey.generate()
        if request.operation_id is None
        else OperationIdempotencyKey(request.operation_id)
    )


def _result(status: GatewayStatus, operation: str, **values: object) -> GatewayResult:
    return GatewayResult(status=status, operation_id=operation, **values)  # type: ignore[arg-type]


def _validate_input(tool: ResolvedToolVersion, raw: object) -> None:
    if len(canonical_json_v1(raw).encode()) > 65_536:
        raise ValueError("Tool input exceeds the Phase-0 canonical payload limit")
    errors = sorted(
        Draft202012Validator(
            tool.input_schema.primitive(), format_checker=Draft202012Validator.FORMAT_CHECKER
        ).iter_errors(raw),
        key=lambda item: list(item.path),
    )
    if errors:
        raise ValueError(errors[0].message)


def _validate_output(tool: ResolvedToolVersion, output: object) -> bool:
    try:
        canonical_json_v1(output)
    except ValueError:
        return False
    return not any(Draft202012Validator(tool.output_schema.primitive()).iter_errors(output))


def _audit(
    context: ExecutionContext, call: ToolCall, action: str, outcome: AuditOutcome
) -> AuditRecord:
    return tenant_audit(
        context,
        action=action,
        outcome=outcome,
        reason_code=call.error_code,
        resource_type="tool_call",
        resource_id=str(call.id),
        agent_definition_id=call.binding.requested_agent_definition_id,
        agent_version_id=call.binding.agent_version_id,
        tool_definition_id=call.binding.tool_definition_id,
        tool_version_id=call.binding.tool_version_id,
        permission_id=call.binding.permission_id,
        permission_version_id=call.binding.permission_version_id,
        approval_request_id=call.approval_request_id,
        tool_call_id=call.id,
        idempotency_record_id=call.idempotency_record_id,
        attempt_id=call.attempt_id,
        after_digest=call.action_digest,
        metadata=safe_metadata(
            {"status": call.status.value, "external_outcome": call.external_outcome.value}
        ),
    )


@dataclass(slots=True)
class ToolGateway:
    agent_resolver: AgentResolver
    tool_resolver: ToolResolver
    permission_evaluator: EvaluateToolPermission
    bindings: ToolExecutionBindingRegistry
    uow_factory: GatewayUnitOfWorkFactory
    budget_guard: BudgetGuard | None = None
    contracts: EventContractRegistry = field(default_factory=EventContractRegistry)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    async def invoke(
        self, invocation: TrustedAgentInvocation, request: ToolInvocationRequest
    ) -> GatewayResult:
        context = invocation.initiating_context
        operation = _operation(request)
        try:
            agent = await self.agent_resolver(context, invocation.requested_agent_definition_id)
            tool = await self.tool_resolver(request.tool_key)
        except Exception:
            return _result(GatewayStatus.DENIED, operation.value, reason_code="TOOL_UNAVAILABLE")
        binding = self.bindings.resolve(tool)
        if binding is None:
            return _result(
                GatewayStatus.EXECUTOR_UNAVAILABLE,
                operation.value,
                reason_code="EXECUTOR_UNAVAILABLE",
            )
        try:
            _validate_input(tool, request.raw_input)
            normalized = binding.normalizer(request.raw_input)
            resources = await binding.resource_resolver(context, tool, normalized)
        except ResourceAccessDenied:
            return await self._record_pre_call_denial(
                context, agent, tool, operation.value, "RESOURCE_ACCESS_DENIED"
            )
        except Exception:
            return _result(
                GatewayStatus.INVALID_INPUT, operation.value, reason_code="INPUT_INVALID"
            )
        decision = await self.permission_evaluator(
            context,
            invocation.requested_agent_definition_id,
            request.tool_key,
            resources.scopes,
        )
        if (
            decision.tenant_id != context.tenant_id
            or decision.requested_agent_definition_id != agent.requested_tenant_definition_id
            or decision.resolved_agent_definition_id != agent.resolved_definition_id
            or decision.agent_version_id != agent.version_id
            or decision.tool_definition_id != tool.definition_id
            or decision.tool_version_id != tool.version_id
        ):
            return _result(
                GatewayStatus.DENIED, operation.value, reason_code="SECURITY_CONTEXT_INVALID"
            )
        if decision.decision is Decision.DENY:
            return _result(
                GatewayStatus.DENIED, operation.value, reason_code=decision.reason_code.value
            )
        blocked = await self._check_obligations(context, tool, binding, decision)
        if blocked is not None:
            return _result(blocked, operation.value, reason_code=blocked.value)
        action = ActionBindingV1.from_permission_decision(
            decision,
            normalized,
            operation,
            resource_type=resources.resource_type,
            resource_id=resources.resource_id,
        )
        return await self._prepare_and_execute(context, tool, binding, decision, action, normalized)

    async def _record_pre_call_denial(
        self,
        context: ExecutionContext,
        agent: ResolvedAgentVersion,
        tool: ResolvedToolVersion,
        operation_id: str,
        reason_code: str,
    ) -> GatewayResult:
        try:
            async with self.uow_factory(context.tenant_context()) as uow:
                await uow.audit.append(
                    tenant_audit(
                        context,
                        action="tool.invocation.denied",
                        outcome=AuditOutcome.DENIED,
                        reason_code=reason_code,
                        resource_type="tool_definition",
                        resource_id=str(tool.definition_id),
                        agent_definition_id=agent.requested_tenant_definition_id,
                        agent_version_id=agent.version_id,
                        tool_definition_id=tool.definition_id,
                        tool_version_id=tool.version_id,
                        metadata=safe_metadata({"operation_id": operation_id}),
                    )
                )
                await uow.commit()
        except Exception:
            pass
        return _result(GatewayStatus.DENIED, operation_id, reason_code=reason_code)

    async def _check_obligations(
        self,
        context: ExecutionContext,
        tool: ResolvedToolVersion,
        binding: ToolExecutionBinding,
        decision: PermissionDecision,
    ) -> GatewayStatus | None:
        supported = {
            Obligation.VALIDATE_TOOL_INPUT,
            Obligation.CHECK_IDEMPOTENCY,
            Obligation.REQUIRE_APPROVAL,
            Obligation.AUDIT_EXECUTION,
            Obligation.CHECK_BUDGET,
            Obligation.RESOLVE_CONNECTOR_CREDENTIAL,
        }
        if any(item not in supported for item in decision.obligations):
            return GatewayStatus.UNSUPPORTED_OBLIGATION
        if Obligation.CHECK_BUDGET in decision.obligations and (
            self.budget_guard is None or not await self.budget_guard.authorize(context, tool)
        ):
            return GatewayStatus.BUDGET_GUARD_UNAVAILABLE
        if (
            Obligation.RESOLVE_CONNECTOR_CREDENTIAL in decision.obligations
            or tool.credential_boundary is CredentialBoundary.CONNECTOR
        ) and not binding.credential_capable:
            return GatewayStatus.CREDENTIAL_EXECUTION_UNAVAILABLE
        return None

    async def _prepare_and_execute(
        self,
        context: ExecutionContext,
        tool: ResolvedToolVersion,
        binding: ToolExecutionBinding,
        decision: PermissionDecision,
        action: ActionBindingV1,
        normalized: NormalizedToolInput,
    ) -> GatewayResult:
        now = self.clock()
        initial = ToolCall(
            binding=action,
            correlation_id=context.correlation_id,
            status=(
                ToolCallStatus.AWAITING_APPROVAL
                if decision.decision is Decision.REQUIRES_APPROVAL
                else ToolCallStatus.READY
            ),
            created_at=now,
            updated_at=now,
        )
        async with self.uow_factory(context.tenant_context()) as uow:
            call, created = await uow.tool_calls.reserve(initial)
            if call.action_digest != action.action_digest:
                return _result(GatewayStatus.OPERATION_CONFLICT, action.idempotency_key)
            if decision.decision is Decision.REQUIRES_APPROVAL and created:
                approval = ApprovalRequest(
                    action,
                    context.actor.kind.value,
                    context.actor.id,
                    now,
                    now + approval_ttl(action.risk_level),
                )
                call = replace(call, approval_request_id=approval.id)
                await append_approval_request(uow, context, approval, self.contracts)
                await uow.tool_calls.update(call)
                await uow.audit.append(
                    _audit(context, call, "tool.invocation.awaiting_approval", AuditOutcome.SUCCESS)
                )
                await uow.commit()
                return _result(
                    GatewayStatus.AWAITING_APPROVAL,
                    call.operation_id,
                    tool_call_id=call.id,
                    approval_request_id=approval.id,
                )
            if decision.decision is Decision.REQUIRES_APPROVAL:
                approval_id = call.approval_request_id
                if approval_id is None:
                    return _result(
                        GatewayStatus.APPROVAL_INVALID,
                        call.operation_id,
                        tool_call_id=call.id,
                        reason_code="approval_binding_missing",
                    )
                existing_approval = await uow.requests.get(approval_id)
                approval_decision = await uow.decisions.get(approval_id)
                revocation = await uow.revocations.get(approval_id)
                validation = ApprovalValidator().validate(
                    decision, action, existing_approval, approval_decision, revocation, now
                )
                if not validation.valid:
                    status = (
                        GatewayStatus.AWAITING_APPROVAL
                        if validation.reason.value == "pending"
                        else GatewayStatus.APPROVAL_INVALID
                    )
                    return _result(
                        status,
                        call.operation_id,
                        tool_call_id=call.id,
                        approval_request_id=call.approval_request_id,
                        reason_code=validation.reason.value,
                    )
            if not await uow.verify_authorization_snapshot(call):
                return _result(
                    GatewayStatus.DENIED, call.operation_id, reason_code="STALE_AUTHORIZATION"
                )
            record, was_created = await uow.idempotency.reserve(
                IdempotencyRecord.from_binding(action)
            )
            reservation = (
                ReservationOutcome.NEW_RESERVATION
                if was_created
                else reservation_outcome(record, action.action_digest)
            )
            if (
                reservation is ReservationOutcome.REPLAY_SUCCEEDED
                and call.status is ToolCallStatus.UNKNOWN_EXTERNAL_OUTCOME
                and record.reconciliation_outcome is ReconciliationOutcome.EFFECT_CONFIRMED
            ):
                call = replace(
                    call,
                    status=ToolCallStatus.SUCCEEDED,
                    external_outcome=ExternalOutcome.RECONCILED,
                    result_ref=record.result_ref,
                    error_code=None,
                    completed_at=now,
                    updated_at=now,
                )
                await uow.tool_calls.update(call)
                await uow.audit.append(
                    _audit(context, call, "tool.execution.replayed", AuditOutcome.SUCCESS)
                )
                await uow.outbox.append(tool_outcome_event(context, call, self.contracts, now))
                await uow.commit()
                return self._reservation_result(call, record, reservation)  # type: ignore[return-value]
            terminal = self._reservation_result(call, record, reservation)
            if terminal is not None:
                if terminal.status is GatewayStatus.REPLAYED:
                    await uow.audit.append(
                        _audit(context, call, "tool.execution.replayed", AuditOutcome.SUCCESS)
                    )
                    await uow.commit()
                return terminal
            executing, acquired = begin_attempt(record, now)
            if acquired.value != "ACQUIRED":
                return _result(GatewayStatus.IN_PROGRESS, call.operation_id, tool_call_id=call.id)
            await uow.idempotency.update(executing)
            call = transition_call(
                call,
                ToolCallStatus.EXECUTING,
                now,
                idempotency_record_id=record.id,
                attempt_id=executing.current_attempt_id,
            )
            await uow.tool_calls.update(call)
            await uow.audit.append(
                _audit(context, call, "tool.execution.started", AuditOutcome.SUCCESS)
            )
            await uow.commit()
        execution_context = ToolExecutionContext(
            context.tenant_id,
            call.id,
            call.operation_id,
            call.attempt_id,  # type: ignore[arg-type]
            action.tool_definition_id,
            action.tool_version_id,
            context.correlation_id,
        )
        try:
            executor_result = await binding.executor.execute(execution_context, normalized)
        except PreEffectFailure:
            return await self._complete(
                context, call, ToolCallStatus.FAILED_PRE_EFFECT, "PRE_EFFECT_FAILURE"
            )
        except (OutcomeUnknown, Exception):
            return await self._complete(
                context, call, ToolCallStatus.UNKNOWN_EXTERNAL_OUTCOME, "OUTCOME_UNKNOWN"
            )
        valid_output = _validate_output(tool, executor_result.output)
        try:
            completed = await self._complete(
                context,
                call,
                ToolCallStatus.SUCCEEDED,
                "RESULT_CONTRACT_INVALID" if not valid_output else None,
                result_ref=executor_result.result_ref,
            )
        except Exception:
            try:
                return await self._complete(
                    context,
                    call,
                    ToolCallStatus.UNKNOWN_EXTERNAL_OUTCOME,
                    "POST_EFFECT_PERSISTENCE_FAILED",
                )
            except Exception:
                return _result(
                    GatewayStatus.CRITICAL_AMBIGUOUS,
                    call.operation_id,
                    tool_call_id=call.id,
                    reason_code="RECOVERY_PERSISTENCE_FAILED",
                )
        return replace(
            completed,
            status=(
                GatewayStatus.RESULT_CONTRACT_INVALID if not valid_output else completed.status
            ),
            output=None if not valid_output else immutable_output(executor_result.output),
        )

    @staticmethod
    def _reservation_result(
        call: ToolCall, record: IdempotencyRecord, outcome: ReservationOutcome
    ) -> GatewayResult | None:
        mapped = {
            ReservationOutcome.REPLAY_SUCCEEDED: GatewayStatus.REPLAYED,
            ReservationOutcome.IN_PROGRESS: GatewayStatus.IN_PROGRESS,
            ReservationOutcome.UNKNOWN_REQUIRES_RECONCILIATION: (
                GatewayStatus.BLOCKED_RECONCILIATION
            ),
            ReservationOutcome.CONFLICT: GatewayStatus.OPERATION_CONFLICT,
        }
        status = mapped.get(outcome)
        if status is None:
            return None
        return _result(
            status,
            call.operation_id,
            tool_call_id=call.id,
            idempotency_record_id=record.id,
            result_ref=record.result_ref,
        )

    async def _complete(
        self,
        context: ExecutionContext,
        call: ToolCall,
        target: ToolCallStatus,
        error_code: str | None,
        *,
        result_ref: str | None = None,
    ) -> GatewayResult:
        now = self.clock()
        async with self.uow_factory(context.tenant_context()) as uow:
            current_call = await uow.tool_calls.get(call.id, for_update=True)
            if call.idempotency_record_id is None or call.attempt_id is None:
                raise RuntimeError("durable execution ownership is missing")
            record = await uow.idempotency.get(call.idempotency_record_id, for_update=True)
            if current_call is None or record is None:
                raise RuntimeError("durable execution ownership is missing")
            idem_target = {
                ToolCallStatus.SUCCEEDED: IdempotencyState.SUCCEEDED,
                ToolCallStatus.FAILED_PRE_EFFECT: IdempotencyState.FAILED_PRE_EFFECT,
                ToolCallStatus.UNKNOWN_EXTERNAL_OUTCOME: IdempotencyState.UNKNOWN_EXTERNAL_OUTCOME,
            }[target]
            changed_record = complete_attempt(
                record, call.attempt_id, idem_target, now, result_ref=result_ref
            )
            changed_call = transition_call(
                current_call, target, now, result_ref=result_ref, error_code=error_code
            )
            await uow.idempotency.update(changed_record)
            await uow.tool_calls.update(changed_call)
            action = {
                ToolCallStatus.SUCCEEDED: "tool.execution.succeeded",
                ToolCallStatus.FAILED_PRE_EFFECT: "tool.execution.failed_pre_effect",
                ToolCallStatus.UNKNOWN_EXTERNAL_OUTCOME: "tool.execution.outcome_unknown",
            }[target]
            await uow.audit.append(_audit(context, changed_call, action, AuditOutcome.SUCCESS))
            await uow.outbox.append(tool_outcome_event(context, changed_call, self.contracts, now))
            await uow.commit()
            status = {
                ToolCallStatus.SUCCEEDED: GatewayStatus.EXECUTED,
                ToolCallStatus.FAILED_PRE_EFFECT: GatewayStatus.FAILED_PRE_EFFECT,
                ToolCallStatus.UNKNOWN_EXTERNAL_OUTCOME: GatewayStatus.UNKNOWN_OUTCOME,
            }[target]
            return _result(
                status,
                changed_call.operation_id,
                tool_call_id=changed_call.id,
                approval_request_id=changed_call.approval_request_id,
                idempotency_record_id=record.id,
                result_ref=result_ref,
                reason_code=error_code,
            )
