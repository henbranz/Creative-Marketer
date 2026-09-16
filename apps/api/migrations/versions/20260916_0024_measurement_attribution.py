# ruff: noqa: E501
"""Add deterministic performance measurement and attribution facts.

Revision ID: 20260916_0024
Revises: 20260914_0023
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260916_0024"
down_revision: str | None = "20260914_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME = "creative_marketer_runtime"
MIGRATOR = "creative_marketer_migrator"
TENANT = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def _base() -> list[sa.Column[object]]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
    ]


def _secure(table: str, permissions: str = "SELECT, INSERT") -> None:
    op.execute(f"ALTER TABLE measurement.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE measurement.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_migration_control ON measurement.{table} FOR ALL TO {MIGRATOR} USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY {table}_runtime_tenant ON measurement.{table} FOR ALL TO {RUNTIME} USING (tenant_id = {TENANT}) WITH CHECK (tenant_id = {TENANT})"
    )
    op.execute(f"REVOKE ALL ON measurement.{table} FROM PUBLIC, {RUNTIME}")
    op.execute(f"GRANT {permissions} ON measurement.{table} TO {RUNTIME}")


def _immutable(table: str) -> None:
    op.execute(
        f"CREATE FUNCTION measurement.protect_{table}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION '{table} is immutable'; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER protect_{table} BEFORE UPDATE OR DELETE ON measurement.{table} FOR EACH ROW EXECUTE FUNCTION measurement.protect_{table}()"
    )
    op.execute(f"REVOKE ALL ON FUNCTION measurement.protect_{table}() FROM PUBLIC")
    _secure(table)


def _tenant_fk() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT")


def _publication_fk() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "publication_id"],
        ["publishing.publications.tenant_id", "publishing.publications.id"],
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    op.execute("CREATE SCHEMA measurement")
    op.execute(f"GRANT USAGE ON SCHEMA measurement TO {RUNTIME}, {MIGRATOR}")
    op.create_table(
        "performance_observations",
        *_base(),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_key", sa.String(64), nullable=False),
        sa.Column("semantics", sa.String(16), nullable=False),
        sa.Column("value", sa.Numeric(30, 9), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_version", sa.String(128), nullable=False),
        sa.Column("source_digest", sa.String(71), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _tenant_fk(),
        _publication_fk(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["catalog.products.tenant_id", "catalog.products.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_performance_observations_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "source_digest", name="uq_performance_observations_source"
        ),
        sa.CheckConstraint(
            "provider='fake' AND value>=0 AND semantics IN ('CUMULATIVE','INTERVAL','DURATION','RATIO') AND source_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_performance_observations_values",
        ),
        schema="measurement",
    )
    op.create_table(
        "collection_runs",
        *_base(),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("checkpoint", sa.String(64), nullable=False),
        sa.Column("cursor", sa.String(256)),
        sa.Column("failure_code", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        _tenant_fk(),
        _publication_fk(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_collection_runs_tenant_id_id"),
        sa.CheckConstraint(
            "status IN ('RUNNING','SUCCEEDED','FAILED') AND length(btrim(checkpoint))>0",
            name="ck_collection_runs_values",
        ),
        schema="measurement",
    )
    op.create_table(
        "attribution_references",
        *_base(),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_code_hash", sa.String(64), nullable=False),
        sa.Column("destination_url", sa.String(2000), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _tenant_fk(),
        _publication_fk(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_attribution_references_tenant_id_id"),
        sa.UniqueConstraint("public_code_hash", name="uq_attribution_references_public_code_hash"),
        sa.CheckConstraint(
            "public_code_hash ~ '^[0-9a-f]{64}$' AND destination_url ~ '^https?://'",
            name="ck_attribution_references_values",
        ),
        schema="measurement",
    )
    op.create_table(
        "conversion_observations",
        *_base(),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("external_id", sa.String(256), nullable=False),
        sa.Column("amount", sa.Numeric(30, 9), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attribution_code_hash", sa.String(64)),
        sa.Column("semantic_digest", sa.String(71), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _tenant_fk(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_conversion_observations_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "source", "external_id", name="uq_conversion_observations_external"
        ),
        sa.CheckConstraint(
            "source='fake' AND amount>=0 AND currency ~ '^[A-Z]{3}$' AND semantic_digest ~ '^sha256:[0-9a-f]{64}$' AND (attribution_code_hash IS NULL OR attribution_code_hash ~ '^[0-9a-f]{64}$')",
            name="ck_conversion_observations_values",
        ),
        schema="measurement",
    )
    op.create_table(
        "attribution_results",
        *_base(),
        sa.Column("conversion_observation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attribution_reference_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("formula_version", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _tenant_fk(),
        _publication_fk(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversion_observation_id"],
            [
                "measurement.conversion_observations.tenant_id",
                "measurement.conversion_observations.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "attribution_reference_id"],
            [
                "measurement.attribution_references.tenant_id",
                "measurement.attribution_references.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_attribution_results_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "conversion_observation_id", name="uq_attribution_results_conversion"
        ),
        sa.CheckConstraint(
            "method='DIRECT_REFERENCE' AND formula_version='direct-reference-v1'",
            name="ck_attribution_results_values",
        ),
        schema="measurement",
    )
    op.create_table(
        "performance_snapshots",
        *_base(),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "observation_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=False
        ),
        sa.Column("latest_metrics", postgresql.JSONB(), nullable=False),
        sa.Column("derived_metrics", postgresql.JSONB(), nullable=False),
        sa.Column("attributed_conversions", sa.Integer(), nullable=False),
        sa.Column("attributed_revenue", postgresql.JSONB(), nullable=False),
        sa.Column("freshness", sa.String(16), nullable=False),
        sa.Column("semantic_digest", sa.String(71), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _tenant_fk(),
        _publication_fk(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["catalog.products.tenant_id", "catalog.products.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_performance_snapshots_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "publication_id",
            "semantic_digest",
            name="uq_performance_snapshots_semantic",
        ),
        sa.CheckConstraint(
            "attributed_conversions>=0 AND freshness IN ('CURRENT','STALE','NO_DATA') AND semantic_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_performance_snapshots_values",
        ),
        schema="measurement",
    )
    for table in (
        "performance_observations",
        "attribution_references",
        "conversion_observations",
        "attribution_results",
        "performance_snapshots",
    ):
        _immutable(table)
    _secure("collection_runs")


def downgrade() -> None:
    count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT "
                "(SELECT count(*) FROM measurement.performance_observations) + "
                "(SELECT count(*) FROM measurement.collection_runs) + "
                "(SELECT count(*) FROM measurement.attribution_references) + "
                "(SELECT count(*) FROM measurement.conversion_observations) + "
                "(SELECT count(*) FROM measurement.attribution_results) + "
                "(SELECT count(*) FROM measurement.performance_snapshots)"
            )
        )
        .scalar_one()
    )
    if count:
        raise RuntimeError("refusing lossy measurement downgrade while facts exist")
    for table in (
        "performance_snapshots",
        "attribution_results",
        "conversion_observations",
        "attribution_references",
        "performance_observations",
    ):
        op.execute(f"DROP TRIGGER protect_{table} ON measurement.{table}")
        op.execute(f"DROP FUNCTION measurement.protect_{table}()")
    for table in (
        "performance_snapshots",
        "attribution_results",
        "conversion_observations",
        "attribution_references",
        "collection_runs",
        "performance_observations",
    ):
        op.drop_table(table, schema="measurement")
    op.execute("DROP SCHEMA measurement")
