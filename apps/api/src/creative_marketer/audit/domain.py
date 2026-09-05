from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4


class AuditScopeKind(StrEnum):
    PLATFORM = "platform"
    TENANT = "tenant"


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    FAILED = "failed"
    ERROR = "error"


class AuditActorKind(StrEnum):
    USER = "user"
    WORKLOAD = "workload"
    SYSTEM = "system"
    AGENT = "agent"
    EXTERNAL_PRINCIPAL = "external_principal"
    ANONYMOUS = "anonymous"


@dataclass(frozen=True, slots=True)
class SafeAuditMetadata:
    canonical_json: str = "{}"


@dataclass(frozen=True, slots=True)
class AuditRecord:
    scope_kind: AuditScopeKind
    tenant_id: UUID | None
    actor_kind: AuditActorKind
    actor_id: str | None
    action: str
    outcome: AuditOutcome
    correlation_id: UUID
    environment: str
    safe_metadata: SafeAuditMetadata = field(default_factory=SafeAuditMetadata)
    id: UUID = field(default_factory=uuid4)
    resource_type: str | None = None
    resource_id: str | None = None
    reason_code: str | None = None
    causation_id: UUID | None = None
    policy_version: str | None = None
    tool_name: str | None = None
    tool_version: str | None = None
    tool_definition_id: UUID | None = None
    tool_version_id: UUID | None = None
    agent_definition_id: UUID | None = None
    agent_version_id: UUID | None = None
    agent_run_id: UUID | None = None
    permission_id: UUID | None = None
    permission_version_id: UUID | None = None
    approval_request_id: UUID | None = None
    tool_call_id: UUID | None = None
    idempotency_record_id: UUID | None = None
    attempt_id: UUID | None = None
    before_digest: str | None = None
    after_digest: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    audit_schema_version: int = 1

    def __post_init__(self) -> None:
        valid_scope = (self.scope_kind is AuditScopeKind.PLATFORM and self.tenant_id is None) or (
            self.scope_kind is AuditScopeKind.TENANT and self.tenant_id is not None
        )
        if not valid_scope:
            raise ValueError("audit scope and tenant_id are inconsistent")
