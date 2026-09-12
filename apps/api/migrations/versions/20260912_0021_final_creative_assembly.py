# ruff: noqa: E501
"""Add deterministic final creative assembly and publishing handoff evidence.

Revision ID: 20260912_0021
Revises: 20260912_0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260912_0021"
down_revision: str | None = "20260912_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME = "creative_marketer_runtime"
MIGRATOR = "creative_marketer_migrator"
TENANT = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def _secure(table: str, permissions: str) -> None:
    op.execute(f"ALTER TABLE assembly.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE assembly.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_migration_control ON assembly.{table} FOR ALL TO {MIGRATOR} USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY {table}_runtime_tenant ON assembly.{table} FOR ALL TO {RUNTIME} USING (tenant_id = {TENANT}) WITH CHECK (tenant_id = {TENANT})"
    )
    op.execute(f"REVOKE ALL ON assembly.{table} FROM PUBLIC, {RUNTIME}")
    op.execute(f"GRANT {permissions} ON assembly.{table} TO {RUNTIME}")


def _identity() -> list[sa.Column[object]]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
    ]


def _tenant_fk() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT")


def upgrade() -> None:
    op.execute("CREATE SCHEMA assembly")
    op.execute(f"GRANT USAGE ON SCHEMA assembly TO {RUNTIME}, {MIGRATOR}")
    op.drop_constraint("ck_assets_role", "assets", schema="catalog", type_="check")
    op.create_check_constraint(
        "ck_assets_role",
        "assets",
        "role IN ('product_hero','product_detail','lifestyle','logo','brand_guideline','packaging','other','brand_reference','production_reference','generated_shot','generated_frame','final_creative')",
        schema="catalog",
    )
    op.execute(
        "ALTER TABLE catalog.asset_lineage DROP CONSTRAINT ck_asset_lineage_ck_asset_lineage_values"
    )
    op.create_check_constraint(
        "ck_asset_lineage_values",
        "asset_lineage",
        "parent_asset_id<>child_asset_id AND relationship_type IN ('REFERENCE_IMAGE','START_FRAME','END_FRAME','SOURCE_IMAGE','SOURCE_VIDEO','DERIVED_FROM','ASSEMBLED_FROM')",
        schema="catalog",
    )

    op.create_table(
        "assembly_plans",
        *_identity(),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("production_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("production_plan_digest", sa.String(71), nullable=False),
        sa.Column("creative_concept_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("creative_concept_digest", sa.String(71), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("render_profile_key", sa.String(64), nullable=False),
        sa.Column("render_profile_version", sa.Integer(), nullable=False),
        sa.Column("timeline_duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("semantic_digest", sa.String(71), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _tenant_fk(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["catalog.products.tenant_id", "catalog.products.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_assembly_plans_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "production_plan_id",
            "semantic_digest",
            name="uq_assembly_plans_semantic_revision",
        ),
        sa.CheckConstraint(
            "schema_version=1 AND render_profile_key='social_vertical_v1' AND render_profile_version=1 AND timeline_duration_ms BETWEEN 1000 AND 60000 AND production_plan_digest ~ '^sha256:[0-9a-f]{64}$' AND creative_concept_digest ~ '^sha256:[0-9a-f]{64}$' AND semantic_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_assembly_plans_values",
        ),
        schema="assembly",
    )
    op.create_table(
        "assembly_items",
        *_identity(),
        sa.Column("assembly_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_key", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_asset_digest", sa.String(71), nullable=False),
        sa.Column("source_media_kind", sa.String(16), nullable=False),
        sa.Column("production_shot_ids", postgresql.JSONB(), nullable=False),
        sa.Column("production_shot_keys", postgresql.JSONB(), nullable=False),
        sa.Column("generation_segment_id", postgresql.UUID(as_uuid=True)),
        sa.Column("timeline_start_ms", sa.BigInteger(), nullable=False),
        sa.Column("timeline_duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("source_in_ms", sa.BigInteger(), nullable=False),
        sa.Column("source_out_ms", sa.BigInteger()),
        sa.Column("fit_mode", sa.String(32), nullable=False),
        sa.Column("audio_behavior", sa.String(16), nullable=False),
        sa.Column("transition_in", sa.String(16), nullable=False),
        sa.Column("transition_out", sa.String(16), nullable=False),
        sa.Column("transition_duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _tenant_fk(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "assembly_plan_id"],
            ["assembly.assembly_plans.tenant_id", "assembly.assembly_plans.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_asset_id"],
            ["catalog.assets.tenant_id", "catalog.assets.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "generation_segment_id"],
            ["production.generation_segments.tenant_id", "production.generation_segments.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_assembly_items_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "assembly_plan_id", "item_key", name="uq_assembly_items_key"
        ),
        sa.CheckConstraint(
            "ordinal>0 AND timeline_start_ms>=0 AND timeline_duration_ms>0 AND source_in_ms>=0 AND (source_out_ms IS NULL OR source_out_ms>source_in_ms) AND source_asset_digest ~ '^sha256:[0-9a-f]{64}$' AND jsonb_typeof(production_shot_ids)='array' AND jsonb_array_length(production_shot_ids)>0",
            name="ck_assembly_items_values",
        ),
        schema="assembly",
    )
    for table, extra in (
        ("caption_cues", [sa.Column("caption_kind", sa.String(32), nullable=False)]),
        (
            "overlay_instructions",
            [
                sa.Column("position", sa.String(32), nullable=False),
                sa.Column("style_token", sa.String(32), nullable=False),
                sa.Column("source_provenance", sa.String(256), nullable=False),
            ],
        ),
    ):
        op.create_table(
            table,
            *_identity(),
            sa.Column("assembly_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("text", sa.String(500), nullable=False),
            sa.Column("start_ms", sa.BigInteger(), nullable=False),
            sa.Column("end_ms", sa.BigInteger(), nullable=False),
            *extra,
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            _tenant_fk(),
            sa.ForeignKeyConstraint(
                ["tenant_id", "assembly_plan_id"],
                ["assembly.assembly_plans.tenant_id", "assembly.assembly_plans.id"],
                ondelete="RESTRICT",
            ),
            sa.UniqueConstraint("tenant_id", "id", name=f"uq_{table}_tenant_id_id"),
            sa.UniqueConstraint(
                "tenant_id", "assembly_plan_id", "ordinal", name=f"uq_{table}_ordinal"
            ),
            sa.CheckConstraint(
                "ordinal>0 AND start_ms>=0 AND end_ms>start_ms AND length(btrim(text))>0",
                name=f"ck_{table}_values",
            ),
            schema="assembly",
        )
    op.create_table(
        "manual_source_bindings",
        *_identity(),
        sa.Column("production_shot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bound_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "bound_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _tenant_fk(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "production_shot_id"],
            ["production.production_shots.tenant_id", "production.production_shots.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            ["catalog.assets.tenant_id", "catalog.assets.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["bound_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_manual_source_bindings_tenant_id_id"),
        schema="assembly",
    )
    op.create_index(
        "ix_manual_source_bindings_latest",
        "manual_source_bindings",
        ["tenant_id", "production_shot_id", "bound_at"],
        schema="assembly",
    )
    op.create_table(
        "assembly_jobs",
        *_identity(),
        sa.Column("assembly_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("output_asset_id", postgresql.UUID(as_uuid=True)),
        sa.Column("final_creative_id", postgresql.UUID(as_uuid=True)),
        sa.Column("renderer", sa.String(32)),
        sa.Column("renderer_version", sa.String(128)),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _tenant_fk(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "assembly_plan_id"],
            ["assembly.assembly_plans.tenant_id", "assembly.assembly_plans.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "output_asset_id"],
            ["catalog.assets.tenant_id", "catalog.assets.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_assembly_jobs_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "assembly_plan_id", name="uq_assembly_jobs_plan"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_assembly_jobs_idempotency"),
        sa.CheckConstraint(
            "status IN ('PENDING','READY','RENDERING','IMPORTING','SUCCEEDED','FAILED')",
            name="ck_assembly_jobs_status",
        ),
        schema="assembly",
    )
    op.create_table(
        "final_creatives",
        *_identity(),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assembly_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assembly_plan_digest", sa.String(71), nullable=False),
        sa.Column("production_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("production_plan_digest", sa.String(71), nullable=False),
        sa.Column("creative_concept_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("creative_concept_digest", sa.String(71), nullable=False),
        sa.Column("output_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("output_asset_digest", sa.String(71), nullable=False),
        sa.Column("render_profile_key", sa.String(64), nullable=False),
        sa.Column("render_profile_version", sa.Integer(), nullable=False),
        sa.Column("renderer", sa.String(32), nullable=False),
        sa.Column("renderer_version", sa.String(128), nullable=False),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("fps", sa.Integer(), nullable=False),
        sa.Column("has_audio", sa.Boolean(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("semantic_digest", sa.String(71), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _tenant_fk(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["catalog.products.tenant_id", "catalog.products.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "assembly_plan_id"],
            ["assembly.assembly_plans.tenant_id", "assembly.assembly_plans.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "production_plan_id"],
            ["production.production_plans.tenant_id", "production.production_plans.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "creative_concept_id"],
            ["creative.concepts.tenant_id", "creative.concepts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "output_asset_id"],
            ["catalog.assets.tenant_id", "catalog.assets.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_final_creatives_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "assembly_plan_id", name="uq_final_creatives_plan"),
        sa.CheckConstraint(
            "render_profile_key='social_vertical_v1' AND render_profile_version=1 AND duration_ms>0 AND width=1080 AND height=1920 AND fps=24 AND source_count>0 AND assembly_plan_digest ~ '^sha256:[0-9a-f]{64}$' AND production_plan_digest ~ '^sha256:[0-9a-f]{64}$' AND creative_concept_digest ~ '^sha256:[0-9a-f]{64}$' AND output_asset_digest ~ '^sha256:[0-9a-f]{64}$' AND semantic_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_final_creatives_values",
        ),
        schema="assembly",
    )
    op.create_foreign_key(
        "fk_assembly_jobs_final_creative",
        "assembly_jobs",
        "final_creatives",
        ["tenant_id", "final_creative_id"],
        ["tenant_id", "id"],
        source_schema="assembly",
        referent_schema="assembly",
        ondelete="RESTRICT",
    )
    op.create_table(
        "final_creative_decisions",
        *_identity(),
        sa.Column("final_creative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("final_creative_digest", sa.String(71), nullable=False),
        sa.Column("output_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("output_asset_digest", sa.String(71), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _tenant_fk(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "final_creative_id"],
            ["assembly.final_creatives.tenant_id", "assembly.final_creatives.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "output_asset_id"],
            ["catalog.assets.tenant_id", "catalog.assets.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_final_creative_decisions_tenant_id_id"),
        sa.CheckConstraint(
            "state IN ('APPROVED_FOR_PUBLISHING','REJECTED') AND final_creative_digest ~ '^sha256:[0-9a-f]{64}$' AND output_asset_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_final_creative_decisions_values",
        ),
        schema="assembly",
    )
    immutable = (
        "assembly_plans",
        "assembly_items",
        "caption_cues",
        "overlay_instructions",
        "manual_source_bindings",
        "final_creatives",
        "final_creative_decisions",
    )
    for table in immutable:
        op.execute(
            f"CREATE FUNCTION assembly.protect_{table}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION '{table} is immutable'; END; $$"
        )
        op.execute(
            f"CREATE TRIGGER protect_{table} BEFORE UPDATE OR DELETE ON assembly.{table} FOR EACH ROW EXECUTE FUNCTION assembly.protect_{table}()"
        )
        op.execute(f"REVOKE ALL ON FUNCTION assembly.protect_{table}() FROM PUBLIC")
        _secure(table, "SELECT, INSERT")
    op.execute(
        """
        CREATE FUNCTION assembly.protect_assembly_jobs() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'assembly jobs cannot be deleted';
          END IF;
          IF NEW.id <> OLD.id OR NEW.tenant_id <> OLD.tenant_id
             OR NEW.assembly_plan_id <> OLD.assembly_plan_id
             OR NEW.idempotency_key <> OLD.idempotency_key
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'assembly job identity is immutable';
          END IF;
          IF NOT (
            (OLD.status = 'PENDING' AND NEW.status IN ('READY', 'FAILED')) OR
            (OLD.status = 'READY' AND NEW.status IN ('RENDERING', 'FAILED')) OR
            (OLD.status = 'RENDERING' AND NEW.status IN ('IMPORTING', 'FAILED')) OR
            (OLD.status = 'IMPORTING' AND NEW.status IN ('SUCCEEDED', 'FAILED'))
          ) THEN
            RAISE EXCEPTION 'invalid assembly job transition';
          END IF;
          IF (NEW.status = 'FAILED') <> (NEW.failure_code IS NOT NULL) THEN
            RAISE EXCEPTION 'assembly job failure evidence is invalid';
          END IF;
          IF NEW.status = 'SUCCEEDED' AND (
            NEW.output_asset_id IS NULL OR NEW.final_creative_id IS NULL
            OR NEW.renderer IS NULL OR NEW.renderer_version IS NULL
          ) THEN
            RAISE EXCEPTION 'assembly job success evidence is incomplete';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER protect_assembly_jobs BEFORE UPDATE OR DELETE ON assembly.assembly_jobs FOR EACH ROW EXECUTE FUNCTION assembly.protect_assembly_jobs()"
    )
    op.execute("REVOKE ALL ON FUNCTION assembly.protect_assembly_jobs() FROM PUBLIC")
    _secure("assembly_jobs", "SELECT, INSERT, UPDATE")


def downgrade() -> None:
    evidence = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT (SELECT count(*) FROM assembly.assembly_plans)"
                "+(SELECT count(*) FROM assembly.assembly_items)"
                "+(SELECT count(*) FROM assembly.caption_cues)"
                "+(SELECT count(*) FROM assembly.overlay_instructions)"
                "+(SELECT count(*) FROM assembly.manual_source_bindings)"
                "+(SELECT count(*) FROM assembly.assembly_jobs)"
                "+(SELECT count(*) FROM assembly.final_creatives)"
                "+(SELECT count(*) FROM assembly.final_creative_decisions)"
            )
        )
        .scalar_one()
    )
    if evidence:
        raise RuntimeError("refusing lossy Assembly downgrade while final provenance exists")
    for table in (
        "final_creative_decisions",
        "assembly_jobs",
        "final_creatives",
        "manual_source_bindings",
        "overlay_instructions",
        "caption_cues",
        "assembly_items",
        "assembly_plans",
    ):
        op.execute(f"DROP FUNCTION assembly.protect_{table}() CASCADE")
        op.drop_table(table, schema="assembly")
    op.execute("DROP SCHEMA assembly")
    op.execute(
        "ALTER TABLE catalog.asset_lineage DROP CONSTRAINT ck_asset_lineage_ck_asset_lineage_values"
    )
    op.create_check_constraint(
        "ck_asset_lineage_values",
        "asset_lineage",
        "parent_asset_id<>child_asset_id AND relationship_type IN ('REFERENCE_IMAGE','START_FRAME','END_FRAME','SOURCE_IMAGE','SOURCE_VIDEO','DERIVED_FROM')",
        schema="catalog",
    )
    op.drop_constraint("ck_assets_role", "assets", schema="catalog", type_="check")
    op.create_check_constraint(
        "ck_assets_role",
        "assets",
        "role IN ('product_hero','product_detail','lifestyle','logo','brand_guideline','packaging','other','brand_reference','production_reference','generated_shot','generated_frame')",
        schema="catalog",
    )
