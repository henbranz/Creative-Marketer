"""Create deterministic tenant ToolPermission policy storage.

Revision ID: 20260905_0006
Revises: 20260905_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0006"
down_revision: str | None = "20260905_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME_ROLE = "creative_marketer_runtime"
MIGRATOR_ROLE = "creative_marketer_migrator"
TENANT = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.add_column(
        "audit_records", sa.Column("permission_id", postgresql.UUID(as_uuid=True)), schema="audit"
    )
    op.add_column(
        "audit_records",
        sa.Column("permission_version_id", postgresql.UUID(as_uuid=True)),
        schema="audit",
    )
    op.create_index(
        "ix_audit_records_permission",
        "audit_records",
        ["permission_id", "occurred_at"],
        schema="audit",
    )
    op.create_unique_constraint(
        "uq_agent_definitions_tenant_id_id",
        "agent_definitions",
        ["tenant_id", "id"],
        schema="agent_governance",
    )
    op.execute("CREATE SCHEMA permission_governance")
    op.create_table(
        "tool_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by_actor_kind", sa.String(32), nullable=False),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('active','disabled','archived')", name="ck_tool_permissions_status"
        ),
        sa.CheckConstraint(
            "created_by_actor_kind = 'user'", name="ck_tool_permissions_created_actor_kind"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_definition_id"],
            [
                "agent_governance.agent_definitions.tenant_id",
                "agent_governance.agent_definitions.id",
            ],
            name="fk_tool_permissions_tenant_agent",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tool_definition_id"],
            ["tool_governance.tool_definitions.id"],
            name="fk_tool_permissions_tool",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tool_permissions"),
        sa.UniqueConstraint(
            "tenant_id",
            "agent_definition_id",
            "tool_definition_id",
            name="uq_tool_permissions_subject",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tool_permissions_tenant_id_id"),
        schema="permission_governance",
    )
    op.create_table(
        "tool_permission_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("effect", sa.String(16), nullable=False),
        sa.Column("allowed_scopes", postgresql.JSONB(), nullable=False),
        sa.Column("allowed_environments", postgresql.JSONB(), nullable=False),
        sa.Column("approval_behavior", sa.String(32), nullable=False),
        sa.Column("policy_schema_version", sa.Integer(), nullable=False),
        sa.Column("configuration_digest", sa.String(71), nullable=False),
        sa.Column("created_by_actor_kind", sa.String(32), nullable=False),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version_number > 0", name="ck_tool_permission_versions_version_number"),
        sa.CheckConstraint("effect IN ('GRANT','DENY')", name="ck_tool_permission_versions_effect"),
        sa.CheckConstraint(
            "approval_behavior IN ('RISK_DEFAULT','ALWAYS')",
            name="ck_tool_permission_versions_approval_behavior",
        ),
        sa.CheckConstraint(
            "policy_schema_version = 1", name="ck_tool_permission_versions_schema_version"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(allowed_scopes) = 'array' AND "
            "jsonb_typeof(allowed_environments) = 'array' AND "
            "jsonb_array_length(allowed_environments) > 0",
            name="ck_tool_permission_versions_json_shapes",
        ),
        sa.CheckConstraint(
            "configuration_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_tool_permission_versions_digest",
        ),
        sa.CheckConstraint(
            "created_by_actor_kind = 'user'", name="ck_tool_permission_versions_created_actor_kind"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "permission_id"],
            [
                "permission_governance.tool_permissions.tenant_id",
                "permission_governance.tool_permissions.id",
            ],
            name="fk_tool_permission_versions_tenant_permission",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tool_permission_versions"),
        sa.UniqueConstraint(
            "permission_id", "version_number", name="uq_tool_permission_versions_permission_version"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "permission_id",
            "id",
            name="uq_tool_permission_versions_tenant_permission_id",
        ),
        schema="permission_governance",
    )
    op.create_table(
        "tool_permission_activations",
        sa.Column("permission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("active_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("activated_by_actor_kind", sa.String(32), nullable=False),
        sa.Column("activated_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "activated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "activated_by_actor_kind = 'user'", name="ck_tool_permission_activations_actor_kind"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "permission_id"],
            [
                "permission_governance.tool_permissions.tenant_id",
                "permission_governance.tool_permissions.id",
            ],
            name="fk_tool_permission_activations_tenant_permission",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "permission_id", "active_version_id"],
            [
                "permission_governance.tool_permission_versions.tenant_id",
                "permission_governance.tool_permission_versions.permission_id",
                "permission_governance.tool_permission_versions.id",
            ],
            name="fk_tool_permission_activations_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("permission_id", name="pk_tool_permission_activations"),
        schema="permission_governance",
    )
    op.execute(
        """
        CREATE FUNCTION permission_governance.protect_permission_identity()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (NEW.id, NEW.tenant_id, NEW.agent_definition_id, NEW.tool_definition_id,
              NEW.created_by_actor_kind, NEW.created_by_actor_id, NEW.created_at)
             IS DISTINCT FROM
             (OLD.id, OLD.tenant_id, OLD.agent_definition_id, OLD.tool_definition_id,
              OLD.created_by_actor_kind, OLD.created_by_actor_id, OLD.created_at)
          THEN RAISE EXCEPTION 'tool permission identity is immutable'; END IF;
          IF NOT ((OLD.status = 'active' AND NEW.status IN ('disabled','archived')) OR
                  (OLD.status = 'disabled' AND NEW.status = 'archived'))
          THEN RAISE EXCEPTION 'invalid tool permission lifecycle transition'; END IF;
          RETURN NEW;
        END; $$
        """
    )
    op.execute(
        "CREATE TRIGGER protect_permission_identity BEFORE UPDATE ON "
        "permission_governance.tool_permissions FOR EACH ROW EXECUTE FUNCTION "
        "permission_governance.protect_permission_identity()"
    )
    for table in ("tool_permissions", "tool_permission_versions", "tool_permission_activations"):
        op.execute(f"ALTER TABLE permission_governance.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE permission_governance.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_read ON permission_governance.{table} "
            f"FOR SELECT TO {RUNTIME_ROLE} USING (tenant_id = {TENANT})"
        )
        op.execute(
            f"CREATE POLICY {table}_tenant_insert ON permission_governance.{table} "
            f"FOR INSERT TO {RUNTIME_ROLE} WITH CHECK (tenant_id = {TENANT})"
        )
        op.execute(
            f"CREATE POLICY {table}_migration_control ON permission_governance.{table} "
            f"FOR ALL TO {MIGRATOR_ROLE} USING (true) WITH CHECK (true)"
        )
    for table in ("tool_permissions", "tool_permission_activations"):
        op.execute(
            f"CREATE POLICY {table}_tenant_update ON permission_governance.{table} "
            f"FOR UPDATE TO {RUNTIME_ROLE} USING (tenant_id = {TENANT}) "
            f"WITH CHECK (tenant_id = {TENANT})"
        )
    op.execute(f"REVOKE ALL ON SCHEMA permission_governance FROM {RUNTIME_ROLE}")
    for table in ("tool_permissions", "tool_permission_versions", "tool_permission_activations"):
        op.execute(f"REVOKE ALL ON permission_governance.{table} FROM {RUNTIME_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA permission_governance TO {RUNTIME_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON permission_governance.tool_permissions TO {RUNTIME_ROLE}"
    )
    op.execute(
        f"GRANT SELECT, INSERT ON permission_governance.tool_permission_versions TO {RUNTIME_ROLE}"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON permission_governance.tool_permission_activations "
        f"TO {RUNTIME_ROLE}"
    )


def downgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM permission_governance.tool_permissions) + "
            "(SELECT count(*) FROM permission_governance.tool_permission_versions)"
        )
    ).scalar_one()
    refs = connection.execute(
        sa.text(
            "SELECT count(*) FROM audit.audit_records WHERE permission_id IS NOT NULL OR "
            "permission_version_id IS NOT NULL"
        )
    ).scalar_one()
    if rows or refs:
        raise RuntimeError("refusing lossy permission policy downgrade while history exists")
    op.drop_table("tool_permission_activations", schema="permission_governance")
    op.drop_table("tool_permission_versions", schema="permission_governance")
    op.drop_table("tool_permissions", schema="permission_governance")
    op.execute("DROP FUNCTION permission_governance.protect_permission_identity()")
    op.execute("DROP SCHEMA permission_governance")
    op.drop_constraint(
        "uq_agent_definitions_tenant_id_id",
        "agent_definitions",
        schema="agent_governance",
        type_="unique",
    )
    op.drop_index("ix_audit_records_permission", table_name="audit_records", schema="audit")
    op.drop_column("audit_records", "permission_version_id", schema="audit")
    op.drop_column("audit_records", "permission_id", schema="audit")
