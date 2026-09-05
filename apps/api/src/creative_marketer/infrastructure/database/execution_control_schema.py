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

idempotency_records = Table(
    "idempotency_records",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("tool_definition_id", UUID(as_uuid=True), nullable=False),
    Column("tool_version_id", UUID(as_uuid=True), nullable=False),
    Column("idempotency_key", String(64), nullable=False),
    Column("request_digest", String(71), nullable=False),
    Column("state", String(40), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("current_attempt_id", UUID(as_uuid=True)),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("result_ref", String(500)),
    Column("reconciliation_outcome", String(32)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("idempotency_key ~ '^op_[0-9a-f]{32}$'", name="key"),
    CheckConstraint("request_digest ~ '^sha256:[0-9a-f]{64}$'", name="digest"),
    CheckConstraint("attempt_count >= 0", name="attempt_count"),
    CheckConstraint(
        "result_ref IS NULL OR (length(result_ref) <= 489 AND result_ref ~ "
        "'^result://[A-Za-z0-9][A-Za-z0-9._~:/-]*$' AND lower(result_ref) "
        "!~ '(sk-[a-z0-9_-]{8,}|shpat_[a-z0-9]{8,}|gh[pousr]_[a-z0-9]{12,})')",
        name="safe_result_ref",
    ),
    CheckConstraint(
        "state IN ('RESERVED','EXECUTING','SUCCEEDED','FAILED_PRE_EFFECT',"
        "'UNKNOWN_EXTERNAL_OUTCOME','RECONCILED')",
        name="state",
    ),
    CheckConstraint(
        "reconciliation_outcome IS NULL OR reconciliation_outcome IN "
        "('EFFECT_CONFIRMED','NO_EFFECT_CONFIRMED')",
        name="reconciliation",
    ),
    CheckConstraint(
        "(state = 'EXECUTING') = (current_attempt_id IS NOT NULL AND lease_expires_at IS NOT NULL)",
        name="attempt_lease",
    ),
    CheckConstraint(
        "(state = 'RECONCILED') = (reconciliation_outcome IS NOT NULL)", name="reconciled_outcome"
    ),
    ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT"),
    ForeignKeyConstraint(
        ["tool_definition_id"], ["tool_governance.tool_definitions.id"], ondelete="RESTRICT"
    ),
    ForeignKeyConstraint(
        ["tool_definition_id", "tool_version_id"],
        ["tool_governance.tool_versions.definition_id", "tool_governance.tool_versions.id"],
        ondelete="RESTRICT",
    ),
    UniqueConstraint("tenant_id", "tool_definition_id", "idempotency_key"),
    UniqueConstraint("tenant_id", "id"),
    schema="execution_control",
)
