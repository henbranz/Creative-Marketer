# ruff: noqa: E501
"""Generalize AgentRun provenance and add immutable Creative strategy state.

Revision ID: 20260912_0017
Revises: 20260912_0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260912_0017"
down_revision: str | None = "20260912_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME = "creative_marketer_runtime"
MIGRATOR = "creative_marketer_migrator"
TENANT = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def _secure(table: str, permissions: str) -> None:
    op.execute(f"ALTER TABLE creative.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE creative.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_migration_control ON creative.{table} FOR ALL TO {MIGRATOR} USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY {table}_runtime_tenant ON creative.{table} FOR ALL TO {RUNTIME} USING (tenant_id = {TENANT}) WITH CHECK (tenant_id = {TENANT})"
    )
    op.execute(f"REVOKE ALL ON creative.{table} FROM PUBLIC, {RUNTIME}")
    op.execute(f"GRANT {permissions} ON creative.{table} TO {RUNTIME}")


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS creative")
    op.execute(f"GRANT USAGE ON SCHEMA creative TO {RUNTIME}, {MIGRATOR}")
    for column in (
        sa.Column("agent_type", sa.String(64), nullable=True),
        sa.Column("input_context_kind", sa.String(64), nullable=True),
        sa.Column("input_context_schema_version", sa.Integer(), nullable=True),
        sa.Column("input_context_digest", sa.String(71), nullable=True),
        sa.Column("input_context_refs", postgresql.JSONB(), nullable=True),
    ):
        op.add_column("agent_runs", column, schema="agent_runtime")
    op.execute("""
        UPDATE agent_runtime.agent_runs SET
          agent_type='researcher', input_context_kind='researcher.v1',
          input_context_schema_version=1, input_context_digest=context_digest,
          input_context_refs=jsonb_build_array(
            jsonb_build_object('kind','product_snapshot','id',product_snapshot_id::text,'digest',product_snapshot_digest),
            jsonb_build_object('kind','research_manifest','digest',research_context_digest),
            jsonb_build_object('kind','evidence_blocks','references',selected_evidence)
          )
    """)
    for name in (
        "agent_type",
        "input_context_kind",
        "input_context_schema_version",
        "input_context_digest",
        "input_context_refs",
    ):
        op.alter_column("agent_runs", name, nullable=False, schema="agent_runtime")
    op.create_check_constraint(
        "ck_agent_runs_generic_context",
        "agent_runs",
        "agent_type ~ '^[a-z][a-z0-9_]{0,63}$' AND input_context_kind ~ '^[a-z][a-z0-9_.-]{0,63}$' AND input_context_schema_version > 0 AND input_context_digest ~ '^sha256:[0-9a-f]{64}$' AND jsonb_typeof(input_context_refs)='array' AND jsonb_array_length(input_context_refs) BETWEEN 1 AND 128 AND octet_length(input_context_refs::text) <= 65536",
        schema="agent_runtime",
    )
    op.drop_constraint(
        "ck_agent_runs_selected_evidence",
        "agent_runs",
        schema="agent_runtime",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_runs_selected_evidence",
        "agent_runs",
        "jsonb_typeof(selected_evidence) = 'array' AND "
        "((agent_type = 'researcher' AND jsonb_array_length(selected_evidence) BETWEEN 1 AND 120) "
        "OR (agent_type = 'creative_strategist' AND jsonb_array_length(selected_evidence) = 0))",
        schema="agent_runtime",
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION agent_runtime.protect_agent_run() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF (NEW.tenant_id,NEW.requested_agent_definition_id,NEW.resolved_agent_definition_id,NEW.agent_version_id,NEW.agent_version_number,NEW.agent_configuration_digest,NEW.prompt_revision,NEW.product_id,NEW.product_snapshot_id,NEW.product_snapshot_digest,NEW.product_snapshot_schema_version,NEW.research_context_digest,NEW.context_digest,NEW.selected_evidence,NEW.agent_type,NEW.input_context_kind,NEW.input_context_schema_version,NEW.input_context_digest,NEW.input_context_refs,NEW.model_profile_key,NEW.output_contract_key,NEW.output_contract_version,NEW.correlation_id,NEW.initiated_by_actor_kind,NEW.initiated_by_actor_id,NEW.period_start,NEW.reserved_cost,NEW.currency,NEW.idempotency_key,NEW.recovery_of_run_id,NEW.created_at) IS DISTINCT FROM (OLD.tenant_id,OLD.requested_agent_definition_id,OLD.resolved_agent_definition_id,OLD.agent_version_id,OLD.agent_version_number,OLD.agent_configuration_digest,OLD.prompt_revision,OLD.product_id,OLD.product_snapshot_id,OLD.product_snapshot_digest,OLD.product_snapshot_schema_version,OLD.research_context_digest,OLD.context_digest,OLD.selected_evidence,OLD.agent_type,OLD.input_context_kind,OLD.input_context_schema_version,OLD.input_context_digest,OLD.input_context_refs,OLD.model_profile_key,OLD.output_contract_key,OLD.output_contract_version,OLD.correlation_id,OLD.initiated_by_actor_kind,OLD.initiated_by_actor_id,OLD.period_start,OLD.reserved_cost,OLD.currency,OLD.idempotency_key,OLD.recovery_of_run_id,OLD.created_at) THEN RAISE EXCEPTION 'AgentRun provenance is immutable'; END IF; IF OLD.status <> NEW.status AND NOT ((OLD.status='PENDING' AND NEW.status IN ('RUNNING','CANCELLED','BLOCKED_BUDGET','FAILED')) OR (OLD.status='RUNNING' AND NEW.status IN ('SUCCEEDED','FAILED','CANCELLED'))) THEN RAISE EXCEPTION 'invalid AgentRun transition'; END IF; IF OLD.status IN ('SUCCEEDED','FAILED','CANCELLED','BLOCKED_BUDGET') AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'terminal AgentRun is immutable'; END IF; RETURN NEW; END; $$"
    )

    op.create_table(
        "concept_sets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("product_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_snapshot_digest", sa.String(71), nullable=False),
        sa.Column("research_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("research_snapshot_digest", sa.String(71), nullable=False),
        sa.Column("input_context_digest", sa.String(71), nullable=False),
        sa.Column("semantic_digest", sa.String(71), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["catalog.products.tenant_id", "catalog.products.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_run_id"],
            ["agent_runtime.agent_runs.tenant_id", "agent_runtime.agent_runs.id"],
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
            ["tenant_id", "research_snapshot_id"],
            ["research.research_snapshots.tenant_id", "research.research_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_concept_sets_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "agent_run_id", name="uq_concept_sets_agent_run"),
        sa.CheckConstraint(
            "schema_version=1 AND product_snapshot_digest ~ '^sha256:[0-9a-f]{64}$' AND research_snapshot_digest ~ '^sha256:[0-9a-f]{64}$' AND input_context_digest ~ '^sha256:[0-9a-f]{64}$' AND semantic_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_concept_sets_values",
        ),
        schema="creative",
    )
    op.create_table(
        "concepts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("concept_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("concept_key", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("concept_payload", postgresql.JSONB(), nullable=False),
        sa.Column("semantic_digest", sa.String(71), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "concept_set_id"],
            ["creative.concept_sets.tenant_id", "creative.concept_sets.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["catalog.products.tenant_id", "catalog.products.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_concepts_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "concept_set_id", "concept_key", name="uq_concepts_set_key"
        ),
        sa.UniqueConstraint(
            "tenant_id", "concept_set_id", "ordinal", name="uq_concepts_set_ordinal"
        ),
        sa.CheckConstraint(
            "ordinal BETWEEN 1 AND 5 AND concept_key ~ '^[a-z][a-z0-9_-]{0,63}$' AND jsonb_typeof(concept_payload)='object' AND octet_length(concept_payload::text)<=65536 AND semantic_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_concepts_values",
        ),
        schema="creative",
    )
    op.create_table(
        "concept_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("concept_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason_code", sa.String(64)),
        sa.Column("note", sa.String(1000)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "concept_id"],
            ["creative.concepts.tenant_id", "creative.concepts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["catalog.products.tenant_id", "catalog.products.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["decided_by"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_concept_decisions_tenant_id_id"),
        sa.CheckConstraint(
            "state IN ('SHORTLISTED','APPROVED_FOR_PRODUCTION','REJECTED')",
            name="ck_concept_decisions_state",
        ),
        schema="creative",
    )
    for table in ("concept_sets", "concepts", "concept_decisions"):
        op.execute(
            f"CREATE FUNCTION creative.protect_{table}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION '{table} is immutable'; END; $$"
        )
        op.execute(
            f"CREATE TRIGGER protect_{table} BEFORE UPDATE OR DELETE ON creative.{table} FOR EACH ROW EXECUTE FUNCTION creative.protect_{table}()"
        )
        op.execute(f"REVOKE ALL ON FUNCTION creative.protect_{table}() FROM PUBLIC")
    _secure("concept_sets", "SELECT, INSERT")
    _secure("concepts", "SELECT, INSERT")
    _secure("concept_decisions", "SELECT, INSERT")


def downgrade() -> None:
    evidence = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT (SELECT count(*) FROM creative.concept_sets)+(SELECT count(*) FROM creative.concepts)+(SELECT count(*) FROM creative.concept_decisions)+(SELECT count(*) FROM agent_runtime.agent_runs WHERE agent_type <> 'researcher')"
            )
        )
        .scalar_one()
    )
    if evidence:
        raise RuntimeError("refusing lossy Creative Strategist downgrade while provenance exists")
    for table in ("concept_decisions", "concepts", "concept_sets"):
        op.execute(f"DROP FUNCTION creative.protect_{table}() CASCADE")
        op.drop_table(table, schema="creative")
    op.drop_constraint(
        "ck_agent_runs_selected_evidence",
        "agent_runs",
        schema="agent_runtime",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_runs_selected_evidence",
        "agent_runs",
        "jsonb_typeof(selected_evidence) = 'array' AND "
        "jsonb_array_length(selected_evidence) BETWEEN 1 AND 120",
        schema="agent_runtime",
    )
    op.drop_constraint(
        "ck_agent_runs_generic_context", "agent_runs", schema="agent_runtime", type_="check"
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION agent_runtime.protect_agent_run() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        IF (NEW.tenant_id,NEW.requested_agent_definition_id,NEW.resolved_agent_definition_id,
        NEW.agent_version_id,NEW.agent_version_number,NEW.agent_configuration_digest,
        NEW.prompt_revision,NEW.product_id,NEW.product_snapshot_id,NEW.product_snapshot_digest,
        NEW.product_snapshot_schema_version,NEW.research_context_digest,NEW.context_digest,
        NEW.selected_evidence,NEW.model_profile_key,NEW.output_contract_key,
        NEW.output_contract_version,NEW.correlation_id,NEW.initiated_by_actor_kind,
        NEW.initiated_by_actor_id,NEW.period_start,NEW.reserved_cost,NEW.currency,
        NEW.idempotency_key,NEW.recovery_of_run_id,NEW.created_at) IS DISTINCT FROM
        (OLD.tenant_id,OLD.requested_agent_definition_id,OLD.resolved_agent_definition_id,
        OLD.agent_version_id,OLD.agent_version_number,OLD.agent_configuration_digest,
        OLD.prompt_revision,OLD.product_id,OLD.product_snapshot_id,OLD.product_snapshot_digest,
        OLD.product_snapshot_schema_version,OLD.research_context_digest,OLD.context_digest,
        OLD.selected_evidence,OLD.model_profile_key,OLD.output_contract_key,
        OLD.output_contract_version,OLD.correlation_id,OLD.initiated_by_actor_kind,
        OLD.initiated_by_actor_id,OLD.period_start,OLD.reserved_cost,OLD.currency,
        OLD.idempotency_key,OLD.recovery_of_run_id,OLD.created_at)
        THEN RAISE EXCEPTION 'AgentRun provenance is immutable'; END IF;
        IF OLD.status <> NEW.status AND NOT
        ((OLD.status='PENDING' AND NEW.status IN ('RUNNING','CANCELLED','BLOCKED_BUDGET','FAILED'))
        OR (OLD.status='RUNNING' AND NEW.status IN ('SUCCEEDED','FAILED','CANCELLED')))
        THEN RAISE EXCEPTION 'invalid AgentRun transition'; END IF;
        IF OLD.status IN ('SUCCEEDED','FAILED','CANCELLED','BLOCKED_BUDGET')
        AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'terminal AgentRun is immutable'; END IF;
        RETURN NEW; END; $$"""
    )
    for name in (
        "input_context_refs",
        "input_context_digest",
        "input_context_schema_version",
        "input_context_kind",
        "agent_type",
    ):
        op.drop_column("agent_runs", name, schema="agent_runtime")
    op.execute("DROP SCHEMA creative")
