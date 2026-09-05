from datetime import UTC, datetime

from creative_marketer.approval_governance.domain import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRevocation,
    HumanDecision,
)
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import DomainEvent, tenant_event
from creative_marketer.identity.application.authentication import ExecutionContext


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def approval_requested_event(
    context: ExecutionContext, request: ApprovalRequest, registry: EventContractRegistry
) -> DomainEvent:
    event_type = "governance.approval.requested.v1"
    return tenant_event(
        context,
        event_type=event_type,
        schema_version=1,
        aggregate_type="approval_request",
        aggregate_id=request.id,
        occurred_at=request.created_at,
        payload_schema_digest=registry.schema_digest(event_type),
        payload={
            "approval_request_id": str(request.id),
            "tool_definition_id": str(request.binding.tool_definition_id),
            "tool_version_id": str(request.binding.tool_version_id),
            "risk_level": request.binding.risk_level.value,
            "action_digest": request.action_digest,
            "expires_at": _timestamp(request.expires_at),
        },
    )


def approval_decided_event(
    context: ExecutionContext,
    request: ApprovalRequest,
    decision: ApprovalDecision,
    registry: EventContractRegistry,
) -> DomainEvent:
    fact = "granted" if decision.decision is HumanDecision.APPROVE else "denied"
    event_type = f"governance.approval.{fact}.v1"
    return tenant_event(
        context,
        event_type=event_type,
        schema_version=1,
        aggregate_type="approval_request",
        aggregate_id=request.id,
        occurred_at=decision.decided_at,
        payload_schema_digest=registry.schema_digest(event_type),
        payload={
            "approval_request_id": str(request.id),
            "action_digest": request.action_digest,
            "decided_by_user_id": str(decision.decided_by_user_id),
            "reason_code": decision.reason_code,
        },
    )


def approval_revoked_event(
    context: ExecutionContext,
    request: ApprovalRequest,
    revocation: ApprovalRevocation,
    registry: EventContractRegistry,
) -> DomainEvent:
    event_type = "governance.approval.revoked.v1"
    return tenant_event(
        context,
        event_type=event_type,
        schema_version=1,
        aggregate_type="approval_request",
        aggregate_id=request.id,
        occurred_at=revocation.revoked_at,
        payload_schema_digest=registry.schema_digest(event_type),
        payload={
            "approval_request_id": str(request.id),
            "action_digest": request.action_digest,
            "revoked_by_user_id": str(revocation.revoked_by_user_id),
            "reason_code": revocation.reason_code,
        },
    )
