from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from creative_marketer.infrastructure.database.schema import metadata

tool_calls = Table(
    "tool_calls",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
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
    Column("operation_id", String(64), nullable=False),
    Column("action_digest", String(71), nullable=False),
    Column("canonicalization_version", Integer, nullable=False),
    Column("approval_request_id", UUID(as_uuid=True)),
    Column("idempotency_record_id", UUID(as_uuid=True)),
    Column("attempt_id", UUID(as_uuid=True)),
    Column("status", String(40), nullable=False),
    Column("external_outcome", String(20), nullable=False),
    Column("result_ref", String(500)),
    Column("error_code", String(100)),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "status IN ('AWAITING_APPROVAL','READY','EXECUTING','SUCCEEDED',"
        "'FAILED_PRE_EFFECT','UNKNOWN_EXTERNAL_OUTCOME')",
        name="status",
    ),
    CheckConstraint(
        "external_outcome IN ('NOT_STARTED','CONFIRMED','UNKNOWN','RECONCILED')",
        name="external_outcome",
    ),
    CheckConstraint("operation_id ~ '^op_[0-9a-f]{32}$'", name="operation"),
    CheckConstraint(
        "action_digest ~ '^sha256:[0-9a-f]{64}$' AND "
        "normalized_input_digest ~ '^sha256:[0-9a-f]{64}$' AND "
        "scope_request_digest ~ '^sha256:[0-9a-f]{64}$'",
        name="digests",
    ),
    CheckConstraint("(resource_type IS NULL) = (resource_id IS NULL)", name="resource_pair"),
    ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT"),
    ForeignKeyConstraint(
        ["tenant_id", "requested_agent_definition_id"],
        ["agent_governance.agent_definitions.tenant_id", "agent_governance.agent_definitions.id"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tool_definition_id", "tool_version_id"],
        ["tool_governance.tool_versions.definition_id", "tool_governance.tool_versions.id"],
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
    ForeignKeyConstraint(
        ["tenant_id", "approval_request_id"],
        [
            "approval_governance.approval_requests.tenant_id",
            "approval_governance.approval_requests.id",
        ],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "idempotency_record_id"],
        [
            "execution_control.idempotency_records.tenant_id",
            "execution_control.idempotency_records.id",
        ],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("tenant_id", "tool_definition_id", "operation_id"),
    UniqueConstraint("tenant_id", "id"),
    schema="tool_execution",
)
Index("ix_tool_calls_tenant_created", tool_calls.c.tenant_id, tool_calls.c.created_at)
Index("ix_tool_calls_status_updated", tool_calls.c.status, tool_calls.c.updated_at)
Index("ix_tool_calls_correlation", tool_calls.c.correlation_id)
Index("ix_tool_calls_approval", tool_calls.c.approval_request_id)
Index("ix_tool_calls_idempotency", tool_calls.c.idempotency_record_id)
