"""Add immutable confirmed media provider-attempt history.

Revision ID: 20261015_0039
Revises: 20261010_0038
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261015_0039"
down_revision: str | None = "20261010_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME = "creative_marketer_runtime"
MIGRATOR = "creative_marketer_migrator"
TENANT = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "generation_provider_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("retry_transition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_operation_ref", sa.String(256), nullable=False),
        sa.Column("failure_code", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("terminal_status", sa.String(16), nullable=False),
        sa.Column("actual_cost", sa.Numeric(20, 6), nullable=False),
        sa.Column("historical_unknown_cost", sa.Numeric(20, 6), nullable=False),
        sa.Column("operator_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("retry_reason", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "generation_job_id"],
            ["production.generation_jobs.tenant_id", "production.generation_jobs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["operator_user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_generation_provider_attempts_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "generation_job_id",
            "retry_transition_id",
            name="uq_generation_provider_attempts_retry_transition",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "generation_job_id",
            "provider_operation_ref",
            name="uq_generation_provider_attempts_operation",
        ),
        sa.CheckConstraint(
            "terminal_status='FAILED' AND actual_cost>=0 AND historical_unknown_cost>=0 "
            "AND retry_reason='CONFIRMED_PROVIDER_FAILURE' "
            "AND failure_code ~ '^[A-Z0-9_]{3,64}$' "
            "AND octet_length(provider_operation_ref) BETWEEN 1 AND 256 "
            "AND octet_length(provider) BETWEEN 1 AND 64 "
            "AND octet_length(model) BETWEEN 1 AND 128",
            name="ck_generation_provider_attempts_values",
        ),
        schema="production",
    )
    op.create_index(
        "ix_generation_provider_attempts_job_created",
        "generation_provider_attempts",
        ["tenant_id", "generation_job_id", "created_at"],
        schema="production",
    )
    op.execute(
        "CREATE FUNCTION production.protect_generation_provider_attempts() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
        "'generation_provider_attempts is immutable'; END; $$"
    )
    op.execute(
        "CREATE TRIGGER protect_generation_provider_attempts BEFORE UPDATE OR DELETE ON "
        "production.generation_provider_attempts FOR EACH ROW EXECUTE FUNCTION "
        "production.protect_generation_provider_attempts()"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION production.protect_generation_provider_attempts() FROM PUBLIC"
    )
    op.execute("ALTER TABLE production.generation_provider_attempts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE production.generation_provider_attempts FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY generation_provider_attempts_migration_control ON "
        f"production.generation_provider_attempts FOR ALL TO {MIGRATOR} "
        f"USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY generation_provider_attempts_runtime_tenant ON "
        f"production.generation_provider_attempts FOR ALL TO {RUNTIME} "
        f"USING (tenant_id = {TENANT}) WITH CHECK (tenant_id = {TENANT})"
    )
    op.execute(f"REVOKE ALL ON production.generation_provider_attempts FROM PUBLIC, {RUNTIME}")
    op.execute(f"GRANT SELECT, INSERT ON production.generation_provider_attempts TO {RUNTIME}")


def downgrade() -> None:
    evidence = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM production.generation_provider_attempts"))
        .scalar_one()
    )
    if evidence:
        raise RuntimeError(
            "refusing lossy downgrade while confirmed media provider-attempt history exists"
        )
    op.execute("DROP FUNCTION production.protect_generation_provider_attempts() CASCADE")
    op.drop_table("generation_provider_attempts", schema="production")
