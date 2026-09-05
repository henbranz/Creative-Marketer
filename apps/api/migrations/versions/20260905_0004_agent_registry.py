"""Create immutable tenant agent registry.

Revision ID: 20260905_0004
Revises: 20260905_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0004"
down_revision: str | None = "20260905_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME_ROLE = "creative_marketer_runtime"
MIGRATOR_ROLE = "creative_marketer_migrator"
TENANT_EXPRESSION = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute("CREATE SCHEMA agent_governance")
    op.create_table(
        "agent_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_kind", sa.String(32), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True)),
        sa.Column("platform_template_id", postgresql.UUID(as_uuid=True)),
        sa.Column("agent_key", sa.String(64), nullable=False),
        sa.Column("agent_type", sa.String(64), nullable=False),
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
            "(scope_kind = 'platform' AND tenant_id IS NULL AND platform_template_id IS NULL) OR "
            "(scope_kind = 'tenant' AND tenant_id IS NOT NULL)",
            name="ck_agent_definitions_ownership",
        ),
        sa.CheckConstraint(
            "agent_key ~ '^[a-z][a-z0-9_]{1,63}$'",
            name="ck_agent_definitions_agent_key",
        ),
        sa.CheckConstraint(
            "agent_type ~ '^[a-z][a-z0-9_]{1,63}$'",
            name="ck_agent_definitions_agent_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'archived')",
            name="ck_agent_definitions_status",
        ),
        sa.CheckConstraint(
            "created_by_actor_kind IN ('user', 'workload', 'system')",
            name="ck_agent_definitions_created_actor_kind",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["identity.tenants.id"],
            name="fk_agent_definitions_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["platform_template_id"],
            ["agent_governance.agent_definitions.id"],
            name="fk_agent_definitions_platform_template_id_agent_definitions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_definitions"),
        schema="agent_governance",
    )
    op.create_index(
        "uq_agent_definitions_platform_key",
        "agent_definitions",
        ["agent_key"],
        unique=True,
        schema="agent_governance",
        postgresql_where=sa.text("scope_kind = 'platform'"),
    )
    op.create_index(
        "uq_agent_definitions_tenant_key",
        "agent_definitions",
        ["tenant_id", "agent_key"],
        unique=True,
        schema="agent_governance",
        postgresql_where=sa.text("scope_kind = 'tenant'"),
    )
    op.create_index(
        "ix_agent_definitions_platform_template",
        "agent_definitions",
        ["platform_template_id"],
        schema="agent_governance",
    )

    op.create_table(
        "agent_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_kind", sa.String(32), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True)),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("mission", sa.String(2000), nullable=False),
        sa.Column("responsibilities", postgresql.JSONB(), nullable=False),
        sa.Column("system_instructions", sa.Text(), nullable=False),
        sa.Column("prompt_revision", sa.String(64), nullable=False),
        sa.Column("model_policy", postgresql.JSONB(), nullable=False),
        sa.Column("run_budget_policy", postgresql.JSONB(), nullable=False),
        sa.Column("period_budget_policy", postgresql.JSONB(), nullable=False),
        sa.Column("read_scopes", postgresql.JSONB(), nullable=False),
        sa.Column("write_scopes", postgresql.JSONB(), nullable=False),
        sa.Column("memory_scopes", postgresql.JSONB(), nullable=False),
        sa.Column("allowed_tool_keys", postgresql.JSONB(), nullable=False),
        sa.Column("denied_tool_keys", postgresql.JSONB(), nullable=False),
        sa.Column("approval_policy_key", sa.String(128), nullable=False),
        sa.Column("output_contract_key", sa.String(128)),
        sa.Column("output_contract_version", sa.Integer()),
        sa.Column("configuration_schema_version", sa.Integer(), nullable=False),
        sa.Column("configuration_digest", sa.String(71), nullable=False),
        sa.Column("created_by_actor_kind", sa.String(32), nullable=False),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(scope_kind = 'platform' AND tenant_id IS NULL) OR "
            "(scope_kind = 'tenant' AND tenant_id IS NOT NULL)",
            name="ck_agent_versions_ownership",
        ),
        sa.CheckConstraint("version_number > 0", name="ck_agent_versions_version_number"),
        sa.CheckConstraint(
            "length(btrim(display_name)) > 0 AND length(btrim(mission)) > 0",
            name="ck_agent_versions_required_text",
        ),
        sa.CheckConstraint(
            "octet_length(system_instructions) BETWEEN 1 AND 20000",
            name="ck_agent_versions_instruction_size",
        ),
        sa.CheckConstraint(
            "lower(display_name || ' ' || mission || ' ' || responsibilities::text || ' ' || "
            "system_instructions || ' ' || model_policy::text || ' ' || "
            "run_budget_policy::text || ' ' || period_budget_policy::text) "
            "!~ '(bearer[[:space:]]+[^[:space:]]+|sk-[a-z0-9_-]{8,}|"
            "shpat_[a-z0-9]{8,}|gh[pousr]_[a-z0-9]{12,}|"
            '"(authorization|cookie|password|secret|access_token|refresh_token|id_token|'
            "api_key|client_secret|credential)\"[[:space:]]*:)'",
            name="ck_agent_versions_no_credentials",
        ),
        sa.CheckConstraint(
            "configuration_schema_version = 1",
            name="ck_agent_versions_configuration_schema_version",
        ),
        sa.CheckConstraint(
            "configuration_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_agent_versions_configuration_digest",
        ),
        sa.CheckConstraint(
            "created_by_actor_kind IN ('user', 'workload', 'system')",
            name="ck_agent_versions_created_actor_kind",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(responsibilities) = 'array' AND "
            "jsonb_typeof(model_policy) = 'object' AND "
            "jsonb_typeof(run_budget_policy) = 'object' AND "
            "jsonb_typeof(period_budget_policy) = 'object' AND "
            "jsonb_typeof(read_scopes) = 'array' AND "
            "jsonb_typeof(write_scopes) = 'array' AND "
            "jsonb_typeof(memory_scopes) = 'array' AND "
            "jsonb_typeof(allowed_tool_keys) = 'array' AND "
            "jsonb_typeof(denied_tool_keys) = 'array'",
            name="ck_agent_versions_json_shapes",
        ),
        sa.CheckConstraint(
            "(output_contract_key IS NULL) = (output_contract_version IS NULL) AND "
            "(output_contract_version IS NULL OR output_contract_version > 0)",
            name="ck_agent_versions_output_contract",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["agent_governance.agent_definitions.id"],
            name="fk_agent_versions_definition_id_agent_definitions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_versions"),
        sa.UniqueConstraint(
            "definition_id", "version_number", name="uq_agent_versions_definition_version"
        ),
        sa.UniqueConstraint("definition_id", "id", name="uq_agent_versions_definition_id_id"),
        schema="agent_governance",
    )

    op.create_table(
        "agent_activations",
        sa.Column("definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("active_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_kind", sa.String(32), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True)),
        sa.Column("activated_by_actor_kind", sa.String(32), nullable=False),
        sa.Column("activated_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "activated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(scope_kind = 'platform' AND tenant_id IS NULL) OR "
            "(scope_kind = 'tenant' AND tenant_id IS NOT NULL)",
            name="ck_agent_activations_ownership",
        ),
        sa.CheckConstraint(
            "activated_by_actor_kind IN ('user', 'workload', 'system')",
            name="ck_agent_activations_actor_kind",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["agent_governance.agent_definitions.id"],
            name="fk_agent_activations_definition_id_agent_definitions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["definition_id", "active_version_id"],
            ["agent_governance.agent_versions.definition_id", "agent_governance.agent_versions.id"],
            name="fk_agent_activations_definition_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("definition_id", name="pk_agent_activations"),
        schema="agent_governance",
    )

    op.execute(
        """
        CREATE FUNCTION agent_governance.enforce_platform_template()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, agent_governance
        AS $$
        DECLARE template_scope text;
        BEGIN
          IF NEW.platform_template_id IS NOT NULL THEN
            SELECT scope_kind INTO template_scope
            FROM agent_governance.agent_definitions
            WHERE id = NEW.platform_template_id;
            IF template_scope IS DISTINCT FROM 'platform' THEN
              RAISE EXCEPTION 'platform_template_id must reference a platform definition';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER enforce_platform_template BEFORE INSERT OR UPDATE "
        "ON agent_governance.agent_definitions FOR EACH ROW "
        "EXECUTE FUNCTION agent_governance.enforce_platform_template()"
    )
    op.execute(
        """
        CREATE FUNCTION agent_governance.protect_agent_definition_identity()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF (NEW.id, NEW.scope_kind, NEW.tenant_id, NEW.platform_template_id, NEW.agent_key,
              NEW.agent_type, NEW.created_by_actor_kind, NEW.created_by_actor_id, NEW.created_at)
             IS DISTINCT FROM
             (OLD.id, OLD.scope_kind, OLD.tenant_id, OLD.platform_template_id, OLD.agent_key,
              OLD.agent_type, OLD.created_by_actor_kind, OLD.created_by_actor_id, OLD.created_at)
          THEN
            RAISE EXCEPTION 'agent definition identity is immutable';
          END IF;
          IF OLD.status = 'archived' AND NEW.status IS DISTINCT FROM 'archived' THEN
            RAISE EXCEPTION 'archived agent definitions cannot be resurrected';
          END IF;
          IF NEW.status = OLD.status THEN
            RAISE EXCEPTION 'agent definition update must change lifecycle status';
          END IF;
          IF NOT (
            (OLD.status = 'active' AND NEW.status IN ('disabled', 'archived')) OR
            (OLD.status = 'disabled' AND NEW.status = 'archived')
          ) THEN
            RAISE EXCEPTION 'invalid agent definition lifecycle transition';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER protect_agent_definition_identity BEFORE UPDATE "
        "ON agent_governance.agent_definitions FOR EACH ROW "
        "EXECUTE FUNCTION agent_governance.protect_agent_definition_identity()"
    )
    op.execute(
        """
        CREATE FUNCTION agent_governance.enforce_agent_version_owner()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, agent_governance
        AS $$
        DECLARE definition_scope text;
        DECLARE definition_tenant uuid;
        BEGIN
          SELECT scope_kind, tenant_id INTO definition_scope, definition_tenant
          FROM agent_governance.agent_definitions
          WHERE id = NEW.definition_id;
          IF NOT FOUND OR definition_scope IS DISTINCT FROM NEW.scope_kind
             OR definition_tenant IS DISTINCT FROM NEW.tenant_id THEN
            RAISE EXCEPTION 'agent version ownership must match its definition';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER enforce_agent_version_owner BEFORE INSERT "
        "ON agent_governance.agent_versions FOR EACH ROW "
        "EXECUTE FUNCTION agent_governance.enforce_agent_version_owner()"
    )
    op.execute(
        """
        CREATE FUNCTION agent_governance.enforce_agent_activation_owner()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, agent_governance
        AS $$
        DECLARE definition_scope text;
        DECLARE definition_tenant uuid;
        BEGIN
          SELECT scope_kind, tenant_id INTO definition_scope, definition_tenant
          FROM agent_governance.agent_definitions
          WHERE id = NEW.definition_id;
          IF NOT FOUND OR definition_scope IS DISTINCT FROM NEW.scope_kind
             OR definition_tenant IS DISTINCT FROM NEW.tenant_id THEN
            RAISE EXCEPTION 'agent activation ownership must match its definition';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER enforce_agent_activation_owner BEFORE INSERT OR UPDATE "
        "ON agent_governance.agent_activations FOR EACH ROW "
        "EXECUTE FUNCTION agent_governance.enforce_agent_activation_owner()"
    )

    for table in ("agent_definitions", "agent_versions", "agent_activations"):
        op.execute(f"ALTER TABLE agent_governance.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE agent_governance.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_read ON agent_governance.{table} FOR SELECT TO {RUNTIME_ROLE} "
            f"USING (scope_kind = 'platform' OR tenant_id = {TENANT_EXPRESSION})"
        )
        op.execute(
            f"CREATE POLICY {table}_tenant_insert ON agent_governance.{table} "
            f"FOR INSERT TO {RUNTIME_ROLE} "
            f"WITH CHECK (scope_kind = 'tenant' AND tenant_id = {TENANT_EXPRESSION})"
        )
        op.execute(
            f"CREATE POLICY {table}_migration_control ON agent_governance.{table} "
            f"FOR ALL TO {MIGRATOR_ROLE} USING (true) WITH CHECK (true)"
        )
    op.execute(
        f"CREATE POLICY agent_definitions_tenant_update ON "
        f"agent_governance.agent_definitions FOR UPDATE TO {RUNTIME_ROLE} "
        f"USING (scope_kind = 'tenant' AND tenant_id = {TENANT_EXPRESSION}) "
        f"WITH CHECK (scope_kind = 'tenant' AND tenant_id = {TENANT_EXPRESSION})"
    )
    op.execute(
        f"CREATE POLICY agent_activations_tenant_update ON "
        f"agent_governance.agent_activations FOR UPDATE TO {RUNTIME_ROLE} "
        f"USING (scope_kind = 'tenant' AND tenant_id = {TENANT_EXPRESSION}) "
        f"WITH CHECK (scope_kind = 'tenant' AND tenant_id = {TENANT_EXPRESSION})"
    )

    op.execute(f"REVOKE ALL ON SCHEMA agent_governance FROM {RUNTIME_ROLE}")
    for table in ("agent_definitions", "agent_versions", "agent_activations"):
        op.execute(f"REVOKE ALL ON agent_governance.{table} FROM {RUNTIME_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA agent_governance TO {RUNTIME_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON agent_governance.agent_definitions TO {RUNTIME_ROLE}"
    )
    op.execute(f"GRANT SELECT, INSERT ON agent_governance.agent_versions TO {RUNTIME_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON agent_governance.agent_activations TO {RUNTIME_ROLE}"
    )


def downgrade() -> None:
    op.drop_table("agent_activations", schema="agent_governance")
    op.drop_table("agent_versions", schema="agent_governance")
    op.drop_index(
        "ix_agent_definitions_platform_template",
        table_name="agent_definitions",
        schema="agent_governance",
    )
    op.drop_index(
        "uq_agent_definitions_tenant_key",
        table_name="agent_definitions",
        schema="agent_governance",
    )
    op.drop_index(
        "uq_agent_definitions_platform_key",
        table_name="agent_definitions",
        schema="agent_governance",
    )
    op.drop_table("agent_definitions", schema="agent_governance")
    op.execute("DROP FUNCTION agent_governance.enforce_agent_activation_owner()")
    op.execute("DROP FUNCTION agent_governance.enforce_agent_version_owner()")
    op.execute("DROP FUNCTION agent_governance.protect_agent_definition_identity()")
    op.execute("DROP FUNCTION agent_governance.enforce_platform_template()")
    op.execute("DROP SCHEMA agent_governance")
