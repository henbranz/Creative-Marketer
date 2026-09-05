"""Create tenant-isolated Catalog and Product Brain state.

Revision ID: 20260905_0012
Revises: 20260905_0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0012"
down_revision: str | None = "20260905_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME = "creative_marketer_runtime"
MIGRATOR = "creative_marketer_migrator"
TENANT = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def _base_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.execute("CREATE SCHEMA catalog")
    op.create_table(
        "brands",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        *_base_columns(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("website_url", sa.String(2048)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_brands_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_brands_tenant_slug"),
        sa.CheckConstraint("name = btrim(name) AND length(name) > 0", name="ck_brands_name"),
        sa.CheckConstraint("slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'", name="ck_brands_slug"),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_brands_status"),
        schema="catalog",
    )
    op.create_table(
        "brand_profiles",
        *_base_columns(),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("industry", sa.String(200), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("brand_positioning", sa.Text(), nullable=False, server_default=""),
        sa.Column("brand_voice", sa.Text(), nullable=False, server_default=""),
        sa.Column("tone_attributes", postgresql.JSONB(), nullable=False),
        sa.Column("visual_style_keywords", postgresql.JSONB(), nullable=False),
        sa.Column("target_markets", postgresql.JSONB(), nullable=False),
        sa.Column("primary_language", sa.String(16), nullable=False),
        sa.Column("allowed_claims", postgresql.JSONB(), nullable=False),
        sa.Column("prohibited_claims", postgresql.JSONB(), nullable=False),
        sa.Column("competitors", postgresql.JSONB(), nullable=False),
        sa.Column("provenance", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "brand_id"],
            ["catalog.brands.tenant_id", "catalog.brands.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "brand_id", name="uq_brand_profiles_tenant_id_brand_id"),
        sa.CheckConstraint(
            "provenance IN ('user_provided','imported','ai_inferred','validated')",
            name="ck_brand_profiles_provenance",
        ),
        schema="catalog",
    )
    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        *_base_columns(),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("sku", sa.String(100)),
        sa.Column("category", sa.String(200), nullable=False),
        sa.Column("short_description", sa.String(1000), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "brand_id"],
            ["catalog.brands.tenant_id", "catalog.brands.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_products_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "brand_id", "slug", name="uq_products_tenant_brand_slug"),
        sa.CheckConstraint("name = btrim(name) AND length(name) > 0", name="ck_products_name"),
        sa.CheckConstraint("slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'", name="ck_products_slug"),
        sa.CheckConstraint("status IN ('draft','active','archived')", name="ck_products_status"),
        schema="catalog",
    )
    op.create_index(
        "ix_products_tenant_brand", "products", ["tenant_id", "brand_id"], schema="catalog"
    )
    op.create_table(
        "product_profiles",
        *_base_columns(),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("features", postgresql.JSONB(), nullable=False),
        sa.Column("benefits", postgresql.JSONB(), nullable=False),
        sa.Column("materials", postgresql.JSONB(), nullable=False),
        sa.Column("variants", postgresql.JSONB(), nullable=False),
        sa.Column("price", sa.Numeric(19, 2)),
        sa.Column("currency", sa.String(3)),
        sa.Column("estimated_margin", sa.Numeric(7, 4)),
        sa.Column("target_audiences", postgresql.JSONB(), nullable=False),
        sa.Column("problems_solved", postgresql.JSONB(), nullable=False),
        sa.Column("use_cases", postgresql.JSONB(), nullable=False),
        sa.Column("differentiators", postgresql.JSONB(), nullable=False),
        sa.Column("purchase_objections", postgresql.JSONB(), nullable=False),
        sa.Column("allowed_claims", postgresql.JSONB(), nullable=False),
        sa.Column("prohibited_claims", postgresql.JSONB(), nullable=False),
        sa.Column("shipping_summary", sa.Text()),
        sa.Column("seasonality_notes", sa.Text()),
        sa.Column("landing_page_url", sa.String(2048)),
        sa.Column("competitor_product_refs", postgresql.JSONB(), nullable=False),
        sa.Column("provenance", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["catalog.products.tenant_id", "catalog.products.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "product_id", name="uq_product_profiles_tenant_id_product_id"
        ),
        sa.CheckConstraint(
            "(price IS NULL) = (currency IS NULL)", name="ck_product_profiles_money_pair"
        ),
        sa.CheckConstraint("price IS NULL OR price >= 0", name="ck_product_profiles_price"),
        sa.CheckConstraint(
            "estimated_margin IS NULL OR estimated_margin BETWEEN 0 AND 1",
            name="ck_product_profiles_margin",
        ),
        sa.CheckConstraint(
            "provenance IN ('user_provided','imported','ai_inferred','validated')",
            name="ck_product_profiles_provenance",
        ),
        schema="catalog",
    )
    op.create_table(
        "product_briefs",
        *_base_columns(),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("provenance", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["catalog.products.tenant_id", "catalog.products.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "product_id", name="uq_product_briefs_tenant_id_product_id"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(content) = 'object' AND octet_length(content::text) <= 65536",
            name="ck_product_briefs_content",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_product_briefs_revision"),
        sa.CheckConstraint(
            "provenance IN ('user_provided','imported','ai_inferred','validated')",
            name="ck_product_briefs_provenance",
        ),
        schema="catalog",
    )
    op.create_table(
        "product_knowledge_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("digest", sa.String(71), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["catalog.products.tenant_id", "catalog.products.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_product_snapshots_tenant_id_id"),
        sa.CheckConstraint(
            "schema_version >= 1 AND source_revision >= 1", name="ck_product_snapshots_versions"
        ),
        sa.CheckConstraint("digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_product_snapshots_digest"),
        sa.CheckConstraint(
            "jsonb_typeof(content) = 'object' AND octet_length(content::text) <= 262144",
            name="ck_product_snapshots_content",
        ),
        schema="catalog",
    )
    op.create_index(
        "ix_product_snapshots_latest",
        "product_knowledge_snapshots",
        ["tenant_id", "product_id", "created_at"],
        schema="catalog",
    )

    op.execute(
        "CREATE FUNCTION catalog.reject_snapshot_mutation() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
        "'product knowledge snapshots are immutable'; END; $$"
    )
    op.execute(
        "CREATE TRIGGER reject_snapshot_mutation BEFORE UPDATE OR DELETE ON "
        "catalog.product_knowledge_snapshots FOR EACH ROW EXECUTE FUNCTION "
        "catalog.reject_snapshot_mutation()"
    )
    op.execute("REVOKE ALL ON FUNCTION catalog.reject_snapshot_mutation() FROM PUBLIC")
    op.execute(f"REVOKE ALL ON SCHEMA catalog FROM PUBLIC, {RUNTIME}")
    op.execute(f"GRANT USAGE ON SCHEMA catalog TO {RUNTIME}")
    for table in (
        "brands",
        "brand_profiles",
        "products",
        "product_profiles",
        "product_briefs",
        "product_knowledge_snapshots",
    ):
        op.execute(f"ALTER TABLE catalog.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE catalog.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_migration_control ON catalog.{table} FOR ALL "
            f"TO {MIGRATOR} USING (true) WITH CHECK (true)"
        )
        op.execute(
            f"CREATE POLICY {table}_runtime_tenant ON catalog.{table} FOR ALL TO {RUNTIME} "
            f"USING (tenant_id = {TENANT}) WITH CHECK (tenant_id = {TENANT})"
        )
        op.execute(f"REVOKE ALL ON catalog.{table} FROM PUBLIC, {RUNTIME}")
    for table in ("brands", "brand_profiles", "products", "product_profiles", "product_briefs"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON catalog.{table} TO {RUNTIME}")
    op.execute(f"GRANT SELECT, INSERT ON catalog.product_knowledge_snapshots TO {RUNTIME}")


def downgrade() -> None:
    count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT (SELECT count(*) FROM catalog.brands) + "
                "(SELECT count(*) FROM catalog.products) + "
                "(SELECT count(*) FROM catalog.product_knowledge_snapshots)"
            )
        )
        .scalar_one()
    )
    if count:
        raise RuntimeError("refusing lossy Catalog downgrade while Product Brain state exists")
    op.execute("DROP SCHEMA catalog CASCADE")
