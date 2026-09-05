from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from creative_marketer.action_binding import (
    ActionBindingV1,
    NormalizedToolInput,
    OperationIdempotencyKey,
)
from creative_marketer.approval_governance.domain import (
    ApprovalConflict,
    ApprovalDecision,
    ApprovalExpired,
    ApprovalForbidden,
    ApprovalNotFound,
    ApprovalRequest,
    ApprovalRevocation,
    ApprovalState,
    HumanDecision,
    approval_ttl,
    effective_approval_state,
    utc_now,
)
from creative_marketer.approval_governance.events import (
    approval_decided_event,
    approval_requested_event,
    approval_revoked_event,
)
from creative_marketer.audit.application import AuditWriter
from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditOutcome, AuditRecord
from creative_marketer.audit.safety import safe_metadata
from creative_marketer.events.application import OutboxWriter
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.identity.application.authentication import ActorKind, ExecutionContext
from creative_marketer.identity.application.context import TenantContext
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.permission_governance.domain import Decision, PermissionDecision
from creative_marketer.tool_governance.domain import RiskLevel


class ApprovalRequestRepository(Protocol):
    async def add(self, request: ApprovalRequest) -> None: ...
    async def get(
        self, approval_id: UUID, *, for_update: bool = False
    ) -> ApprovalRequest | None: ...


class ApprovalDecisionRepository(Protocol):
    async def add(self, decision: ApprovalDecision) -> None: ...
    async def get(self, approval_id: UUID) -> ApprovalDecision | None: ...


class ApprovalRevocationRepository(Protocol):
    async def add(self, revocation: ApprovalRevocation) -> None: ...
    async def get(self, approval_id: UUID) -> ApprovalRevocation | None: ...


class ApprovalUnitOfWork(Protocol):
    requests: ApprovalRequestRepository
    decisions: ApprovalDecisionRepository
    revocations: ApprovalRevocationRepository
    audit: AuditWriter
    outbox: OutboxWriter

    async def __aenter__(self) -> "ApprovalUnitOfWork": ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class ApprovalUnitOfWorkFactory(Protocol):
    def __call__(self, context: TenantContext) -> ApprovalUnitOfWork: ...


async def append_approval_request(
    uow: ApprovalUnitOfWork,
    context: ExecutionContext,
    request: ApprovalRequest,
    contracts: EventContractRegistry,
) -> None:
    """Append Approval state, Audit, and event to an existing application transaction."""
    await uow.requests.add(request)
    await uow.audit.append(_request_audit(context, request))
    await uow.outbox.append(approval_requested_event(context, request, contracts))


def _human_authorized(context: ExecutionContext, risk: RiskLevel) -> None:
    allowed_roles = (
        {MembershipRole.OWNER}
        if risk is RiskLevel.R6
        else {
            MembershipRole.OWNER,
            MembershipRole.ADMIN,
        }
    )
    if (
        context.actor.kind is not ActorKind.USER
        or context.actor.id != context.user_id
        or context.membership_status is not MembershipStatus.ACTIVE
        or context.membership_role not in allowed_roles
    ):
        raise ApprovalForbidden("current trusted actor may not decide this approval")


@dataclass(slots=True)
class CreateApprovalRequest:
    uow_factory: ApprovalUnitOfWorkFactory
    clock: Callable[[], datetime] = utc_now
    contracts: EventContractRegistry = field(default_factory=EventContractRegistry)

    async def __call__(
        self,
        context: ExecutionContext,
        permission_decision: PermissionDecision,
        normalized_input: NormalizedToolInput,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> ApprovalRequest:
        if (
            permission_decision.decision is not Decision.REQUIRES_APPROVAL
            or permission_decision.tenant_id != context.tenant_id
            or permission_decision.actor_kind != context.actor.kind.value
            or permission_decision.actor_id != context.actor.id
            or permission_decision.environment != context.environment
        ):
            raise ApprovalForbidden(
                "approval request requires the current trusted requires-approval decision"
            )
        binding = ActionBindingV1.from_permission_decision(
            permission_decision,
            normalized_input,
            OperationIdempotencyKey.generate(),
            resource_type=resource_type,
            resource_id=resource_id,
        )
        now = self.clock()
        request = ApprovalRequest(
            binding=binding,
            requested_by_actor_kind=context.actor.kind.value,
            requested_by_actor_id=context.actor.id,
            created_at=now,
            expires_at=now + approval_ttl(binding.risk_level),
        )
        async with self.uow_factory(context.tenant_context()) as uow:
            await append_approval_request(uow, context, request, self.contracts)
            await uow.commit()
        return request


@dataclass(slots=True)
class DecideApproval:
    uow_factory: ApprovalUnitOfWorkFactory
    clock: Callable[[], datetime] = utc_now
    contracts: EventContractRegistry = field(default_factory=EventContractRegistry)

    async def __call__(
        self,
        context: ExecutionContext,
        approval_id: UUID,
        decision_value: HumanDecision,
        *,
        reason_code: str | None = None,
        safe_note: str | None = None,
    ) -> ApprovalDecision:
        decided_at = self.clock()
        async with self.uow_factory(context.tenant_context()) as uow:
            request = await uow.requests.get(approval_id, for_update=True)
            if request is None:
                raise ApprovalNotFound("approval request was not found")
            _human_authorized(context, request.binding.risk_level)
            existing = await uow.decisions.get(approval_id)
            revocation = await uow.revocations.get(approval_id)
            if existing is not None or revocation is not None:
                raise ApprovalConflict("approval request is already terminal")
            if decided_at >= request.expires_at:
                raise ApprovalExpired("approval request has expired")
            decision = ApprovalDecision(
                approval_request_id=request.id,
                tenant_id=request.tenant_id,
                decision=decision_value,
                decided_by_user_id=context.user_id,
                decided_by_actor_kind=context.actor.kind.value,
                decided_at=decided_at,
                reason_code=reason_code,
                safe_note=safe_note,
            )
            await uow.decisions.add(decision)
            await uow.audit.append(_decision_audit(context, request, decision))
            await uow.outbox.append(
                approval_decided_event(context, request, decision, self.contracts)
            )
            await uow.commit()
            return decision


@dataclass(slots=True)
class RevokeApproval:
    uow_factory: ApprovalUnitOfWorkFactory
    clock: Callable[[], datetime] = utc_now
    contracts: EventContractRegistry = field(default_factory=EventContractRegistry)

    async def __call__(
        self,
        context: ExecutionContext,
        approval_id: UUID,
        reason_code: str,
    ) -> ApprovalRevocation:
        revoked_at = self.clock()
        async with self.uow_factory(context.tenant_context()) as uow:
            request = await uow.requests.get(approval_id, for_update=True)
            if request is None:
                raise ApprovalNotFound("approval request was not found")
            _human_authorized(context, request.binding.risk_level)
            if await uow.revocations.get(approval_id) is not None:
                raise ApprovalConflict("approval request is already revoked")
            revocation = ApprovalRevocation(
                request.id, request.tenant_id, context.user_id, revoked_at, reason_code
            )
            await uow.revocations.add(revocation)
            await uow.audit.append(_revocation_audit(context, request, revocation))
            await uow.outbox.append(
                approval_revoked_event(context, request, revocation, self.contracts)
            )
            await uow.commit()
            return revocation


@dataclass(frozen=True, slots=True)
class ApprovalView:
    request: ApprovalRequest
    state: ApprovalState
    decision: ApprovalDecision | None
    revocation: ApprovalRevocation | None


@dataclass(slots=True)
class InspectApproval:
    uow_factory: ApprovalUnitOfWorkFactory
    clock: Callable[[], datetime] = utc_now

    async def __call__(self, context: ExecutionContext, approval_id: UUID) -> ApprovalView:
        async with self.uow_factory(context.tenant_context()) as uow:
            request = await uow.requests.get(approval_id)
            if request is None:
                raise ApprovalNotFound("approval request was not found")
            decision = await uow.decisions.get(approval_id)
            revocation = await uow.revocations.get(approval_id)
            return ApprovalView(
                request,
                effective_approval_state(request, decision, revocation, self.clock()),
                decision,
                revocation,
            )


def _request_audit(context: ExecutionContext, request: ApprovalRequest) -> AuditRecord:
    return tenant_audit(
        context,
        action="approval.request.created",
        outcome=AuditOutcome.SUCCESS,
        resource_type="approval_request",
        resource_id=str(request.id),
        agent_definition_id=request.binding.requested_agent_definition_id,
        agent_version_id=request.binding.agent_version_id,
        tool_definition_id=request.binding.tool_definition_id,
        tool_version_id=request.binding.tool_version_id,
        permission_id=request.binding.permission_id,
        permission_version_id=request.binding.permission_version_id,
        approval_request_id=request.id,
        after_digest=request.action_digest,
        metadata=safe_metadata({"risk": request.binding.risk_level.value}),
    )


def _decision_audit(
    context: ExecutionContext, request: ApprovalRequest, decision: ApprovalDecision
) -> AuditRecord:
    action_suffix = "approved" if decision.decision is HumanDecision.APPROVE else "denied"
    return tenant_audit(
        context,
        action=f"approval.decision.{action_suffix}",
        outcome=AuditOutcome.SUCCESS,
        reason_code=decision.reason_code,
        resource_type="approval_request",
        resource_id=str(request.id),
        approval_request_id=request.id,
        after_digest=request.action_digest,
        metadata=safe_metadata({"risk": request.binding.risk_level.value}),
    )


def _revocation_audit(
    context: ExecutionContext, request: ApprovalRequest, revocation: ApprovalRevocation
) -> AuditRecord:
    return tenant_audit(
        context,
        action="approval.revoked",
        outcome=AuditOutcome.SUCCESS,
        reason_code=revocation.reason_code,
        resource_type="approval_request",
        resource_id=str(request.id),
        approval_request_id=request.id,
        after_digest=request.action_digest,
        metadata=safe_metadata({"risk": request.binding.risk_level.value}),
    )
