"""Add immutable CreativeConcept revalidation authority.

Revision ID: 20261009_0036
Revises: 20261002_0035
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261009_0036"
down_revision: str | None = "20261002_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME = "creative_marketer_runtime"
MIGRATOR = "creative_marketer_migrator"
TENANT = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "concept_revalidations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("concept_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("concept_digest", sa.String(71), nullable=False),
        sa.Column("original_concept_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_research_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_research_snapshot_digest", sa.String(71), nullable=False),
        sa.Column("current_research_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_research_snapshot_digest", sa.String(71), nullable=False),
        sa.Column("product_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_snapshot_digest", sa.String(71), nullable=False),
        sa.Column("original_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result", sa.String(32), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("referenced_finding_assertions", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
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
            ["tenant_id", "concept_id"],
            ["creative.concepts.tenant_id", "creative.concepts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "original_concept_set_id"],
            ["creative.concept_sets.tenant_id", "creative.concept_sets.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "original_research_snapshot_id"],
            ["research.research_snapshots.tenant_id", "research.research_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "current_research_snapshot_id"],
            ["research.research_snapshots.tenant_id", "research.research_snapshots.id"],
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
            ["tenant_id", "original_decision_id"],
            ["creative.concept_decisions.tenant_id", "creative.concept_decisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_concept_revalidations_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "concept_id",
            "current_research_snapshot_id",
            "original_decision_id",
            name="uq_concept_revalidations_authority",
        ),
        sa.CheckConstraint(
            "result IN ('REVALIDATED_FOR_PRODUCTION','REQUIRES_RESTRATEGY')",
            name="ck_concept_revalidations_result",
        ),
        sa.CheckConstraint(
            "concept_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "original_research_snapshot_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "current_research_snapshot_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "product_snapshot_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "semantic_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_concept_revalidations_digests",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reason_codes)='array' AND jsonb_array_length(reason_codes)<=8 AND "
            "jsonb_typeof(referenced_finding_assertions)='array' AND "
            "jsonb_array_length(referenced_finding_assertions)<=10 AND "
            "octet_length(referenced_finding_assertions::text)<=8192 AND "
            "((result='REVALIDATED_FOR_PRODUCTION' AND jsonb_array_length(reason_codes)=0) OR "
            "(result='REQUIRES_RESTRATEGY' AND jsonb_array_length(reason_codes)>0))",
            name="ck_concept_revalidations_evidence",
        ),
        schema="creative",
    )
    op.create_index(
        "ix_concept_revalidations_concept_created",
        "concept_revalidations",
        ["tenant_id", "concept_id", "created_at"],
        schema="creative",
    )
    op.execute(
        "CREATE FUNCTION creative.protect_concept_revalidations() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'concept_revalidations is immutable'; "
        "END; $$"
    )
    op.execute(
        "CREATE TRIGGER protect_concept_revalidations BEFORE UPDATE OR DELETE ON "
        "creative.concept_revalidations FOR EACH ROW EXECUTE FUNCTION "
        "creative.protect_concept_revalidations()"
    )
    op.execute("REVOKE ALL ON FUNCTION creative.protect_concept_revalidations() FROM PUBLIC")
    op.execute("ALTER TABLE creative.concept_revalidations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE creative.concept_revalidations FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY concept_revalidations_migration_control ON "
        f"creative.concept_revalidations FOR ALL TO {MIGRATOR} USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY concept_revalidations_runtime_tenant ON "
        f"creative.concept_revalidations FOR ALL TO {RUNTIME} "
        f"USING (tenant_id = {TENANT}) WITH CHECK (tenant_id = {TENANT})"
    )
    op.execute(f"REVOKE ALL ON creative.concept_revalidations FROM PUBLIC, {RUNTIME}")
    op.execute(f"GRANT SELECT, INSERT ON creative.concept_revalidations TO {RUNTIME}")


def downgrade() -> None:
    evidence = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM creative.concept_revalidations"))
        .scalar_one()
    )
    if evidence:
        raise RuntimeError(
            "refusing lossy Creative revalidation downgrade while authority records exist"
        )
    op.execute("DROP FUNCTION creative.protect_concept_revalidations() CASCADE")
    op.drop_table("concept_revalidations", schema="creative")
