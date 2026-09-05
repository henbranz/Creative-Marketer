from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Table,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from creative_marketer.infrastructure.database.schema import metadata

approval_requests = Table(
    "approval_requests",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("requested_by_actor_kind", String(32), nullable=False),
    Column("requested_by_actor_id", UUID(as_uuid=True), nullable=False),
    Column("requested_agent_definition_id", UUID(as_uuid=True), nullable=False),
    Column("resolved_agent_definition_id", UUID(as_uuid=True), nullable=False),
    Column("agent_version_id", UUID(as_uuid=True), nullable=False),
    Column("agent_configuration_digest", String(71), nullable=False),
    Column("tool_definition_id", UUID(as_uuid=True), nullable=False),
    Column("tool_version_id", UUID(as_uuid=True), nullable=False),
    Column("tool_configuration_digest", String(71), nullable=False),
    Column("tool_key", String(128), nullable=False),
    Column("risk_level", String(2), nullable=False),
    Column("permission_id", UUID(as_uuid=True), nullable=False),
    Column("permission_version_id", UUID(as_uuid=True), nullable=False),
    Column("permission_configuration_digest", String(71), nullable=False),
    Column("permission_engine_version", Integer, nullable=False),
    Column("scope_request_digest", String(71), nullable=False),
    Column("resource_type", String(128)),
    Column("resource_id", String(200)),
    Column("environment", String(32), nullable=False),
    Column("normalized_input_digest", String(71), nullable=False),
    Column("idempotency_key", String(64), nullable=False),
    Column("action_digest", String(71), nullable=False),
    Column("canonicalization_version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("risk_level IN ('R0','R1','R2','R3','R4','R5','R6')", name="risk"),
    CheckConstraint(
        "requested_by_actor_kind IN ('user','agent','workload','system')", name="requester"
    ),
    CheckConstraint("(resource_type IS NULL) = (resource_id IS NULL)", name="resource_pair"),
    CheckConstraint("expires_at > created_at", name="expiry"),
    CheckConstraint("idempotency_key ~ '^op_[0-9a-f]{32}$'", name="idempotency_key"),
    CheckConstraint("canonicalization_version = 1", name="canonicalization_version"),
    CheckConstraint(
        "agent_configuration_digest ~ '^sha256:[0-9a-f]{64}$' AND "
        "tool_configuration_digest ~ '^sha256:[0-9a-f]{64}$' AND "
        "permission_configuration_digest ~ '^sha256:[0-9a-f]{64}$' AND "
        "scope_request_digest ~ '^sha256:[0-9a-f]{64}$' AND "
        "normalized_input_digest ~ '^sha256:[0-9a-f]{64}$' AND "
        "action_digest ~ '^sha256:[0-9a-f]{64}$'",
        name="digests",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "requested_agent_definition_id"],
        ["agent_governance.agent_definitions.tenant_id", "agent_governance.agent_definitions.id"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["resolved_agent_definition_id", "agent_version_id"],
        ["agent_governance.agent_versions.definition_id", "agent_governance.agent_versions.id"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tool_definition_id"], ["tool_governance.tool_definitions.id"], ondelete="RESTRICT"
    ),
    ForeignKeyConstraint(
        ["tool_definition_id", "tool_version_id"],
        ["tool_governance.tool_versions.definition_id", "tool_governance.tool_versions.id"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "permission_id"],
        [
            "permission_governance.tool_permissions.tenant_id",
            "permission_governance.tool_permissions.id",
        ],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "permission_id", "permission_version_id"],
        [
            "permission_governance.tool_permission_versions.tenant_id",
            "permission_governance.tool_permission_versions.permission_id",
            "permission_governance.tool_permission_versions.id",
        ],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("tenant_id", "id"),
    schema="approval_governance",
)

approval_decisions = Table(
    "approval_decisions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("approval_request_id", UUID(as_uuid=True), nullable=False),
    Column("decision", String(16), nullable=False),
    Column("decided_by_user_id", UUID(as_uuid=True), nullable=False),
    Column("decided_by_actor_kind", String(32), nullable=False),
    Column("decided_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("reason_code", String(100)),
    Column("safe_note", String(500)),
    CheckConstraint("decision IN ('APPROVE','DENY')", name="decision"),
    CheckConstraint("decided_by_actor_kind = 'user'", name="human_actor"),
    CheckConstraint(
        "reason_code IS NULL OR reason_code ~ '^[a-z][a-z0-9_.-]{0,99}$'",
        name="reason_code",
    ),
    CheckConstraint(
        "safe_note IS NULL OR lower(safe_note) "
        "!~ '(bearer[[:space:]]+[^[:space:]]+|sk-[a-z0-9_-]{8,}|"
        "shpat_[a-z0-9]{8,}|gh[pousr]_[a-z0-9]{12,}|"
        "(api[_ -]?key|password|client[_ -]?secret)[[:space:]]*[:=])'",
        name="safe_note_no_credentials",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "approval_request_id"],
        [
            "approval_governance.approval_requests.tenant_id",
            "approval_governance.approval_requests.id",
        ],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("approval_request_id"),
    schema="approval_governance",
)

approval_revocations = Table(
    "approval_revocations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("approval_request_id", UUID(as_uuid=True), nullable=False),
    Column("revoked_by_user_id", UUID(as_uuid=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("reason_code", String(100), nullable=False),
    CheckConstraint("reason_code ~ '^[a-z][a-z0-9_.-]{0,99}$'", name="reason_code"),
    ForeignKeyConstraint(
        ["tenant_id", "approval_request_id"],
        [
            "approval_governance.approval_requests.tenant_id",
            "approval_governance.approval_requests.id",
        ],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("approval_request_id"),
    schema="approval_governance",
)
