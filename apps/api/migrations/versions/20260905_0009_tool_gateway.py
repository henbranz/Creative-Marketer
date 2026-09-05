"""Create governed ToolCall execution state and audit linkage.

Revision ID: 20260905_0009
Revises: 20260905_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0009"
down_revision: str | None = "20260905_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME = "creative_marketer_runtime"
MIGRATOR = "creative_marketer_migrator"
TENANT = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def _create_tool_calls() -> None:
    op.execute("CREATE SCHEMA tool_execution")
    uuid = postgresql.UUID(as_uuid=True)
    columns = [
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, nullable=False),
        sa.Column("requested_agent_definition_id", uuid, nullable=False),
        sa.Column("resolved_agent_definition_id", uuid, nullable=False),
        sa.Column("agent_version_id", uuid, nullable=False),
        sa.Column("agent_configuration_digest", sa.String(71), nullable=False),
        sa.Column("tool_definition_id", uuid, nullable=False),
        sa.Column("tool_version_id", uuid, nullable=False),
        sa.Column("tool_configuration_digest", sa.String(71), nullable=False),
        sa.Column("tool_key", sa.String(128), nullable=False),
        sa.Column("risk_level", sa.String(2), nullable=False),
        sa.Column("permission_id", uuid, nullable=False),
        sa.Column("permission_version_id", uuid, nullable=False),
        sa.Column("permission_configuration_digest", sa.String(71), nullable=False),
        sa.Column("permission_engine_version", sa.Integer, nullable=False),
        sa.Column("scope_request_digest", sa.String(71), nullable=False),
        sa.Column("resource_type", sa.String(128)),
        sa.Column("resource_id", sa.String(200)),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("normalized_input_digest", sa.String(71), nullable=False),
        sa.Column("operation_id", sa.String(64), nullable=False),
        sa.Column("action_digest", sa.String(71), nullable=False),
        sa.Column("canonicalization_version", sa.Integer, nullable=False),
        sa.Column("approval_request_id", uuid),
        sa.Column("idempotency_record_id", uuid),
        sa.Column("attempt_id", uuid),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("external_outcome", sa.String(20), nullable=False),
        sa.Column("result_ref", sa.String(500)),
        sa.Column("error_code", sa.String(100)),
        sa.Column("correlation_id", uuid, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('AWAITING_APPROVAL','READY','EXECUTING','SUCCEEDED',"
            "'FAILED_PRE_EFFECT','UNKNOWN_EXTERNAL_OUTCOME')",
            name="ck_tool_calls_status",
        ),
        sa.CheckConstraint(
            "external_outcome IN ('NOT_STARTED','CONFIRMED','UNKNOWN','RECONCILED')",
            name="ck_tool_calls_external_outcome",
        ),
        sa.CheckConstraint("operation_id ~ '^op_[0-9a-f]{32}$'", name="ck_tool_calls_operation"),
        sa.CheckConstraint(
            "action_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "normalized_input_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "scope_request_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_tool_calls_digests",
        ),
        sa.CheckConstraint(
            "(resource_type IS NULL) = (resource_id IS NULL)", name="ck_tool_calls_resource_pair"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_agent_definition_id"],
            [
                "agent_governance.agent_definitions.tenant_id",
                "agent_governance.agent_definitions.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tool_definition_id", "tool_version_id"],
            ["tool_governance.tool_versions.definition_id", "tool_governance.tool_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "permission_id", "permission_version_id"],
            [
                "permission_governance.tool_permission_versions.tenant_id",
                "permission_governance.tool_permission_versions.permission_id",
                "permission_governance.tool_permission_versions.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approval_request_id"],
            [
                "approval_governance.approval_requests.tenant_id",
                "approval_governance.approval_requests.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "idempotency_record_id"],
            [
                "execution_control.idempotency_records.tenant_id",
                "execution_control.idempotency_records.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "tool_definition_id", "operation_id", name="uq_tool_calls_operation"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tool_calls_tenant_id"),
    ]
    op.create_table("tool_calls", *columns, schema="tool_execution")
    for name, fields in (
        ("ix_tool_calls_tenant_created", ["tenant_id", "created_at"]),
        ("ix_tool_calls_status_updated", ["status", "updated_at"]),
        ("ix_tool_calls_correlation", ["correlation_id"]),
        ("ix_tool_calls_approval", ["approval_request_id"]),
        ("ix_tool_calls_idempotency", ["idempotency_record_id"]),
    ):
        op.create_index(name, "tool_calls", fields, schema="tool_execution")


def _protect_lifecycle() -> None:
    op.execute("""
    CREATE FUNCTION tool_execution.protect_tool_call() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF (NEW.id, NEW.tenant_id, NEW.requested_agent_definition_id,
          NEW.resolved_agent_definition_id, NEW.agent_version_id,
          NEW.agent_configuration_digest, NEW.tool_definition_id, NEW.tool_version_id,
          NEW.tool_configuration_digest, NEW.tool_key, NEW.risk_level, NEW.permission_id,
          NEW.permission_version_id, NEW.permission_configuration_digest,
          NEW.permission_engine_version, NEW.scope_request_digest, NEW.resource_type,
          NEW.resource_id, NEW.environment, NEW.normalized_input_digest, NEW.operation_id,
          NEW.action_digest, NEW.canonicalization_version, NEW.correlation_id, NEW.created_at)
         IS DISTINCT FROM
         (OLD.id, OLD.tenant_id, OLD.requested_agent_definition_id,
          OLD.resolved_agent_definition_id, OLD.agent_version_id,
          OLD.agent_configuration_digest, OLD.tool_definition_id, OLD.tool_version_id,
          OLD.tool_configuration_digest, OLD.tool_key, OLD.risk_level, OLD.permission_id,
          OLD.permission_version_id, OLD.permission_configuration_digest,
          OLD.permission_engine_version, OLD.scope_request_digest, OLD.resource_type,
          OLD.resource_id, OLD.environment, OLD.normalized_input_digest, OLD.operation_id,
          OLD.action_digest, OLD.canonicalization_version, OLD.correlation_id, OLD.created_at)
      THEN RAISE EXCEPTION 'ToolCall binding is immutable'; END IF;
      IF NOT (
        (OLD.status = 'AWAITING_APPROVAL' AND NEW.status = 'AWAITING_APPROVAL'
          AND OLD.approval_request_id IS NULL AND NEW.approval_request_id IS NOT NULL) OR
        (OLD.status IN ('READY','AWAITING_APPROVAL','FAILED_PRE_EFFECT')
          AND NEW.status = 'EXECUTING' AND NEW.idempotency_record_id IS NOT NULL
          AND NEW.attempt_id IS NOT NULL) OR
        (OLD.status = 'UNKNOWN_EXTERNAL_OUTCOME' AND NEW.status = 'EXECUTING'
          AND OLD.external_outcome = 'UNKNOWN' AND NEW.external_outcome = 'NOT_STARTED'
          AND NEW.idempotency_record_id IS NOT NULL AND NEW.attempt_id IS NOT NULL) OR
        (OLD.status = 'UNKNOWN_EXTERNAL_OUTCOME' AND NEW.status = 'SUCCEEDED'
          AND NEW.external_outcome = 'RECONCILED' AND NEW.attempt_id IS NULL) OR
        (OLD.status = 'EXECUTING'
          AND NEW.status IN ('SUCCEEDED','FAILED_PRE_EFFECT','UNKNOWN_EXTERNAL_OUTCOME')
          AND NEW.attempt_id IS NULL)
      ) THEN RAISE EXCEPTION 'invalid ToolCall lifecycle transition'; END IF;
      RETURN NEW;
    END; $$
    """)
    op.execute(
        "CREATE TRIGGER protect_tool_call BEFORE UPDATE ON tool_execution.tool_calls "
        "FOR EACH ROW EXECUTE FUNCTION tool_execution.protect_tool_call()"
    )


def _security() -> None:
    op.execute(f"REVOKE ALL ON SCHEMA tool_execution FROM {RUNTIME}")
    op.execute(f"GRANT USAGE ON SCHEMA tool_execution TO {RUNTIME}")
    op.execute("ALTER TABLE tool_execution.tool_calls ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tool_execution.tool_calls FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tool_calls_migration_control ON tool_execution.tool_calls "
        f"FOR ALL TO {MIGRATOR} USING (true) WITH CHECK (true)"
    )
    op.execute(
        "CREATE POLICY tool_calls_runtime_select ON tool_execution.tool_calls "
        f"FOR SELECT TO {RUNTIME} USING (tenant_id = {TENANT})"
    )
    op.execute(
        "CREATE POLICY tool_calls_runtime_insert ON tool_execution.tool_calls "
        f"FOR INSERT TO {RUNTIME} WITH CHECK (tenant_id = {TENANT})"
    )
    op.execute(
        "CREATE POLICY tool_calls_runtime_update ON tool_execution.tool_calls "
        f"FOR UPDATE TO {RUNTIME} USING (tenant_id = {TENANT}) "
        f"WITH CHECK (tenant_id = {TENANT})"
    )
    op.execute(f"GRANT SELECT, INSERT ON tool_execution.tool_calls TO {RUNTIME}")
    op.execute(
        "GRANT UPDATE (approval_request_id, idempotency_record_id, attempt_id, status, "
        "external_outcome, result_ref, error_code, started_at, completed_at, updated_at) "
        "ON tool_execution.tool_calls TO creative_marketer_runtime"
    )


def upgrade() -> None:
    _create_tool_calls()
    _protect_lifecycle()
    _security()
    op.add_column(
        "audit_records", sa.Column("tool_call_id", postgresql.UUID(as_uuid=True)), schema="audit"
    )
    op.create_index(
        "ix_audit_records_tool_call",
        "audit_records",
        ["tool_call_id", "occurred_at"],
        schema="audit",
    )


def downgrade() -> None:
    count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT (SELECT count(*) FROM tool_execution.tool_calls) + "
                "(SELECT count(*) FROM audit.audit_records WHERE tool_call_id IS NOT NULL)"
            )
        )
        .scalar_one()
    )
    if count:
        raise RuntimeError("refusing lossy Tool Gateway downgrade while history exists")
    op.drop_index("ix_audit_records_tool_call", table_name="audit_records", schema="audit")
    op.drop_column("audit_records", "tool_call_id", schema="audit")
    op.drop_table("tool_calls", schema="tool_execution")
    op.execute("DROP FUNCTION tool_execution.protect_tool_call()")
    op.execute("DROP SCHEMA tool_execution")
