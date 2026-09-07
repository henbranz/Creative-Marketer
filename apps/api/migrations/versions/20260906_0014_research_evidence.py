# ruff: noqa: E501
"""Create secure research ingestion and immutable evidence storage.

Revision ID: 20260906_0014
Revises: 20260906_0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260906_0014"
down_revision: str | None = "20260906_0013"
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
    op.execute("CREATE SCHEMA IF NOT EXISTS research AUTHORIZATION creative_marketer_migrator")
    op.execute(
        f"REVOKE ALL ON SCHEMA research FROM PUBLIC; GRANT USAGE ON SCHEMA research TO {RUNTIME}"
    )
    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("canonical_url", sa.String(2048), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
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
        sa.UniqueConstraint("tenant_id", "id", name="uq_research_sources_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "product_id", "canonical_url", name="uq_research_sources_product_url"
        ),
        sa.CheckConstraint("source_type = 'web_page'", name="ck_research_sources_type"),
        sa.CheckConstraint(
            "category IN ('competitor','product_page','landing_page','pricing','review','market_reference','creative_reference','other')",
            name="ck_research_sources_category",
        ),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_research_sources_status"),
        schema="research",
    )
    op.create_index(
        "ix_research_sources_tenant_product_status",
        "sources",
        ["tenant_id", "product_id", "status", "created_at"],
        schema="research",
    )
    op.create_table(
        "source_fetches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_url", sa.String(2048), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_object_key", sa.String(1024), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("final_url", sa.String(2048)),
        sa.Column("http_status", sa.Integer()),
        sa.Column("content_type", sa.String(100)),
        sa.Column("raw_digest", sa.String(71)),
        sa.Column("raw_byte_size", sa.BigInteger()),
        sa.Column("evidence_snapshot_id", postgresql.UUID(as_uuid=True)),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["research.sources.tenant_id", "research.sources.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_source_fetches_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "raw_object_key", name="uq_source_fetches_tenant_raw_key"),
        sa.CheckConstraint(
            "status IN ('pending','fetching','succeeded','rejected','failed')",
            name="ck_source_fetches_status",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code IN ('url_invalid','secret_query_parameter','blocked_network_target','dns_failed','dns_rebinding','robots_denied','robots_unavailable','redirect_limit','redirect_invalid','connect_timeout','overall_timeout','http_error','response_too_large','unsupported_media_type','binary_content','extraction_failed','storage_failed','concurrent_fetch')",
            name="ck_source_fetches_failure_code",
        ),
        sa.CheckConstraint(
            "raw_object_key LIKE ('tenants/' || tenant_id::text || '/research/' || source_id::text || '/' || id::text || '/raw/%')",
            name="ck_source_fetches_storage_prefix",
        ),
        sa.CheckConstraint(
            "raw_byte_size IS NULL OR raw_byte_size BETWEEN 1 AND 5242880",
            name="ck_source_fetches_size",
        ),
        sa.CheckConstraint(
            "raw_digest IS NULL OR raw_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_source_fetches_raw_digest",
        ),
        sa.CheckConstraint(
            "(status = 'succeeded' AND completed_at IS NOT NULL AND evidence_snapshot_id IS NOT NULL AND raw_digest IS NOT NULL AND raw_byte_size IS NOT NULL AND final_url IS NOT NULL AND content_type IS NOT NULL AND failure_code IS NULL) OR (status IN ('rejected','failed') AND completed_at IS NOT NULL AND failure_code IS NOT NULL AND evidence_snapshot_id IS NULL) OR (status IN ('pending','fetching') AND completed_at IS NULL AND evidence_snapshot_id IS NULL AND failure_code IS NULL)",
            name="ck_source_fetches_terminal_shape",
        ),
        schema="research",
    )
    op.create_index(
        "ix_source_fetches_tenant_source_created",
        "source_fetches",
        ["tenant_id", "source_id", "created_at"],
        schema="research",
    )
    op.create_index(
        "uq_source_fetches_one_active",
        "source_fetches",
        ["tenant_id", "source_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','fetching')"),
        schema="research",
    )
    op.create_table(
        "evidence_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_fetch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("final_url", sa.String(2048), nullable=False),
        sa.Column("title", sa.String(500)),
        sa.Column("content_blocks", postgresql.JSONB(), nullable=False),
        sa.Column("outbound_links", postgresql.JSONB(), nullable=False),
        sa.Column("structured_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("raw_digest", sa.String(71), nullable=False),
        sa.Column("semantic_digest", sa.String(71), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("extractor_version", sa.String(64), nullable=False),
        sa.Column(
            "instruction_like_content", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["catalog.products.tenant_id", "catalog.products.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["research.sources.tenant_id", "research.sources.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_fetch_id"],
            ["research.source_fetches.tenant_id", "research.source_fetches.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_evidence_snapshots_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "source_fetch_id", name="uq_evidence_snapshots_fetch"),
        sa.CheckConstraint(
            "jsonb_typeof(content_blocks) = 'array' AND jsonb_array_length(content_blocks) BETWEEN 1 AND 500",
            name="ck_evidence_blocks",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(outbound_links) = 'array' AND jsonb_array_length(outbound_links) <= 200",
            name="ck_evidence_links",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(structured_metadata) = 'object'", name="ck_evidence_metadata"
        ),
        sa.CheckConstraint(
            "raw_digest ~ '^sha256:[0-9a-f]{64}$' AND semantic_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_evidence_digests",
        ),
        sa.CheckConstraint(
            "schema_version = 1 AND extractor_version <> ''", name="ck_evidence_version"
        ),
        schema="research",
    )
    op.create_index(
        "ix_evidence_tenant_product_captured",
        "evidence_snapshots",
        ["tenant_id", "product_id", "captured_at"],
        schema="research",
    )
    op.create_index(
        "ix_evidence_tenant_source_captured",
        "evidence_snapshots",
        ["tenant_id", "source_id", "captured_at"],
        schema="research",
    )
    op.create_index(
        "ix_evidence_tenant_semantic_digest",
        "evidence_snapshots",
        ["tenant_id", "semantic_digest"],
        schema="research",
    )
    op.create_foreign_key(
        "fk_source_fetches_evidence",
        "source_fetches",
        "evidence_snapshots",
        ["tenant_id", "evidence_snapshot_id"],
        ["tenant_id", "id"],
        source_schema="research",
        referent_schema="research",
        ondelete="RESTRICT",
    )
    op.execute(
        "CREATE FUNCTION research.protect_source_fetch() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF (NEW.tenant_id,NEW.source_id,NEW.requested_url,NEW.requested_by,NEW.raw_object_key,NEW.created_at) IS DISTINCT FROM (OLD.tenant_id,OLD.source_id,OLD.requested_url,OLD.requested_by,OLD.raw_object_key,OLD.created_at) THEN RAISE EXCEPTION 'fetch identity is immutable'; END IF; IF OLD.status <> NEW.status AND NOT ((OLD.status = 'pending' AND NEW.status IN ('fetching','rejected','failed')) OR (OLD.status = 'fetching' AND NEW.status IN ('succeeded','rejected','failed'))) THEN RAISE EXCEPTION 'invalid fetch lifecycle transition'; END IF; IF OLD.status IN ('succeeded','rejected','failed') AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'terminal fetch is immutable'; END IF; RETURN NEW; END; $$"
    )
    op.execute(
        "CREATE TRIGGER protect_source_fetch BEFORE UPDATE ON research.source_fetches FOR EACH ROW EXECUTE FUNCTION research.protect_source_fetch()"
    )
    op.execute("REVOKE ALL ON FUNCTION research.protect_source_fetch() FROM PUBLIC")
    op.execute(
        "CREATE FUNCTION research.protect_source_identity() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF (NEW.tenant_id,NEW.product_id,NEW.source_type,NEW.canonical_url,NEW.category,NEW.created_by,NEW.created_at) IS DISTINCT FROM (OLD.tenant_id,OLD.product_id,OLD.source_type,OLD.canonical_url,OLD.category,OLD.created_by,OLD.created_at) THEN RAISE EXCEPTION 'source identity is immutable'; END IF; IF OLD.status = 'archived' AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'archived source is immutable'; END IF; IF OLD.status <> NEW.status AND NOT (OLD.status = 'active' AND NEW.status = 'archived') THEN RAISE EXCEPTION 'invalid source lifecycle transition'; END IF; RETURN NEW; END; $$"
    )
    op.execute(
        "CREATE TRIGGER protect_source_identity BEFORE UPDATE ON research.sources FOR EACH ROW EXECUTE FUNCTION research.protect_source_identity()"
    )
    op.execute("REVOKE ALL ON FUNCTION research.protect_source_identity() FROM PUBLIC")
    _secure("sources", "SELECT, INSERT, UPDATE")
    _secure("source_fetches", "SELECT, INSERT, UPDATE")
    _secure("evidence_snapshots", "SELECT, INSERT")


def downgrade() -> None:
    count = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT (SELECT count(*) FROM research.sources) + (SELECT count(*) FROM research.source_fetches) + (SELECT count(*) FROM research.evidence_snapshots)"
            )
        )
        .scalar_one()
    )
    if count:
        raise RuntimeError("refusing lossy research evidence downgrade while data exists")
    op.drop_constraint(
        "fk_source_fetches_evidence", "source_fetches", schema="research", type_="foreignkey"
    )
    op.drop_table("evidence_snapshots", schema="research")
    op.execute("DROP FUNCTION research.protect_source_identity() CASCADE")
    op.execute("DROP FUNCTION research.protect_source_fetch() CASCADE")
    op.drop_table("source_fetches", schema="research")
    op.drop_table("sources", schema="research")
    op.execute("DROP SCHEMA research")
