# ruff: noqa: E501
"""Add tenant-safe competitor targets and immutable social evidence.

Revision ID: 20260913_0022
Revises: 20260912_0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260913_0022"
down_revision: str | None = "20260912_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME = "creative_marketer_runtime"
MIGRATOR = "creative_marketer_migrator"
TENANT = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def _secure(table: str, permissions: str) -> None:
    op.execute(f"ALTER TABLE research.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE research.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_migration_control ON research.{table} FOR ALL TO {MIGRATOR} USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY {table}_runtime_tenant ON research.{table} FOR ALL TO {RUNTIME} USING (tenant_id = {TENANT}) WITH CHECK (tenant_id = {TENANT})"
    )
    op.execute(f"REVOKE ALL ON research.{table} FROM PUBLIC, {RUNTIME}")
    op.execute(f"GRANT {permissions} ON research.{table} TO {RUNTIME}")


def upgrade() -> None:
    op.create_table(
        "research_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("website_url", sa.String(2048)),
        sa.Column("platform", sa.String(32)),
        sa.Column("platform_handle", sa.String(200)),
        sa.Column("platform_profile_url", sa.String(2048)),
        sa.Column("platform_identifier", sa.String(200)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["catalog.products.tenant_id", "catalog.products.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_research_targets_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "product_id", "id", name="uq_research_targets_tenant_product_id"
        ),
        sa.CheckConstraint(
            "kind IN ('competitor_brand','advertiser','social_profile','product')",
            name="ck_research_targets_kind",
        ),
        sa.CheckConstraint(
            "platform IS NULL OR platform IN ('facebook','instagram','tiktok','other')",
            name="ck_research_targets_platform",
        ),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_research_targets_status"),
        sa.CheckConstraint(
            "platform IS NOT NULL OR (platform_handle IS NULL AND platform_profile_url IS NULL AND platform_identifier IS NULL)",
            name="ck_research_targets_platform_shape",
        ),
        schema="research",
    )
    op.create_index(
        "ix_research_targets_tenant_product_status",
        "research_targets",
        ["tenant_id", "product_id", "status", "created_at"],
        schema="research",
    )
    op.create_table(
        "social_evidence_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("research_target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("evidence_type", sa.String(32), nullable=False),
        sa.Column("provenance", sa.String(32), nullable=False),
        sa.Column("source_url", sa.String(2048)),
        sa.Column("destination_url", sa.String(2048)),
        sa.Column("advertiser_name", sa.String(300)),
        sa.Column("advertiser_platform_id", sa.String(200)),
        sa.Column("platform_content_id", sa.String(200)),
        sa.Column("headline", sa.String(1000)),
        sa.Column("body_text", sa.Text()),
        sa.Column("cta", sa.String(300)),
        sa.Column("ad_objective", sa.String(200)),
        sa.Column("media_type", sa.String(100)),
        sa.Column("placements", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("activity_status", sa.String(100)),
        sa.Column("region", sa.String(200)),
        sa.Column("reach_range", sa.String(200)),
        sa.Column("source_provider", sa.String(100)),
        sa.Column("media_asset_id", postgresql.UUID(as_uuid=True)),
        sa.Column("raw_provider_metadata_digest", sa.String(71)),
        sa.Column("semantic_digest", sa.String(71), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("rights_status", sa.String(32), nullable=False),
        sa.Column("allowed_uses", postgresql.JSONB(), nullable=False),
        sa.Column("captured_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["captured_by"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["catalog.products.tenant_id", "catalog.products.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id", "research_target_id"],
            [
                "research.research_targets.tenant_id",
                "research.research_targets.product_id",
                "research.research_targets.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "media_asset_id"],
            ["catalog.assets.tenant_id", "catalog.assets.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_social_evidence_tenant_id_id"),
        sa.CheckConstraint(
            "platform IN ('facebook','instagram','tiktok','other')",
            name="ck_social_evidence_platform",
        ),
        sa.CheckConstraint(
            "evidence_type IN ('ad','post','reel','video','screenshot','exported_image','exported_video','other')",
            name="ck_social_evidence_type",
        ),
        sa.CheckConstraint(
            "provenance IN ('user_provided','provider_fetched')",
            name="ck_social_evidence_provenance",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(placements) = 'array' AND jsonb_array_length(placements) <= 20",
            name="ck_social_evidence_placements",
        ),
        sa.CheckConstraint(
            "semantic_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_social_evidence_digest"
        ),
        sa.CheckConstraint(
            "raw_provider_metadata_digest IS NULL OR raw_provider_metadata_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_social_evidence_raw_digest",
        ),
        sa.CheckConstraint("schema_version = 1", name="ck_social_evidence_version"),
        sa.CheckConstraint(
            "rights_status = 'restricted' AND allowed_uses = '[\"internal_analysis\"]'::jsonb",
            name="ck_social_evidence_rights",
        ),
        sa.CheckConstraint(
            "(provenance = 'user_provided' AND source_provider IS NULL AND raw_provider_metadata_digest IS NULL) OR (provenance = 'provider_fetched' AND source_provider IS NOT NULL)",
            name="ck_social_evidence_provenance_shape",
        ),
        schema="research",
    )
    op.create_index(
        "ix_social_evidence_tenant_product_captured",
        "social_evidence_snapshots",
        ["tenant_id", "product_id", "captured_at"],
        schema="research",
    )
    op.create_index(
        "ix_social_evidence_target_content",
        "social_evidence_snapshots",
        ["tenant_id", "research_target_id", "platform_content_id", "semantic_digest"],
        schema="research",
    )
    op.execute(
        "CREATE FUNCTION research.protect_research_target() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF (NEW.tenant_id,NEW.product_id,NEW.kind,NEW.created_by,NEW.created_at) IS DISTINCT FROM (OLD.tenant_id,OLD.product_id,OLD.kind,OLD.created_by,OLD.created_at) THEN RAISE EXCEPTION 'research target identity is immutable'; END IF; IF OLD.status = 'archived' AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'archived research target is immutable'; END IF; IF OLD.status <> NEW.status AND NOT (OLD.status = 'active' AND NEW.status = 'archived') THEN RAISE EXCEPTION 'invalid research target lifecycle transition'; END IF; RETURN NEW; END; $$"
    )
    op.execute(
        "CREATE TRIGGER protect_research_target BEFORE UPDATE ON research.research_targets FOR EACH ROW EXECUTE FUNCTION research.protect_research_target()"
    )
    op.execute(
        "CREATE FUNCTION research.reject_social_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'social evidence snapshots are immutable'; END; $$"
    )
    op.execute(
        "CREATE TRIGGER reject_social_evidence_update_delete BEFORE UPDATE OR DELETE ON research.social_evidence_snapshots FOR EACH ROW EXECUTE FUNCTION research.reject_social_evidence_mutation()"
    )
    op.execute("REVOKE ALL ON FUNCTION research.protect_research_target() FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION research.reject_social_evidence_mutation() FROM PUBLIC")
    _secure("research_targets", "SELECT, INSERT, UPDATE")
    _secure("social_evidence_snapshots", "SELECT, INSERT")


def downgrade() -> None:
    count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT (SELECT count(*) FROM research.research_targets) + (SELECT count(*) FROM research.social_evidence_snapshots)"
            )
        )
        .scalar_one()
    )
    if count:
        raise RuntimeError("refusing lossy social research downgrade while data exists")
    op.drop_table("social_evidence_snapshots", schema="research")
    op.drop_table("research_targets", schema="research")
    op.execute("DROP FUNCTION research.reject_social_evidence_mutation()")
    op.execute("DROP FUNCTION research.protect_research_target()")
