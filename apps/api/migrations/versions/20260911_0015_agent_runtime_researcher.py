# ruff: noqa: E501
"""Create durable AgentRun, budget ledger, and ResearchSnapshot.

Revision ID: 20260911_0015
Revises: 20260906_0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260911_0015"
down_revision: str | None = "20260906_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME = "creative_marketer_runtime"
MIGRATOR = "creative_marketer_migrator"
TENANT = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def _secure(schema: str, table: str, permissions: str) -> None:
    op.execute(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_migration_control ON {schema}.{table} FOR ALL TO {MIGRATOR} USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY {table}_runtime_tenant ON {schema}.{table} FOR ALL TO {RUNTIME} USING (tenant_id = {TENANT}) WITH CHECK (tenant_id = {TENANT})"
    )
    op.execute(f"REVOKE ALL ON {schema}.{table} FROM PUBLIC, {RUNTIME}")
    op.execute(f"GRANT {permissions} ON {schema}.{table} TO {RUNTIME}")


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS agent_runtime AUTHORIZATION creative_marketer_migrator")
    op.execute(
        f"REVOKE ALL ON SCHEMA agent_runtime FROM PUBLIC; GRANT USAGE ON SCHEMA agent_runtime TO {RUNTIME}"
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_agent_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resolved_agent_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_version_number", sa.Integer(), nullable=False),
        sa.Column("agent_configuration_digest", sa.String(71), nullable=False),
        sa.Column("prompt_revision", sa.String(128), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_snapshot_digest", sa.String(71), nullable=False),
        sa.Column("product_snapshot_schema_version", sa.Integer(), nullable=False),
        sa.Column("research_context_digest", sa.String(71), nullable=False),
        sa.Column("context_digest", sa.String(71), nullable=False),
        sa.Column("selected_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("model_profile_key", sa.String(128), nullable=False),
        sa.Column("output_contract_key", sa.String(128), nullable=False),
        sa.Column("output_contract_version", sa.Integer(), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("initiated_by_actor_kind", sa.String(32), nullable=False),
        sa.Column("initiated_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_cost", sa.Numeric(19, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("executed_by_workload_id", sa.String(128)),
        sa.Column("resolved_provider", sa.String(64)),
        sa.Column("resolved_model", sa.String(128)),
        sa.Column("resolved_model_route_version", sa.String(64)),
        sa.Column("pricing_version", sa.String(64)),
        sa.Column("reasoning_effort", sa.String(32)),
        sa.Column("max_output_tokens", sa.Integer()),
        sa.Column("max_total_tokens", sa.Integer(), nullable=False),
        sa.Column("model_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("estimated_cost", sa.Numeric(19, 6), nullable=False, server_default="0"),
        sa.Column("provider_response_id", sa.String(256)),
        sa.Column("result_ref", sa.String(256)),
        sa.Column("failure_code", sa.String(100)),
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
            ["resolved_agent_definition_id"],
            ["agent_governance.agent_definitions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_agent_definition_id", "agent_version_id"],
            [
                "agent_governance.agent_versions.definition_id",
                "agent_governance.agent_versions.id",
            ],
            name="fk_agent_runs_resolved_definition_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["catalog.products.tenant_id", "catalog.products.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_snapshot_id"],
            [
                "catalog.product_knowledge_snapshots.tenant_id",
                "catalog.product_knowledge_snapshots.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_agent_runs_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_agent_runs_tenant_idempotency"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','CANCELLED','BLOCKED_BUDGET')",
            name="ck_agent_runs_status",
        ),
        sa.CheckConstraint(
            "agent_configuration_digest ~ '^sha256:[0-9a-f]{64}$' AND product_snapshot_digest ~ '^sha256:[0-9a-f]{64}$' AND research_context_digest ~ '^sha256:[0-9a-f]{64}$' AND context_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_agent_runs_digests",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(selected_evidence) = 'array' AND jsonb_array_length(selected_evidence) BETWEEN 1 AND 120",
            name="ck_agent_runs_selected_evidence",
        ),
        sa.CheckConstraint(
            "reserved_cost >= 0 AND estimated_cost >= 0 AND model_call_count BETWEEN 0 AND 1 AND input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0",
            name="ck_agent_runs_usage",
        ),
        schema="agent_runtime",
    )
    op.create_index(
        "ix_agent_runs_tenant_product_created",
        "agent_runs",
        ["tenant_id", "product_id", "created_at"],
        schema="agent_runtime",
    )
    op.create_index(
        "uq_agent_runs_one_active_researcher",
        "agent_runs",
        ["tenant_id", "product_id", "requested_agent_definition_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING','RUNNING')"),
        schema="agent_runtime",
    )
    op.create_table(
        "agent_budget_usage",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_definition_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("period_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("reserved_runs", sa.Integer(), nullable=False),
        sa.Column("reserved_cost", sa.Numeric(19, 6), nullable=False),
        sa.Column("actual_cost", sa.Numeric(19, 6), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_definition_id"],
            [
                "agent_governance.agent_definitions.tenant_id",
                "agent_governance.agent_definitions.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "reserved_runs >= 0 AND reserved_cost >= 0 AND actual_cost >= 0",
            name="ck_agent_budget_usage_nonnegative",
        ),
        schema="agent_runtime",
    )
    op.create_table(
        "research_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("product_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_snapshot_digest", sa.String(71), nullable=False),
        sa.Column("research_context_digest", sa.String(71), nullable=False),
        sa.Column("findings", postgresql.JSONB(), nullable=False),
        sa.Column("research_gaps", postgresql.JSONB(), nullable=False),
        sa.Column("recommended_next_sources", postgresql.JSONB(), nullable=False),
        sa.Column("semantic_digest", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["catalog.products.tenant_id", "catalog.products.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_snapshot_id"],
            [
                "catalog.product_knowledge_snapshots.tenant_id",
                "catalog.product_knowledge_snapshots.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_run_id"],
            ["agent_runtime.agent_runs.tenant_id", "agent_runtime.agent_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_research_snapshots_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "agent_run_id", name="uq_research_snapshots_agent_run"),
        sa.CheckConstraint(
            "schema_version = 1 AND jsonb_typeof(findings) = 'array' AND jsonb_array_length(findings) BETWEEN 1 AND 30",
            name="ck_research_snapshots_findings",
        ),
        sa.CheckConstraint(
            "product_snapshot_digest ~ '^sha256:[0-9a-f]{64}$' AND research_context_digest ~ '^sha256:[0-9a-f]{64}$' AND semantic_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_research_snapshots_digests",
        ),
        schema="research",
    )
    op.create_index(
        "ix_research_snapshots_tenant_product_created",
        "research_snapshots",
        ["tenant_id", "product_id", "created_at"],
        schema="research",
    )
    op.execute(
        "CREATE FUNCTION agent_runtime.protect_agent_run() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF (NEW.tenant_id,NEW.requested_agent_definition_id,NEW.resolved_agent_definition_id,NEW.agent_version_id,NEW.agent_version_number,NEW.agent_configuration_digest,NEW.prompt_revision,NEW.product_id,NEW.product_snapshot_id,NEW.product_snapshot_digest,NEW.product_snapshot_schema_version,NEW.research_context_digest,NEW.context_digest,NEW.selected_evidence,NEW.model_profile_key,NEW.output_contract_key,NEW.output_contract_version,NEW.correlation_id,NEW.initiated_by_actor_kind,NEW.initiated_by_actor_id,NEW.period_start,NEW.reserved_cost,NEW.currency,NEW.idempotency_key,NEW.created_at) IS DISTINCT FROM (OLD.tenant_id,OLD.requested_agent_definition_id,OLD.resolved_agent_definition_id,OLD.agent_version_id,OLD.agent_version_number,OLD.agent_configuration_digest,OLD.prompt_revision,OLD.product_id,OLD.product_snapshot_id,OLD.product_snapshot_digest,OLD.product_snapshot_schema_version,OLD.research_context_digest,OLD.context_digest,OLD.selected_evidence,OLD.model_profile_key,OLD.output_contract_key,OLD.output_contract_version,OLD.correlation_id,OLD.initiated_by_actor_kind,OLD.initiated_by_actor_id,OLD.period_start,OLD.reserved_cost,OLD.currency,OLD.idempotency_key,OLD.created_at) THEN RAISE EXCEPTION 'AgentRun provenance is immutable'; END IF; IF OLD.status <> NEW.status AND NOT ((OLD.status='PENDING' AND NEW.status IN ('RUNNING','CANCELLED','BLOCKED_BUDGET','FAILED')) OR (OLD.status='RUNNING' AND NEW.status IN ('SUCCEEDED','FAILED','CANCELLED'))) THEN RAISE EXCEPTION 'invalid AgentRun transition'; END IF; IF OLD.status IN ('SUCCEEDED','FAILED','CANCELLED','BLOCKED_BUDGET') AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'terminal AgentRun is immutable'; END IF; RETURN NEW; END; $$"
    )
    op.execute(
        "CREATE TRIGGER protect_agent_run BEFORE UPDATE ON agent_runtime.agent_runs FOR EACH ROW EXECUTE FUNCTION agent_runtime.protect_agent_run()"
    )
    op.execute("REVOKE ALL ON FUNCTION agent_runtime.protect_agent_run() FROM PUBLIC")
    op.execute(
        "CREATE FUNCTION research.protect_research_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'ResearchSnapshot is immutable'; END; $$"
    )
    op.execute(
        "CREATE TRIGGER protect_research_snapshot BEFORE UPDATE OR DELETE ON research.research_snapshots FOR EACH ROW EXECUTE FUNCTION research.protect_research_snapshot()"
    )
    op.execute("REVOKE ALL ON FUNCTION research.protect_research_snapshot() FROM PUBLIC")
    _secure("agent_runtime", "agent_runs", "SELECT, INSERT, UPDATE")
    _secure("agent_runtime", "agent_budget_usage", "SELECT, INSERT, UPDATE")
    _secure("research", "research_snapshots", "SELECT, INSERT")


def downgrade() -> None:
    count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT (SELECT count(*) FROM agent_runtime.agent_runs) + (SELECT count(*) FROM research.research_snapshots)"
            )
        )
        .scalar_one()
    )
    if count:
        raise RuntimeError("refusing lossy AgentRuntime downgrade while data exists")
    op.execute("DROP FUNCTION research.protect_research_snapshot() CASCADE")
    op.drop_table("research_snapshots", schema="research")
    op.drop_table("agent_budget_usage", schema="agent_runtime")
    op.execute("DROP FUNCTION agent_runtime.protect_agent_run() CASCADE")
    op.drop_table("agent_runs", schema="agent_runtime")
    op.execute("DROP SCHEMA agent_runtime")
