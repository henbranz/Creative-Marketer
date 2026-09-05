"""Create immutable platform Tool Registry.

Revision ID: 20260905_0005
Revises: 20260905_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0005"
down_revision: str | None = "20260905_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME_ROLE = "creative_marketer_runtime"
MIGRATOR_ROLE = "creative_marketer_migrator"


def upgrade() -> None:
    op.add_column(
        "audit_records",
        sa.Column("tool_definition_id", postgresql.UUID(as_uuid=True)),
        schema="audit",
    )
    op.add_column(
        "audit_records",
        sa.Column("tool_version_id", postgresql.UUID(as_uuid=True)),
        schema="audit",
    )
    op.create_index(
        "ix_audit_records_tool_definition",
        "audit_records",
        ["tool_definition_id", "occurred_at"],
        schema="audit",
    )
    op.execute(
        f"CREATE POLICY audit_platform_control ON audit.audit_records FOR INSERT "
        f"TO {MIGRATOR_ROLE} WITH CHECK (scope_kind = 'platform' AND tenant_id IS NULL)"
    )

    op.execute("CREATE SCHEMA tool_governance")
    op.create_table(
        "tool_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_key", sa.String(128), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
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
            "tool_key ~ '^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)+$'",
            name="ck_tool_definitions_tool_key",
        ),
        sa.CheckConstraint(
            "category ~ '^[a-z][a-z0-9_]{0,63}$'", name="ck_tool_definitions_category"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'archived')",
            name="ck_tool_definitions_status",
        ),
        sa.CheckConstraint(
            "created_by_actor_kind IN ('workload', 'system')",
            name="ck_tool_definitions_created_actor_kind",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tool_definitions"),
        sa.UniqueConstraint("tool_key", name="uq_tool_definitions_tool_key"),
        schema="tool_governance",
    )
    op.create_table(
        "tool_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(2000), nullable=False),
        sa.Column("risk_level", sa.String(2), nullable=False),
        sa.Column("side_effect_class", sa.String(32), nullable=False),
        sa.Column("execution_class", sa.String(32), nullable=False),
        sa.Column("credential_boundary", sa.String(32), nullable=False),
        sa.Column("idempotency_requirement", sa.String(32), nullable=False),
        sa.Column("input_schema", postgresql.JSONB(), nullable=False),
        sa.Column("output_schema", postgresql.JSONB(), nullable=False),
        sa.Column("input_schema_digest", sa.String(71), nullable=False),
        sa.Column("output_schema_digest", sa.String(71), nullable=False),
        sa.Column("capability_tags", postgresql.JSONB(), nullable=False),
        sa.Column("configuration_schema_version", sa.Integer(), nullable=False),
        sa.Column("configuration_digest", sa.String(71), nullable=False),
        sa.Column("created_by_actor_kind", sa.String(32), nullable=False),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version_number > 0", name="ck_tool_versions_version_number"),
        sa.CheckConstraint(
            "length(btrim(display_name)) > 0 AND length(btrim(description)) > 0",
            name="ck_tool_versions_required_text",
        ),
        sa.CheckConstraint(
            "risk_level IN ('R0','R1','R2','R3','R4','R5','R6','R7')",
            name="ck_tool_versions_risk",
        ),
        sa.CheckConstraint(
            "side_effect_class IN ('READ_ONLY','INTERNAL_MUTATION','EXTERNAL_MUTATION')",
            name="ck_tool_versions_side_effect",
        ),
        sa.CheckConstraint(
            "execution_class IN ('INTERNAL','CONNECTOR','PROVIDER')",
            name="ck_tool_versions_execution",
        ),
        sa.CheckConstraint(
            "credential_boundary IN ('NONE','CONNECTOR')",
            name="ck_tool_versions_credential",
        ),
        sa.CheckConstraint(
            "idempotency_requirement IN ('NOT_APPLICABLE','SUPPORTED','REQUIRED')",
            name="ck_tool_versions_idempotency",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_schema) = 'object' AND "
            "jsonb_typeof(output_schema) = 'object' AND "
            "jsonb_typeof(capability_tags) = 'array'",
            name="ck_tool_versions_json_shapes",
        ),
        sa.CheckConstraint(
            "octet_length(input_schema::text) <= 131072 AND "
            "octet_length(output_schema::text) <= 131072",
            name="ck_tool_versions_schema_size",
        ),
        sa.CheckConstraint(
            "configuration_schema_version = 1",
            name="ck_tool_versions_configuration_schema_version",
        ),
        sa.CheckConstraint(
            "input_schema_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "output_schema_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "configuration_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_tool_versions_digests",
        ),
        sa.CheckConstraint(
            "created_by_actor_kind IN ('workload', 'system')",
            name="ck_tool_versions_created_actor_kind",
        ),
        sa.CheckConstraint(
            "lower(display_name || ' ' || description || ' ' || input_schema::text || ' ' || "
            "output_schema::text || ' ' || capability_tags::text) "
            "!~ '(bearer[[:space:]]+[^[:space:]\"}]+|sk-[a-z0-9_-]{8,}|"
            "shpat_[a-z0-9]{8,}|gh[pousr]_[a-z0-9]{12,}|"
            "(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|password|"
            "authorization)[[:space:]]*[:=][[:space:]]*[^[:space:]\"}]+)'",
            name="ck_tool_versions_no_credentials",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["tool_governance.tool_definitions.id"],
            name="fk_tool_versions_definition_id_tool_definitions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tool_versions"),
        sa.UniqueConstraint(
            "definition_id", "version_number", name="uq_tool_versions_definition_version"
        ),
        sa.UniqueConstraint("definition_id", "id", name="uq_tool_versions_definition_id_id"),
        schema="tool_governance",
    )
    op.create_table(
        "tool_activations",
        sa.Column("definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("active_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("activated_by_actor_kind", sa.String(32), nullable=False),
        sa.Column("activated_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "activated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "activated_by_actor_kind IN ('workload', 'system')",
            name="ck_tool_activations_activated_actor_kind",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["tool_governance.tool_definitions.id"],
            name="fk_tool_activations_definition_id_tool_definitions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id", "active_version_id"],
            ["tool_governance.tool_versions.definition_id", "tool_governance.tool_versions.id"],
            name="fk_tool_activations_definition_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("definition_id", name="pk_tool_activations"),
        schema="tool_governance",
    )

    op.execute(
        """
        CREATE FUNCTION tool_governance.protect_tool_definition_identity()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF (NEW.id, NEW.tool_key, NEW.category, NEW.created_by_actor_kind,
              NEW.created_by_actor_id, NEW.created_at)
             IS DISTINCT FROM
             (OLD.id, OLD.tool_key, OLD.category, OLD.created_by_actor_kind,
              OLD.created_by_actor_id, OLD.created_at)
          THEN
            RAISE EXCEPTION 'tool definition identity is immutable';
          END IF;
          IF OLD.status = 'archived' AND NEW.status IS DISTINCT FROM 'archived' THEN
            RAISE EXCEPTION 'archived tool definitions cannot be resurrected';
          END IF;
          IF NEW.status = OLD.status THEN
            RAISE EXCEPTION 'tool definition update must change lifecycle status';
          END IF;
          IF NOT (
            (OLD.status = 'active' AND NEW.status IN ('disabled', 'archived')) OR
            (OLD.status = 'disabled' AND NEW.status = 'archived')
          ) THEN
            RAISE EXCEPTION 'invalid tool definition lifecycle transition';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER protect_tool_definition_identity BEFORE UPDATE "
        "ON tool_governance.tool_definitions FOR EACH ROW "
        "EXECUTE FUNCTION tool_governance.protect_tool_definition_identity()"
    )

    op.execute(f"REVOKE ALL ON SCHEMA tool_governance FROM {RUNTIME_ROLE}")
    for table in ("tool_definitions", "tool_versions", "tool_activations"):
        op.execute(f"REVOKE ALL ON tool_governance.{table} FROM {RUNTIME_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA tool_governance TO {RUNTIME_ROLE}")
    for table in ("tool_definitions", "tool_versions", "tool_activations"):
        op.execute(f"GRANT SELECT ON tool_governance.{table} TO {RUNTIME_ROLE}")


def downgrade() -> None:
    connection = op.get_bind()
    registry_rows = connection.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM tool_governance.tool_definitions) + "
            "(SELECT count(*) FROM tool_governance.tool_versions)"
        )
    ).scalar_one()
    audit_refs = connection.execute(
        sa.text(
            "SELECT count(*) FROM audit.audit_records "
            "WHERE tool_definition_id IS NOT NULL OR tool_version_id IS NOT NULL"
        )
    ).scalar_one()
    if registry_rows or audit_refs:
        raise RuntimeError("refusing lossy Tool Registry downgrade while history exists")

    op.drop_table("tool_activations", schema="tool_governance")
    op.drop_table("tool_versions", schema="tool_governance")
    op.drop_table("tool_definitions", schema="tool_governance")
    op.execute("DROP FUNCTION tool_governance.protect_tool_definition_identity()")
    op.execute("DROP SCHEMA tool_governance")
    op.drop_index("ix_audit_records_tool_definition", table_name="audit_records", schema="audit")
    op.execute("DROP POLICY audit_platform_control ON audit.audit_records")
    op.drop_column("audit_records", "tool_version_id", schema="audit")
    op.drop_column("audit_records", "tool_definition_id", schema="audit")
