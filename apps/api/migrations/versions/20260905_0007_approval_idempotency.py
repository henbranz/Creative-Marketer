"""Create immutable approvals and durable idempotency control.

Revision ID: 20260905_0007
Revises: 20260905_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0007"
down_revision: str | None = "20260905_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME = "creative_marketer_runtime"
MIGRATOR = "creative_marketer_migrator"
TENANT = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def _audit_columns() -> None:
    for name in ("approval_request_id", "idempotency_record_id", "attempt_id"):
        op.add_column(
            "audit_records", sa.Column(name, postgresql.UUID(as_uuid=True)), schema="audit"
        )
    op.create_index(
        "ix_audit_records_approval",
        "audit_records",
        ["approval_request_id", "occurred_at"],
        schema="audit",
    )
    op.create_index(
        "ix_audit_records_idempotency",
        "audit_records",
        ["idempotency_record_id", "occurred_at"],
        schema="audit",
    )


def _approval_tables() -> None:
    op.execute("CREATE SCHEMA approval_governance")
    op.create_table(
        "approval_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_actor_kind", sa.String(32), nullable=False),
        sa.Column("requested_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_agent_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resolved_agent_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_configuration_digest", sa.String(71), nullable=False),
        sa.Column("tool_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_configuration_digest", sa.String(71), nullable=False),
        sa.Column("tool_key", sa.String(128), nullable=False),
        sa.Column("risk_level", sa.String(2), nullable=False),
        sa.Column("permission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_configuration_digest", sa.String(71), nullable=False),
        sa.Column("permission_engine_version", sa.Integer(), nullable=False),
        sa.Column("scope_request_digest", sa.String(71), nullable=False),
        sa.Column("resource_type", sa.String(128)),
        sa.Column("resource_id", sa.String(200)),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("normalized_input_digest", sa.String(71), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("action_digest", sa.String(71), nullable=False),
        sa.Column("canonicalization_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "risk_level IN ('R0','R1','R2','R3','R4','R5','R6')", name="ck_approval_requests_risk"
        ),
        sa.CheckConstraint(
            "requested_by_actor_kind IN ('user','agent','workload','system')",
            name="ck_approval_requests_requester",
        ),
        sa.CheckConstraint(
            "(resource_type IS NULL) = (resource_id IS NULL)",
            name="ck_approval_requests_resource_pair",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_approval_requests_expiry"),
        sa.CheckConstraint(
            "idempotency_key ~ '^op_[0-9a-f]{32}$'", name="ck_approval_requests_idempotency_key"
        ),
        sa.CheckConstraint(
            "canonicalization_version = 1",
            name="ck_approval_requests_canonicalization_version",
        ),
        sa.CheckConstraint(
            "agent_configuration_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "tool_configuration_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "permission_configuration_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "scope_request_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "normalized_input_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "action_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_approval_requests_digests",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_agent_definition_id"],
            [
                "agent_governance.agent_definitions.tenant_id",
                "agent_governance.agent_definitions.id",
            ],
            name="fk_approval_requests_tenant_agent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_agent_definition_id", "agent_version_id"],
            [
                "agent_governance.agent_versions.definition_id",
                "agent_governance.agent_versions.id",
            ],
            name="fk_approval_requests_resolved_agent_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tool_definition_id"],
            ["tool_governance.tool_definitions.id"],
            name="fk_approval_requests_tool",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tool_definition_id", "tool_version_id"],
            ["tool_governance.tool_versions.definition_id", "tool_governance.tool_versions.id"],
            name="fk_approval_requests_tool_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "permission_id"],
            [
                "permission_governance.tool_permissions.tenant_id",
                "permission_governance.tool_permissions.id",
            ],
            name="fk_approval_requests_permission",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "permission_id", "permission_version_id"],
            [
                "permission_governance.tool_permission_versions.tenant_id",
                "permission_governance.tool_permission_versions.permission_id",
                "permission_governance.tool_permission_versions.id",
            ],
            name="fk_approval_requests_permission_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approval_requests"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_approval_requests_tenant_id_id"),
        schema="approval_governance",
    )
    op.create_table(
        "approval_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decided_by_actor_kind", sa.String(32), nullable=False),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("reason_code", sa.String(100)),
        sa.Column("safe_note", sa.String(500)),
        sa.CheckConstraint("decision IN ('APPROVE','DENY')", name="ck_approval_decisions_decision"),
        sa.CheckConstraint(
            "decided_by_actor_kind = 'user'", name="ck_approval_decisions_human_actor"
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR reason_code ~ '^[a-z][a-z0-9_.-]{0,99}$'",
            name="ck_approval_decisions_reason_code",
        ),
        sa.CheckConstraint(
            "safe_note IS NULL OR lower(safe_note) "
            "!~ '(bearer[[:space:]]+[^[:space:]]+|sk-[a-z0-9_-]{8,}|"
            "shpat_[a-z0-9]{8,}|gh[pousr]_[a-z0-9]{12,}|"
            "(api[_ -]?key|password|client[_ -]?secret)[[:space:]]*[:=])'",
            name="ck_approval_decisions_safe_note_no_credentials",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approval_request_id"],
            [
                "approval_governance.approval_requests.tenant_id",
                "approval_governance.approval_requests.id",
            ],
            name="fk_approval_decisions_tenant_request",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approval_decisions"),
        sa.UniqueConstraint("approval_request_id", name="uq_approval_decisions_request"),
        schema="approval_governance",
    )
    op.create_table(
        "approval_revocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revoked_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "revoked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("reason_code", sa.String(100), nullable=False),
        sa.CheckConstraint(
            "reason_code ~ '^[a-z][a-z0-9_.-]{0,99}$'",
            name="ck_approval_revocations_reason_code",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approval_request_id"],
            [
                "approval_governance.approval_requests.tenant_id",
                "approval_governance.approval_requests.id",
            ],
            name="fk_approval_revocations_tenant_request",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approval_revocations"),
        sa.UniqueConstraint("approval_request_id", name="uq_approval_revocations_request"),
        schema="approval_governance",
    )
    op.execute(
        """
        CREATE FUNCTION approval_governance.reject_history_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'approval history is immutable'; END; $$
        """
    )
    for table in ("approval_requests", "approval_decisions", "approval_revocations"):
        op.execute(
            f"CREATE TRIGGER reject_history_mutation BEFORE UPDATE OR DELETE ON "
            f"approval_governance.{table} FOR EACH ROW EXECUTE FUNCTION "
            "approval_governance.reject_history_mutation()"
        )


def _idempotency_table() -> None:
    op.execute("CREATE SCHEMA execution_control")
    op.create_table(
        "idempotency_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("current_attempt_id", postgresql.UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("result_ref", sa.String(500)),
        sa.Column("reconciliation_outcome", sa.String(32)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "idempotency_key ~ '^op_[0-9a-f]{32}$'", name="ck_idempotency_records_key"
        ),
        sa.CheckConstraint(
            "request_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_idempotency_records_digest"
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_idempotency_records_attempt_count"),
        sa.CheckConstraint(
            "result_ref IS NULL OR (result_ref ~ "
            "'^result://[A-Za-z0-9][A-Za-z0-9._~:/-]{0,479}$' AND lower(result_ref) "
            "!~ '(sk-[a-z0-9_-]{8,}|shpat_[a-z0-9]{8,}|gh[pousr]_[a-z0-9]{12,})')",
            name="ck_idempotency_records_safe_result_ref",
        ),
        sa.CheckConstraint(
            "state IN ('RESERVED','EXECUTING','SUCCEEDED','FAILED_PRE_EFFECT',"
            "'UNKNOWN_EXTERNAL_OUTCOME','RECONCILED')",
            name="ck_idempotency_records_state",
        ),
        sa.CheckConstraint(
            "reconciliation_outcome IS NULL OR reconciliation_outcome IN "
            "('EFFECT_CONFIRMED','NO_EFFECT_CONFIRMED')",
            name="ck_idempotency_records_reconciliation",
        ),
        sa.CheckConstraint(
            "(state = 'EXECUTING') = "
            "(current_attempt_id IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_idempotency_records_attempt_lease",
        ),
        sa.CheckConstraint(
            "(state = 'RECONCILED') = (reconciliation_outcome IS NOT NULL)",
            name="ck_idempotency_records_reconciled_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["identity.tenants.id"],
            name="fk_idempotency_records_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tool_definition_id"],
            ["tool_governance.tool_definitions.id"],
            name="fk_idempotency_records_tool",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tool_definition_id", "tool_version_id"],
            ["tool_governance.tool_versions.definition_id", "tool_governance.tool_versions.id"],
            name="fk_idempotency_records_tool_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_records"),
        sa.UniqueConstraint(
            "tenant_id",
            "tool_definition_id",
            "idempotency_key",
            name="uq_idempotency_records_logical_operation",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_idempotency_records_tenant_id_id"),
        schema="execution_control",
    )
    op.execute(
        """
        CREATE FUNCTION execution_control.protect_idempotency_transition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (NEW.id, NEW.tenant_id, NEW.tool_definition_id, NEW.tool_version_id,
              NEW.idempotency_key, NEW.request_digest, NEW.created_at)
             IS DISTINCT FROM
             (OLD.id, OLD.tenant_id, OLD.tool_definition_id, OLD.tool_version_id,
              OLD.idempotency_key, OLD.request_digest, OLD.created_at)
          THEN RAISE EXCEPTION 'idempotency binding identity is immutable'; END IF;
          IF NOT (
            (OLD.state = 'RESERVED' AND NEW.state = 'EXECUTING') OR
            (OLD.state = 'FAILED_PRE_EFFECT' AND NEW.state = 'EXECUTING') OR
            (OLD.state = 'EXECUTING' AND NEW.state IN
              ('SUCCEEDED','FAILED_PRE_EFFECT','UNKNOWN_EXTERNAL_OUTCOME')) OR
            (OLD.state = 'UNKNOWN_EXTERNAL_OUTCOME' AND NEW.state = 'RECONCILED') OR
            (OLD.state = 'RECONCILED' AND
              OLD.reconciliation_outcome = 'NO_EFFECT_CONFIRMED' AND
              NEW.state = 'EXECUTING')
          ) THEN RAISE EXCEPTION 'invalid idempotency state transition'; END IF;
          IF NEW.attempt_count < OLD.attempt_count OR NEW.attempt_count > OLD.attempt_count + 1
          THEN RAISE EXCEPTION 'invalid idempotency attempt count'; END IF;
          RETURN NEW;
        END; $$
        """
    )
    op.execute(
        "CREATE TRIGGER protect_idempotency_transition BEFORE UPDATE ON "
        "execution_control.idempotency_records FOR EACH ROW EXECUTE FUNCTION "
        "execution_control.protect_idempotency_transition()"
    )


def _rls_and_grants() -> None:
    groups = {
        "approval_governance": ("approval_requests", "approval_decisions", "approval_revocations"),
        "execution_control": ("idempotency_records",),
    }
    for schema, tables in groups.items():
        op.execute(f"REVOKE ALL ON SCHEMA {schema} FROM {RUNTIME}")
        op.execute(f"GRANT USAGE ON SCHEMA {schema} TO {RUNTIME}")
        for table in tables:
            op.execute(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"CREATE POLICY {table}_tenant_read ON {schema}.{table} FOR SELECT TO "
                f"{RUNTIME} USING (tenant_id = {TENANT})"
            )
            op.execute(
                f"CREATE POLICY {table}_tenant_insert ON {schema}.{table} FOR INSERT TO "
                f"{RUNTIME} WITH CHECK (tenant_id = {TENANT})"
            )
            op.execute(
                f"CREATE POLICY {table}_migration_control ON {schema}.{table} FOR ALL TO "
                f"{MIGRATOR} USING (true) WITH CHECK (true)"
            )
            op.execute(f"REVOKE ALL ON {schema}.{table} FROM {RUNTIME}")
    for table in ("approval_requests", "approval_decisions", "approval_revocations"):
        op.execute(f"GRANT SELECT, INSERT ON approval_governance.{table} TO {RUNTIME}")
    op.execute(
        "CREATE POLICY idempotency_records_tenant_update ON "
        f"execution_control.idempotency_records FOR UPDATE TO {RUNTIME} "
        f"USING (tenant_id = {TENANT}) WITH CHECK (tenant_id = {TENANT})"
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON execution_control.idempotency_records TO {RUNTIME}"
    )


def upgrade() -> None:
    _audit_columns()
    _approval_tables()
    _idempotency_table()
    _rls_and_grants()


def downgrade() -> None:
    connection = op.get_bind()
    history = connection.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM approval_governance.approval_requests) + "
            "(SELECT count(*) FROM approval_governance.approval_decisions) + "
            "(SELECT count(*) FROM approval_governance.approval_revocations) + "
            "(SELECT count(*) FROM execution_control.idempotency_records)"
        )
    ).scalar_one()
    refs = connection.execute(
        sa.text(
            "SELECT count(*) FROM audit.audit_records WHERE "
            "approval_request_id IS NOT NULL OR idempotency_record_id IS NOT NULL OR "
            "attempt_id IS NOT NULL"
        )
    ).scalar_one()
    if history or refs:
        raise RuntimeError("refusing lossy approval/idempotency downgrade while history exists")
    op.drop_table("idempotency_records", schema="execution_control")
    op.execute("DROP FUNCTION execution_control.protect_idempotency_transition()")
    op.execute("DROP SCHEMA execution_control")
    for table in ("approval_revocations", "approval_decisions", "approval_requests"):
        op.drop_table(table, schema="approval_governance")
    op.execute("DROP FUNCTION approval_governance.reject_history_mutation()")
    op.execute("DROP SCHEMA approval_governance")
    op.drop_index("ix_audit_records_idempotency", table_name="audit_records", schema="audit")
    op.drop_index("ix_audit_records_approval", table_name="audit_records", schema="audit")
    for name in ("attempt_id", "idempotency_record_id", "approval_request_id"):
        op.drop_column("audit_records", name, schema="audit")
