# ruff: noqa: E501
"""Create the tenant-isolated product asset library.

Revision ID: 20260906_0013
Revises: 20260905_0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260906_0013"
down_revision: str | None = "20260905_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME = "creative_marketer_runtime"
MIGRATOR = "creative_marketer_migrator"
TENANT = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True)),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("declared_mime_type", sa.String(100), nullable=False),
        sa.Column("detected_mime_type", sa.String(100)),
        sa.Column("rights_status", sa.String(32), nullable=False),
        sa.Column("allowed_uses", postgresql.JSONB(), nullable=False),
        sa.Column("upload_object_key", sa.String(1024), nullable=False),
        sa.Column("object_key", sa.String(1024)),
        sa.Column("byte_size", sa.BigInteger()),
        sa.Column("digest", sa.String(71)),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("duration_ms", sa.BigInteger()),
        sa.Column("rejection_code", sa.String(64)),
        sa.Column("parent_asset_id", postgresql.UUID(as_uuid=True)),
        sa.Column("source_url", sa.String(2048)),
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
            ["tenant_id", "brand_id"],
            ["catalog.brands.tenant_id", "catalog.brands.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["catalog.products.tenant_id", "catalog.products.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_asset_id"],
            ["catalog.assets.tenant_id", "catalog.assets.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_assets_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "upload_object_key", name="uq_assets_tenant_upload_key"),
        sa.UniqueConstraint("tenant_id", "object_key", name="uq_assets_tenant_object_key"),
        sa.CheckConstraint("kind IN ('image','video','document')", name="ck_assets_kind"),
        sa.CheckConstraint(
            "role IN ('product_hero','product_detail','lifestyle','logo','brand_guideline','packaging','other')",
            name="ck_assets_role",
        ),
        sa.CheckConstraint("origin = 'user_upload'", name="ck_assets_origin"),
        sa.CheckConstraint(
            "status IN ('pending_upload','validating','ready','rejected','archived')",
            name="ck_assets_status",
        ),
        sa.CheckConstraint(
            "rights_status IN ('confirmed','unknown','restricted')", name="ck_assets_rights"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(allowed_uses) = 'array' AND jsonb_array_length(allowed_uses) BETWEEN 1 AND 4",
            name="ck_assets_allowed_uses",
        ),
        sa.CheckConstraint(
            'allowed_uses <@ \'["internal_analysis","generation_input","organic_publishing","paid_advertising"]\'::jsonb',
            name="ck_assets_allowed_use_values",
        ),
        sa.CheckConstraint(
            "rights_status = 'confirmed' OR allowed_uses = '[\"internal_analysis\"]'::jsonb",
            name="ck_assets_unconfirmed_use",
        ),
        sa.CheckConstraint(
            "declared_mime_type IN ('image/jpeg','image/png','image/webp','video/mp4','video/webm','application/pdf')",
            name="ck_assets_declared_mime",
        ),
        sa.CheckConstraint(
            "status <> 'ready' OR (object_key IS NOT NULL AND detected_mime_type = declared_mime_type "
            "AND byte_size > 0 AND digest ~ '^sha256:[0-9a-f]{64}$')",
            name="ck_assets_ready_identity",
        ),
        sa.CheckConstraint(
            "byte_size IS NULL OR (byte_size > 0 AND ((kind IN ('image','document') AND byte_size <= 26214400) "
            "OR (kind = 'video' AND byte_size <= 262144000)))",
            name="ck_assets_byte_size",
        ),
        sa.CheckConstraint(
            "upload_object_key LIKE ('tenants/' || tenant_id::text || '/assets/' || id::text || '/uploads/%') "
            "AND (object_key IS NULL OR object_key LIKE ('tenants/' || tenant_id::text || '/assets/' || id::text || '/objects/%'))",
            name="ck_assets_storage_prefix",
        ),
        sa.CheckConstraint(
            "parent_asset_id IS NULL OR parent_asset_id <> id", name="ck_assets_parent"
        ),
        schema="catalog",
    )
    op.create_index(
        "ix_assets_tenant_product_status",
        "assets",
        ["tenant_id", "product_id", "status", "created_at"],
        schema="catalog",
    )
    op.create_index(
        "ix_assets_tenant_brand_status",
        "assets",
        ["tenant_id", "brand_id", "status", "created_at"],
        schema="catalog",
    )
    op.execute(
        "CREATE FUNCTION catalog.protect_ready_asset_identity() RETURNS trigger LANGUAGE plpgsql "
        "AS $$ BEGIN IF (NEW.tenant_id, NEW.brand_id, NEW.product_id, NEW.kind, NEW.origin, "
        "NEW.declared_mime_type, NEW.upload_object_key, NEW.created_by) IS DISTINCT FROM "
        "(OLD.tenant_id, OLD.brand_id, OLD.product_id, OLD.kind, OLD.origin, OLD.declared_mime_type, "
        "OLD.upload_object_key, OLD.created_by) THEN RAISE EXCEPTION 'asset creation identity is immutable'; END IF; "
        "IF OLD.status <> NEW.status AND NOT ((OLD.status = 'pending_upload' AND NEW.status = 'validating') OR "
        "(OLD.status = 'validating' AND NEW.status IN ('pending_upload','ready','rejected')) OR "
        "(OLD.status IN ('ready','rejected') AND NEW.status = 'archived')) THEN "
        "RAISE EXCEPTION 'invalid asset lifecycle transition'; END IF; "
        "IF OLD.digest IS NOT NULL AND (NEW.object_key, NEW.detected_mime_type, NEW.byte_size, NEW.digest) "
        "IS DISTINCT FROM (OLD.object_key, OLD.detected_mime_type, OLD.byte_size, OLD.digest) THEN "
        "RAISE EXCEPTION 'ready asset binary identity is immutable'; END IF; "
        "RETURN NEW; END; $$"
    )
    op.execute(
        "CREATE TRIGGER protect_ready_asset_identity BEFORE UPDATE ON catalog.assets "
        "FOR EACH ROW EXECUTE FUNCTION catalog.protect_ready_asset_identity()"
    )
    op.execute("REVOKE ALL ON FUNCTION catalog.protect_ready_asset_identity() FROM PUBLIC")
    op.execute("ALTER TABLE catalog.assets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE catalog.assets FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY assets_migration_control ON catalog.assets FOR ALL TO {MIGRATOR} "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY assets_runtime_tenant ON catalog.assets FOR ALL TO {RUNTIME} "
        f"USING (tenant_id = {TENANT}) WITH CHECK (tenant_id = {TENANT})"
    )
    op.execute(f"REVOKE ALL ON catalog.assets FROM PUBLIC, {RUNTIME}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON catalog.assets TO {RUNTIME}")


def downgrade() -> None:
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM catalog.assets")).scalar_one()
    if count:
        raise RuntimeError("refusing lossy Asset Library downgrade while assets exist")
    op.drop_table("assets", schema="catalog")
    op.execute("DROP FUNCTION catalog.protect_ready_asset_identity()")
