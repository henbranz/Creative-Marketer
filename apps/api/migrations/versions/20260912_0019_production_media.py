# ruff: noqa: E501
"""Add tenant-isolated Production planning, media jobs, spend, and Asset lineage.

Revision ID: 20260912_0019
Revises: 20260912_0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260912_0019"
down_revision: str | None = "20260912_0018"
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


def _identity_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
    ]


def upgrade() -> None:
    op.execute("CREATE SCHEMA production")
    op.execute(f"GRANT USAGE ON SCHEMA production TO {RUNTIME}, {MIGRATOR}")
    op.drop_constraint("ck_assets_role", "assets", schema="catalog", type_="check")
    op.create_check_constraint(
        "ck_assets_role",
        "assets",
        "role IN ('product_hero','product_detail','lifestyle','logo','brand_guideline','packaging','other','brand_reference','production_reference','generated_shot','generated_frame')",
        schema="catalog",
    )
    op.drop_constraint("ck_assets_origin", "assets", schema="catalog", type_="check")
    op.create_check_constraint(
        "ck_assets_origin", "assets", "origin IN ('user_upload','generated')", schema="catalog"
    )

    op.create_table(
        "production_plans",
        *_identity_columns(),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("concept_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("concept_digest", sa.String(71), nullable=False),
        sa.Column("concept_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("concept_set_digest", sa.String(71), nullable=False),
        sa.Column("product_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_snapshot_digest", sa.String(71), nullable=False),
        sa.Column("research_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("research_snapshot_digest", sa.String(71), nullable=False),
        sa.Column("creative_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("context_digest", sa.String(71), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("strategy", sa.String(3000), nullable=False),
        sa.Column("required_assets", postgresql.JSONB(), nullable=False),
        sa.Column("semantic_digest", sa.String(71), nullable=False),
        sa.Column("estimated_max_video_cost", sa.Numeric(20, 6), nullable=False),
        sa.Column("estimated_max_image_cost", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("video_pricing_version", sa.String(128), nullable=False),
        sa.Column("image_pricing_version", sa.String(128), nullable=False),
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
            ["tenant_id", "concept_id"],
            ["creative.concepts.tenant_id", "creative.concepts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "concept_set_id"],
            ["creative.concept_sets.tenant_id", "creative.concept_sets.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_production_plans_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "agent_run_id", name="uq_production_plans_agent_run"),
        sa.CheckConstraint(
            "schema_version=1 AND concept_digest ~ '^sha256:[0-9a-f]{64}$' AND concept_set_digest ~ '^sha256:[0-9a-f]{64}$' AND product_snapshot_digest ~ '^sha256:[0-9a-f]{64}$' AND research_snapshot_digest ~ '^sha256:[0-9a-f]{64}$' AND context_digest ~ '^sha256:[0-9a-f]{64}$' AND semantic_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_production_plans_digests",
        ),
        sa.CheckConstraint(
            "estimated_max_video_cost>=0 AND estimated_max_image_cost>=0 AND currency ~ '^[A-Z]{3}$' AND jsonb_typeof(required_assets)='array'",
            name="ck_production_plans_cost",
        ),
        schema="production",
    )
    op.create_table(
        "production_scenes",
        *_identity_columns(),
        sa.Column("production_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_key", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "production_plan_id"],
            ["production.production_plans.tenant_id", "production.production_plans.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_production_scenes_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "production_plan_id", "scene_key", name="uq_production_scenes_key"
        ),
        sa.CheckConstraint(
            "ordinal>0 AND duration_seconds>0 AND scene_key ~ '^[a-z][a-z0-9_-]{0,63}$' AND jsonb_typeof(content)='object'",
            name="ck_production_scenes_values",
        ),
        schema="production",
    )
    op.create_table(
        "production_shots",
        *_identity_columns(),
        sa.Column("production_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("production_scene_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shot_key", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_strategy", sa.String(32), nullable=False),
        sa.Column("specification", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "production_plan_id"],
            ["production.production_plans.tenant_id", "production.production_plans.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "production_scene_id"],
            ["production.production_scenes.tenant_id", "production.production_scenes.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_production_shots_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "production_plan_id", "shot_key", name="uq_production_shots_key"
        ),
        sa.CheckConstraint(
            "ordinal>0 AND shot_key ~ '^[a-z][a-z0-9_-]{0,63}$' AND source_strategy IN ('USE_EXISTING_ASSET','GENERATE_IMAGE','GENERATE_VIDEO','MANUAL_CAPTURE') AND jsonb_typeof(specification)='object'",
            name="ck_production_shots_values",
        ),
        schema="production",
    )
    op.create_table(
        "generation_segments",
        *_identity_columns(),
        sa.Column("production_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("segment_key", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("media_kind", sa.String(8), nullable=False),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("shot_keys", postgresql.JSONB(), nullable=False),
        sa.Column("continuity", postgresql.JSONB(), nullable=False),
        sa.Column("reference_assets", postgresql.JSONB(), nullable=False),
        sa.Column("generation_spec", postgresql.JSONB(), nullable=False),
        sa.Column("generation_spec_digest", sa.String(71), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "production_plan_id"],
            ["production.production_plans.tenant_id", "production.production_plans.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_generation_segments_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "production_plan_id", "segment_key", name="uq_generation_segments_key"
        ),
        sa.CheckConstraint(
            "ordinal>0 AND segment_key ~ '^[a-z][a-z0-9_-]{0,63}$' AND media_kind IN ('IMAGE','VIDEO') AND ((media_kind='IMAGE' AND duration_seconds IS NULL) OR (media_kind='VIDEO' AND duration_seconds BETWEEN 4 AND 30)) AND generation_spec_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_generation_segments_values",
        ),
        schema="production",
    )
    op.create_table(
        "plan_decisions",
        *_identity_columns(),
        sa.Column("production_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_digest", sa.String(71), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_route_version", sa.String(128), nullable=False),
        sa.Column("image_route_version", sa.String(128), nullable=False),
        sa.Column("video_pricing_version", sa.String(128), nullable=False),
        sa.Column("image_pricing_version", sa.String(128), nullable=False),
        sa.Column("estimated_max_cost", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "production_plan_id"],
            ["production.production_plans.tenant_id", "production.production_plans.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["decided_by"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_plan_decisions_tenant_id_id"),
        sa.CheckConstraint(
            "state IN ('APPROVED_FOR_GENERATION','REJECTED') AND plan_digest ~ '^sha256:[0-9a-f]{64}$' AND estimated_max_cost>=0 AND currency ~ '^[A-Z]{3}$'",
            name="ck_plan_decisions_values",
        ),
        schema="production",
    )
    op.create_table(
        "generation_jobs",
        *_identity_columns(),
        sa.Column("production_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation_segment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("media_profile", sa.String(64), nullable=False),
        sa.Column("route_version", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("pricing_version", sa.String(128), nullable=False),
        sa.Column("generation_spec_digest", sa.String(71), nullable=False),
        sa.Column("input_assets", postgresql.JSONB(), nullable=False),
        sa.Column("provider_operation_ref", sa.String(256)),
        sa.Column("reserved_cost", sa.Numeric(20, 6), nullable=False),
        sa.Column("actual_cost", sa.Numeric(20, 6), nullable=False, server_default="0"),
        sa.Column("unknown_cost", sa.Numeric(20, 6), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("output_asset_id", postgresql.UUID(as_uuid=True)),
        sa.Column("failure_code", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "production_plan_id"],
            ["production.production_plans.tenant_id", "production.production_plans.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "generation_segment_id"],
            ["production.generation_segments.tenant_id", "production.generation_segments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "output_asset_id"],
            ["catalog.assets.tenant_id", "catalog.assets.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_generation_jobs_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "production_plan_id",
            "generation_segment_id",
            name="uq_generation_jobs_segment",
        ),
        sa.CheckConstraint(
            "kind IN ('IMAGE','VIDEO') AND status IN ('PENDING_APPROVAL','READY','STARTING','PROCESSING','IMPORTING','SUCCEEDED','FAILED','OUTCOME_UNKNOWN') AND generation_spec_digest ~ '^sha256:[0-9a-f]{64}$' AND reserved_cost>=0 AND actual_cost>=0 AND unknown_cost>=0 AND currency ~ '^[A-Z]{3}$'",
            name="ck_generation_jobs_values",
        ),
        schema="production",
    )
    op.create_table(
        "media_budget_usage",
        *_identity_columns(),
        sa.Column("generation_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_kind", sa.String(16), nullable=False),
        sa.Column("amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "generation_job_id"],
            ["production.generation_jobs.tenant_id", "production.generation_jobs.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_media_budget_usage_tenant_id_id"),
        sa.CheckConstraint(
            "entry_kind IN ('RESERVED','ACTUAL','UNKNOWN','RELEASED') AND amount>=0 AND currency ~ '^[A-Z]{3}$'",
            name="ck_media_budget_usage_values",
        ),
        schema="production",
    )
    op.create_table(
        "asset_lineage",
        *_identity_columns(),
        sa.Column("parent_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("child_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relationship_type", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_asset_id"],
            ["catalog.assets.tenant_id", "catalog.assets.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "child_asset_id"],
            ["catalog.assets.tenant_id", "catalog.assets.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_asset_lineage_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "parent_asset_id",
            "child_asset_id",
            "relationship_type",
            name="uq_asset_lineage_relation",
        ),
        sa.CheckConstraint(
            "parent_asset_id<>child_asset_id AND relationship_type IN ('REFERENCE_IMAGE','START_FRAME','END_FRAME','SOURCE_IMAGE','SOURCE_VIDEO','DERIVED_FROM')",
            name="ck_asset_lineage_values",
        ),
        schema="catalog",
    )
    immutable = (
        "production_plans",
        "production_scenes",
        "production_shots",
        "generation_segments",
        "plan_decisions",
        "media_budget_usage",
    )
    for table in immutable:
        op.execute(
            f"CREATE FUNCTION production.protect_{table}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION '{table} is immutable'; END; $$"
        )
        op.execute(
            f"CREATE TRIGGER protect_{table} BEFORE UPDATE OR DELETE ON production.{table} FOR EACH ROW EXECUTE FUNCTION production.protect_{table}()"
        )
        op.execute(f"REVOKE ALL ON FUNCTION production.protect_{table}() FROM PUBLIC")
    op.execute(
        "CREATE FUNCTION catalog.protect_asset_lineage() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Asset lineage is immutable'; END; $$"
    )
    op.execute(
        "CREATE TRIGGER protect_asset_lineage BEFORE UPDATE OR DELETE ON catalog.asset_lineage FOR EACH ROW EXECUTE FUNCTION catalog.protect_asset_lineage()"
    )
    op.execute("REVOKE ALL ON FUNCTION catalog.protect_asset_lineage() FROM PUBLIC")
    for table in immutable:
        _secure("production", table, "SELECT, INSERT")
    _secure("production", "generation_jobs", "SELECT, INSERT, UPDATE")
    _secure("catalog", "asset_lineage", "SELECT, INSERT")


def downgrade() -> None:
    evidence = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT (SELECT count(*) FROM production.production_plans)+(SELECT count(*) FROM production.generation_jobs)+(SELECT count(*) FROM production.media_budget_usage)+(SELECT count(*) FROM catalog.asset_lineage)"
            )
        )
        .scalar_one()
    )
    if evidence:
        raise RuntimeError("refusing lossy Production downgrade while media provenance exists")
    op.execute("DROP FUNCTION catalog.protect_asset_lineage() CASCADE")
    op.drop_table("asset_lineage", schema="catalog")
    for table in (
        "media_budget_usage",
        "generation_jobs",
        "plan_decisions",
        "generation_segments",
        "production_shots",
        "production_scenes",
        "production_plans",
    ):
        if table != "generation_jobs":
            op.execute(f"DROP FUNCTION production.protect_{table}() CASCADE")
        op.drop_table(table, schema="production")
    op.drop_constraint("ck_assets_origin", "assets", schema="catalog", type_="check")
    op.create_check_constraint(
        "ck_assets_origin", "assets", "origin = 'user_upload'", schema="catalog"
    )
    op.drop_constraint("ck_assets_role", "assets", schema="catalog", type_="check")
    op.create_check_constraint(
        "ck_assets_role",
        "assets",
        "role IN ('product_hero','product_detail','lifestyle','logo','brand_guideline','packaging','other')",
        schema="catalog",
    )
    op.execute("DROP SCHEMA production")
