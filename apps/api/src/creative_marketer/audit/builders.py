from uuid import UUID

from creative_marketer.audit.domain import (
    AuditActorKind,
    AuditOutcome,
    AuditRecord,
    AuditScopeKind,
    SafeAuditMetadata,
)
from creative_marketer.identity.application.authentication import ExecutionContext


def tenant_audit(
    context: ExecutionContext,
    *,
    action: str,
    outcome: AuditOutcome,
    reason_code: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: SafeAuditMetadata | None = None,
) -> AuditRecord:
    return AuditRecord(
        scope_kind=AuditScopeKind.TENANT,
        tenant_id=context.tenant_id,
        actor_kind=AuditActorKind(context.actor.kind.value),
        actor_id=str(context.actor.id),
        action=action,
        outcome=outcome,
        reason_code=reason_code,
        resource_type=resource_type,
        resource_id=resource_id,
        correlation_id=context.correlation_id,
        environment=context.environment,
        safe_metadata=metadata or SafeAuditMetadata(),
    )


def platform_audit(
    *,
    actor_kind: AuditActorKind,
    actor_id: str | None,
    action: str,
    outcome: AuditOutcome,
    correlation_id: UUID,
    environment: str,
    reason_code: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: SafeAuditMetadata | None = None,
) -> AuditRecord:
    return AuditRecord(
        scope_kind=AuditScopeKind.PLATFORM,
        tenant_id=None,
        actor_kind=actor_kind,
        actor_id=actor_id,
        action=action,
        outcome=outcome,
        reason_code=reason_code,
        resource_type=resource_type,
        resource_id=resource_id,
        correlation_id=correlation_id,
        environment=environment,
        safe_metadata=metadata or SafeAuditMetadata(),
    )
