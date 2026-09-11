# ruff: noqa: E501
"""Add durable model attempts and explicit AgentRun recovery accounting.

Revision ID: 20260912_0016
Revises: 20260911_0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260912_0016"
down_revision: str | None = "20260911_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME = "creative_marketer_runtime"
MIGRATOR = "creative_marketer_migrator"
TENANT = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def _secure(table: str, permissions: str) -> None:
    op.execute(f"ALTER TABLE agent_runtime.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE agent_runtime.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_migration_control ON agent_runtime.{table} FOR ALL TO {MIGRATOR} USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY {table}_runtime_tenant ON agent_runtime.{table} FOR ALL TO {RUNTIME} USING (tenant_id = {TENANT}) WITH CHECK (tenant_id = {TENANT})"
    )
    op.execute(f"REVOKE ALL ON agent_runtime.{table} FROM PUBLIC, {RUNTIME}")
    op.execute(f"GRANT {permissions} ON agent_runtime.{table} TO {RUNTIME}")


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("recovery_of_run_id", postgresql.UUID(as_uuid=True)),
        schema="agent_runtime",
    )
    op.create_foreign_key(
        "fk_agent_runs_recovery_predecessor",
        "agent_runs",
        "agent_runs",
        ["tenant_id", "recovery_of_run_id"],
        ["tenant_id", "id"],
        source_schema="agent_runtime",
        referent_schema="agent_runtime",
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_agent_runs_recovery_not_self",
        "agent_runs",
        "recovery_of_run_id IS NULL OR recovery_of_run_id <> id",
        schema="agent_runtime",
    )
    op.create_index(
        "uq_agent_runs_one_recovery_successor",
        "agent_runs",
        ["tenant_id", "recovery_of_run_id"],
        unique=True,
        postgresql_where=sa.text("recovery_of_run_id IS NOT NULL"),
        schema="agent_runtime",
    )
    op.add_column(
        "agent_budget_usage",
        sa.Column("unknown_cost", sa.Numeric(19, 6), nullable=False, server_default="0"),
        schema="agent_runtime",
    )
    op.drop_constraint(
        "ck_agent_budget_usage_nonnegative",
        "agent_budget_usage",
        schema="agent_runtime",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_budget_usage_nonnegative",
        "agent_budget_usage",
        "reserved_runs >= 0 AND reserved_cost >= 0 AND actual_cost >= 0 AND unknown_cost >= 0",
        schema="agent_runtime",
    )

    op.create_table(
        "model_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("workload_id", sa.String(128), nullable=False),
        sa.Column("model_route_version", sa.String(64), nullable=False),
        sa.Column("pricing_version", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_started_at", sa.DateTime(timezone=True)),
        sa.Column("response_recorded_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_response_id", sa.String(256)),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("estimated_cost", sa.Numeric(19, 6), nullable=False, server_default="0"),
        sa.Column("unknown_cost", sa.Numeric(19, 6), nullable=False, server_default="0"),
        sa.Column("failure_code", sa.String(100)),
        sa.ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_run_id"],
            ["agent_runtime.agent_runs.tenant_id", "agent_runtime.agent_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_model_attempts_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "agent_run_id",
            "attempt_number",
            name="uq_model_attempts_run_number",
        ),
        sa.CheckConstraint("attempt_number > 0", name="ck_model_attempts_number"),
        sa.CheckConstraint(
            "status IN ('CLAIMED','PROVIDER_STARTED','RESPONSE_RECORDED','SUCCEEDED','FAILED_NO_RESPONSE','UNKNOWN')",
            name="ck_model_attempts_status",
        ),
        sa.CheckConstraint(
            "lease_expires_at > claimed_at AND input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= input_tokens + output_tokens AND estimated_cost >= 0 AND unknown_cost >= 0",
            name="ck_model_attempts_bounds",
        ),
        sa.CheckConstraint(
            "(status = 'CLAIMED' AND provider_started_at IS NULL AND response_recorded_at IS NULL AND finished_at IS NULL AND provider_response_id IS NULL AND input_tokens = 0 AND output_tokens = 0 AND total_tokens = 0 AND estimated_cost = 0 AND unknown_cost = 0 AND failure_code IS NULL) OR "
            "(status = 'PROVIDER_STARTED' AND provider_started_at IS NOT NULL AND response_recorded_at IS NULL AND finished_at IS NULL AND provider_response_id IS NULL AND input_tokens = 0 AND output_tokens = 0 AND total_tokens = 0 AND estimated_cost = 0 AND unknown_cost = 0 AND failure_code IS NULL) OR "
            "(status = 'RESPONSE_RECORDED' AND provider_started_at IS NOT NULL AND response_recorded_at IS NOT NULL AND finished_at IS NULL AND unknown_cost = 0 AND failure_code IS NULL) OR "
            "(status = 'SUCCEEDED' AND provider_started_at IS NOT NULL AND response_recorded_at IS NOT NULL AND finished_at IS NOT NULL) OR "
            "(status = 'FAILED_NO_RESPONSE' AND provider_started_at IS NULL AND response_recorded_at IS NULL AND finished_at IS NOT NULL AND provider_response_id IS NULL AND input_tokens = 0 AND output_tokens = 0 AND total_tokens = 0 AND estimated_cost = 0 AND unknown_cost = 0) OR "
            "(status = 'UNKNOWN' AND provider_started_at IS NOT NULL AND response_recorded_at IS NULL AND finished_at IS NOT NULL AND provider_response_id IS NULL AND input_tokens = 0 AND output_tokens = 0 AND total_tokens = 0 AND estimated_cost = 0 AND unknown_cost > 0)",
            name="ck_model_attempts_lifecycle",
        ),
        schema="agent_runtime",
    )
    op.create_index(
        "ix_model_attempts_stranded",
        "model_attempts",
        ["tenant_id", "lease_expires_at", "status"],
        schema="agent_runtime",
    )
    op.create_index(
        "uq_model_attempts_one_active",
        "model_attempts",
        ["tenant_id", "agent_run_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('CLAIMED','PROVIDER_STARTED','RESPONSE_RECORDED','UNKNOWN')"
        ),
        schema="agent_runtime",
    )
    op.create_table(
        "model_cost_reconciliations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("unknown_cost", sa.Numeric(19, 6), nullable=False),
        sa.Column("actual_cost", sa.Numeric(19, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("reconciled_by_workload_id", sa.String(128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_run_id"],
            ["agent_runtime.agent_runs.tenant_id", "agent_runtime.agent_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "model_attempt_id"],
            ["agent_runtime.model_attempts.tenant_id", "agent_runtime.model_attempts.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "agent_run_id", name="uq_model_cost_reconciliations_run"),
        sa.CheckConstraint(
            "unknown_cost > 0 AND actual_cost >= 0 AND currency ~ '^[A-Z]{3}$'",
            name="ck_model_cost_reconciliations_values",
        ),
        schema="agent_runtime",
    )

    # Deploy with workers stopped. Pre-migration RUNNING rows cannot prove whether provider I/O
    # started, so backfill one conservative UNKNOWN attempt rather than making them reclaimable.
    op.execute(
        "INSERT INTO agent_runtime.model_attempts (id,tenant_id,agent_run_id,attempt_number,workload_id,model_route_version,pricing_version,provider,model,status,claimed_at,provider_started_at,finished_at,lease_expires_at,input_tokens,output_tokens,total_tokens,estimated_cost,unknown_cost,failure_code) SELECT gen_random_uuid(),tenant_id,id,1,executed_by_workload_id,resolved_model_route_version,pricing_version,resolved_provider,resolved_model,'UNKNOWN',started_at,started_at,now(),started_at + interval '15 minutes',0,0,0,0,reserved_cost,'LEGACY_RUNNING_ATTEMPT' FROM agent_runtime.agent_runs WHERE status='RUNNING' AND started_at IS NOT NULL AND executed_by_workload_id IS NOT NULL AND resolved_model_route_version IS NOT NULL AND pricing_version IS NOT NULL AND resolved_provider IS NOT NULL AND resolved_model IS NOT NULL"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM agent_runtime.agent_runs run WHERE run.status='RUNNING' AND NOT EXISTS (SELECT 1 FROM agent_runtime.model_attempts attempt WHERE attempt.tenant_id=run.tenant_id AND attempt.agent_run_id=run.id)) THEN RAISE EXCEPTION 'cannot safely migrate incomplete legacy RUNNING AgentRun'; END IF; END $$"
    )

    op.execute(
        "CREATE OR REPLACE FUNCTION agent_runtime.protect_agent_run() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF (NEW.tenant_id,NEW.requested_agent_definition_id,NEW.resolved_agent_definition_id,NEW.agent_version_id,NEW.agent_version_number,NEW.agent_configuration_digest,NEW.prompt_revision,NEW.product_id,NEW.product_snapshot_id,NEW.product_snapshot_digest,NEW.product_snapshot_schema_version,NEW.research_context_digest,NEW.context_digest,NEW.selected_evidence,NEW.model_profile_key,NEW.output_contract_key,NEW.output_contract_version,NEW.correlation_id,NEW.initiated_by_actor_kind,NEW.initiated_by_actor_id,NEW.period_start,NEW.reserved_cost,NEW.currency,NEW.idempotency_key,NEW.recovery_of_run_id,NEW.created_at) IS DISTINCT FROM (OLD.tenant_id,OLD.requested_agent_definition_id,OLD.resolved_agent_definition_id,OLD.agent_version_id,OLD.agent_version_number,OLD.agent_configuration_digest,OLD.prompt_revision,OLD.product_id,OLD.product_snapshot_id,OLD.product_snapshot_digest,OLD.product_snapshot_schema_version,OLD.research_context_digest,OLD.context_digest,OLD.selected_evidence,OLD.model_profile_key,OLD.output_contract_key,OLD.output_contract_version,OLD.correlation_id,OLD.initiated_by_actor_kind,OLD.initiated_by_actor_id,OLD.period_start,OLD.reserved_cost,OLD.currency,OLD.idempotency_key,OLD.recovery_of_run_id,OLD.created_at) THEN RAISE EXCEPTION 'AgentRun provenance is immutable'; END IF; IF OLD.status <> NEW.status AND NOT ((OLD.status='PENDING' AND NEW.status IN ('RUNNING','CANCELLED','BLOCKED_BUDGET','FAILED')) OR (OLD.status='RUNNING' AND NEW.status IN ('SUCCEEDED','FAILED','CANCELLED'))) THEN RAISE EXCEPTION 'invalid AgentRun transition'; END IF; IF OLD.status IN ('SUCCEEDED','FAILED','CANCELLED','BLOCKED_BUDGET') AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'terminal AgentRun is immutable'; END IF; RETURN NEW; END; $$"
    )
    op.execute(
        "CREATE FUNCTION agent_runtime.validate_agent_run_recovery() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.recovery_of_run_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM agent_runtime.agent_runs predecessor WHERE predecessor.tenant_id=NEW.tenant_id AND predecessor.id=NEW.recovery_of_run_id AND predecessor.status='FAILED' AND predecessor.failure_code IN ('STRANDED_BEFORE_PROVIDER','STRANDED_PROVIDER_OUTCOME_UNKNOWN','STRANDED_RESPONSE_RECORDED')) THEN RAISE EXCEPTION 'recovery predecessor is not terminal and qualified'; END IF; RETURN NEW; END; $$"
    )
    op.execute(
        "CREATE TRIGGER validate_agent_run_recovery BEFORE INSERT ON agent_runtime.agent_runs FOR EACH ROW EXECUTE FUNCTION agent_runtime.validate_agent_run_recovery()"
    )
    op.execute("REVOKE ALL ON FUNCTION agent_runtime.validate_agent_run_recovery() FROM PUBLIC")
    op.execute(
        "CREATE FUNCTION agent_runtime.protect_model_attempt() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF (NEW.tenant_id,NEW.agent_run_id,NEW.attempt_number,NEW.workload_id,NEW.model_route_version,NEW.pricing_version,NEW.provider,NEW.model,NEW.claimed_at,NEW.lease_expires_at) IS DISTINCT FROM (OLD.tenant_id,OLD.agent_run_id,OLD.attempt_number,OLD.workload_id,OLD.model_route_version,OLD.pricing_version,OLD.provider,OLD.model,OLD.claimed_at,OLD.lease_expires_at) THEN RAISE EXCEPTION 'ModelAttempt identity is immutable'; END IF; IF OLD.status = NEW.status AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'ModelAttempt update requires a lifecycle transition'; END IF; IF OLD.status <> NEW.status AND NOT ((OLD.status='CLAIMED' AND NEW.status IN ('PROVIDER_STARTED','FAILED_NO_RESPONSE')) OR (OLD.status='PROVIDER_STARTED' AND NEW.status IN ('RESPONSE_RECORDED','UNKNOWN')) OR (OLD.status='RESPONSE_RECORDED' AND NEW.status='SUCCEEDED')) THEN RAISE EXCEPTION 'invalid ModelAttempt transition'; END IF; IF OLD.status IN ('SUCCEEDED','FAILED_NO_RESPONSE','UNKNOWN') AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'terminal ModelAttempt is immutable'; END IF; RETURN NEW; END; $$"
    )
    op.execute(
        "CREATE TRIGGER protect_model_attempt BEFORE UPDATE ON agent_runtime.model_attempts FOR EACH ROW EXECUTE FUNCTION agent_runtime.protect_model_attempt()"
    )
    op.execute("REVOKE ALL ON FUNCTION agent_runtime.protect_model_attempt() FROM PUBLIC")
    op.execute(
        "CREATE FUNCTION agent_runtime.protect_model_cost_reconciliation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Model cost reconciliation is immutable'; END; $$"
    )
    op.execute(
        "CREATE TRIGGER protect_model_cost_reconciliation BEFORE UPDATE OR DELETE ON agent_runtime.model_cost_reconciliations FOR EACH ROW EXECUTE FUNCTION agent_runtime.protect_model_cost_reconciliation()"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION agent_runtime.protect_model_cost_reconciliation() FROM PUBLIC"
    )
    _secure("model_attempts", "SELECT, INSERT, UPDATE")
    _secure("model_cost_reconciliations", "SELECT, INSERT")


def downgrade() -> None:
    evidence = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT (SELECT count(*) FROM agent_runtime.model_attempts) + "
                "(SELECT count(*) FROM agent_runtime.model_cost_reconciliations) + "
                "(SELECT count(*) FROM agent_runtime.agent_runs WHERE recovery_of_run_id IS NOT NULL) + "
                "(SELECT count(*) FROM agent_runtime.agent_budget_usage WHERE unknown_cost <> 0)"
            )
        )
        .scalar_one()
    )
    if evidence:
        raise RuntimeError("refusing lossy AgentRun recovery downgrade while evidence exists")
    op.execute("DROP FUNCTION agent_runtime.protect_model_cost_reconciliation() CASCADE")
    op.drop_table("model_cost_reconciliations", schema="agent_runtime")
    op.execute("DROP FUNCTION agent_runtime.protect_model_attempt() CASCADE")
    op.drop_table("model_attempts", schema="agent_runtime")
    op.execute("DROP FUNCTION agent_runtime.validate_agent_run_recovery() CASCADE")
    op.drop_constraint(
        "ck_agent_budget_usage_nonnegative",
        "agent_budget_usage",
        schema="agent_runtime",
        type_="check",
    )
    op.drop_column("agent_budget_usage", "unknown_cost", schema="agent_runtime")
    op.create_check_constraint(
        "ck_agent_budget_usage_nonnegative",
        "agent_budget_usage",
        "reserved_runs >= 0 AND reserved_cost >= 0 AND actual_cost >= 0",
        schema="agent_runtime",
    )
    op.drop_index(
        "uq_agent_runs_one_recovery_successor",
        table_name="agent_runs",
        schema="agent_runtime",
    )
    op.drop_constraint(
        "ck_agent_runs_recovery_not_self", "agent_runs", schema="agent_runtime", type_="check"
    )
    op.drop_constraint(
        "fk_agent_runs_recovery_predecessor",
        "agent_runs",
        schema="agent_runtime",
        type_="foreignkey",
    )
    op.drop_column("agent_runs", "recovery_of_run_id", schema="agent_runtime")
    op.execute(
        "CREATE OR REPLACE FUNCTION agent_runtime.protect_agent_run() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF (NEW.tenant_id,NEW.requested_agent_definition_id,NEW.resolved_agent_definition_id,NEW.agent_version_id,NEW.agent_version_number,NEW.agent_configuration_digest,NEW.prompt_revision,NEW.product_id,NEW.product_snapshot_id,NEW.product_snapshot_digest,NEW.product_snapshot_schema_version,NEW.research_context_digest,NEW.context_digest,NEW.selected_evidence,NEW.model_profile_key,NEW.output_contract_key,NEW.output_contract_version,NEW.correlation_id,NEW.initiated_by_actor_kind,NEW.initiated_by_actor_id,NEW.period_start,NEW.reserved_cost,NEW.currency,NEW.idempotency_key,NEW.created_at) IS DISTINCT FROM (OLD.tenant_id,OLD.requested_agent_definition_id,OLD.resolved_agent_definition_id,OLD.agent_version_id,OLD.agent_version_number,OLD.agent_configuration_digest,OLD.prompt_revision,OLD.product_id,OLD.product_snapshot_id,OLD.product_snapshot_digest,OLD.product_snapshot_schema_version,OLD.research_context_digest,OLD.context_digest,OLD.selected_evidence,OLD.model_profile_key,OLD.output_contract_key,OLD.output_contract_version,OLD.correlation_id,OLD.initiated_by_actor_kind,OLD.initiated_by_actor_id,OLD.period_start,OLD.reserved_cost,OLD.currency,OLD.idempotency_key,OLD.created_at) THEN RAISE EXCEPTION 'AgentRun provenance is immutable'; END IF; IF OLD.status <> NEW.status AND NOT ((OLD.status='PENDING' AND NEW.status IN ('RUNNING','CANCELLED','BLOCKED_BUDGET','FAILED')) OR (OLD.status='RUNNING' AND NEW.status IN ('SUCCEEDED','FAILED','CANCELLED'))) THEN RAISE EXCEPTION 'invalid AgentRun transition'; END IF; IF OLD.status IN ('SUCCEEDED','FAILED','CANCELLED','BLOCKED_BUDGET') AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'terminal AgentRun is immutable'; END IF; RETURN NEW; END; $$"
    )
