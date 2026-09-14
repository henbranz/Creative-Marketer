# ruff: noqa: E501
"""Add approval-bound governed social publishing.

Revision ID: 20260914_0023
Revises: 20260913_0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260914_0023"
down_revision: str | None = "20260913_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME = "creative_marketer_runtime"
MIGRATOR = "creative_marketer_migrator"
TENANT = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def _identity() -> list[sa.Column[object]]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
    ]


def _tenant_fk() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT")


def _secure(table: str, permissions: str) -> None:
    op.execute(f"ALTER TABLE publishing.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE publishing.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_migration_control ON publishing.{table} FOR ALL TO {MIGRATOR} USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY {table}_runtime_tenant ON publishing.{table} FOR ALL TO {RUNTIME} USING (tenant_id = {TENANT}) WITH CHECK (tenant_id = {TENANT})"
    )
    op.execute(f"REVOKE ALL ON publishing.{table} FROM PUBLIC, {RUNTIME}")
    op.execute(f"GRANT {permissions} ON publishing.{table} TO {RUNTIME}")


def _immutable(table: str) -> None:
    op.execute(
        f"CREATE FUNCTION publishing.protect_{table}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION '{table} is immutable'; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER protect_{table} BEFORE UPDATE OR DELETE ON publishing.{table} FOR EACH ROW EXECUTE FUNCTION publishing.protect_{table}()"
    )
    op.execute(f"REVOKE ALL ON FUNCTION publishing.protect_{table}() FROM PUBLIC")
    _secure(table, "SELECT, INSERT")


def upgrade() -> None:
    op.execute("CREATE SCHEMA publishing")
    op.execute(f"GRANT USAGE ON SCHEMA publishing TO {RUNTIME}, {MIGRATOR}")
    op.create_table(
        "social_accounts",
        *_identity(),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("external_account_id", sa.String(256), nullable=False),
        sa.Column("username", sa.String(200)),
        sa.Column("account_type", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "capabilities",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _tenant_fk(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_social_accounts_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "platform", "external_account_id", name="uq_social_accounts_destination"
        ),
        sa.CheckConstraint(
            "platform IN ('facebook','instagram','tiktok') AND status IN ('ACTIVE','DISCONNECTED','REQUIRES_REAUTH','ARCHIVED') AND provider='fake' AND length(btrim(display_name))>0 AND length(btrim(external_account_id))>0",
            name="ck_social_accounts_values",
        ),
        schema="publishing",
    )
    op.create_table(
        "publication_drafts",
        *_identity(),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("final_creative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("final_creative_digest", sa.String(71), nullable=False),
        sa.Column("output_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("output_asset_digest", sa.String(71), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("social_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_destination_id", sa.String(256), nullable=False),
        sa.Column("caption", sa.String(5000), nullable=False),
        sa.Column("title", sa.String(500)),
        sa.Column(
            "hashtags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("destination_url", sa.String(2000)),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column(
            "platform_settings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "social_account_id"],
            ["publishing.social_accounts.tenant_id", "publishing.social_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_publication_drafts_tenant_id_id"),
        sa.CheckConstraint(
            "schema_version=1 AND platform IN ('facebook','instagram','tiktok') AND mode IN ('POST_NOW','SCHEDULE') AND ((mode='POST_NOW' AND scheduled_at IS NULL) OR (mode='SCHEDULE' AND scheduled_at IS NOT NULL)) AND length(btrim(caption))>0 AND final_creative_digest ~ '^sha256:[0-9a-f]{64}$' AND output_asset_digest ~ '^sha256:[0-9a-f]{64}$' AND semantic_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_publication_drafts_values",
        ),
        schema="publishing",
    )
    op.create_table(
        "publication_decisions",
        *_identity(),
        sa.Column("publication_draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publication_draft_digest", sa.String(71), nullable=False),
        sa.Column("final_creative_digest", sa.String(71), nullable=False),
        sa.Column("output_asset_digest", sa.String(71), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("social_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_destination_id", sa.String(256), nullable=False),
        sa.Column("caption_digest", sa.String(71), nullable=False),
        sa.Column("platform_settings_digest", sa.String(71), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("action_digest", sa.String(71), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _tenant_fk(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "publication_draft_id"],
            ["publishing.publication_drafts.tenant_id", "publishing.publication_drafts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "social_account_id"],
            ["publishing.social_accounts.tenant_id", "publishing.social_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_publication_decisions_tenant_id_id"),
        sa.CheckConstraint(
            "state IN ('APPROVED','REJECTED') AND publication_draft_digest ~ '^sha256:[0-9a-f]{64}$' AND final_creative_digest ~ '^sha256:[0-9a-f]{64}$' AND output_asset_digest ~ '^sha256:[0-9a-f]{64}$' AND caption_digest ~ '^sha256:[0-9a-f]{64}$' AND platform_settings_digest ~ '^sha256:[0-9a-f]{64}$' AND action_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_publication_decisions_values",
        ),
        schema="publishing",
    )
    op.create_table(
        "publication_jobs",
        *_identity(),
        sa.Column("publication_draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_id", sa.String(35), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("external_operation_id", sa.String(256)),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        _tenant_fk(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "publication_draft_id"],
            ["publishing.publication_drafts.tenant_id", "publishing.publication_drafts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["identity.users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_publication_jobs_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "publication_draft_id", name="uq_publication_jobs_draft"),
        sa.UniqueConstraint("tenant_id", "operation_id", name="uq_publication_jobs_operation"),
        sa.CheckConstraint(
            "operation_id ~ '^op_[0-9a-f]{32}$' AND status IN ('APPROVED','SCHEDULED','SUBMITTING','SUBMITTED','PUBLISHED','FAILED','OUTCOME_UNKNOWN','CANCELLED')",
            name="ck_publication_jobs_values",
        ),
        schema="publishing",
    )
    op.create_table(
        "publications",
        *_identity(),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publication_draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("final_creative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("output_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("social_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_post_id", sa.String(256), nullable=False),
        sa.Column("external_operation_id", sa.String(256)),
        sa.Column("canonical_permalink", sa.String(2000)),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("connector_version", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("response_metadata_digest", sa.String(71)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
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
            ["tenant_id", "publication_draft_id"],
            ["publishing.publication_drafts.tenant_id", "publishing.publication_drafts.id"],
            ondelete="RESTRICT",
        ),
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "social_account_id"],
            ["publishing.social_accounts.tenant_id", "publishing.social_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_publications_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "publication_draft_id", name="uq_publications_draft"),
        sa.UniqueConstraint(
            "tenant_id",
            "platform",
            "social_account_id",
            "external_post_id",
            name="uq_publications_external_post",
        ),
        sa.CheckConstraint(
            "status='PUBLISHED' AND provider='fake' AND request_digest ~ '^sha256:[0-9a-f]{64}$' AND semantic_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_publications_values",
        ),
        schema="publishing",
    )
    for table in ("publication_drafts", "publication_decisions", "publications"):
        _immutable(table)
    _secure("social_accounts", "SELECT, INSERT, UPDATE")
    op.execute("""
        CREATE FUNCTION publishing.protect_social_accounts() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' OR NEW.id<>OLD.id OR NEW.tenant_id<>OLD.tenant_id OR NEW.platform<>OLD.platform OR NEW.external_account_id<>OLD.external_account_id OR NEW.provider<>OLD.provider OR NEW.created_at<>OLD.created_at THEN
            RAISE EXCEPTION 'social account identity is immutable';
          END IF;
          RETURN NEW;
        END; $$
    """)
    op.execute(
        "CREATE TRIGGER protect_social_accounts BEFORE UPDATE OR DELETE ON publishing.social_accounts FOR EACH ROW EXECUTE FUNCTION publishing.protect_social_accounts()"
    )
    op.execute("REVOKE ALL ON FUNCTION publishing.protect_social_accounts() FROM PUBLIC")
    _secure("publication_jobs", "SELECT, INSERT, UPDATE")
    op.execute("""
        CREATE FUNCTION publishing.protect_publication_jobs() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' OR NEW.id<>OLD.id OR NEW.tenant_id<>OLD.tenant_id OR NEW.publication_draft_id<>OLD.publication_draft_id OR NEW.operation_id<>OLD.operation_id OR NEW.created_by_user_id<>OLD.created_by_user_id OR NEW.created_at<>OLD.created_at THEN RAISE EXCEPTION 'publication job identity is immutable'; END IF;
          IF NOT ((OLD.status='APPROVED' AND NEW.status IN ('SCHEDULED','SUBMITTING','CANCELLED')) OR (OLD.status='SCHEDULED' AND NEW.status IN ('SUBMITTING','CANCELLED')) OR (OLD.status='SUBMITTING' AND NEW.status IN ('SUBMITTED','PUBLISHED','FAILED','OUTCOME_UNKNOWN')) OR (OLD.status='SUBMITTED' AND NEW.status IN ('PUBLISHED','FAILED','OUTCOME_UNKNOWN','CANCELLED')) OR (OLD.status='OUTCOME_UNKNOWN' AND NEW.status IN ('PUBLISHED','FAILED'))) THEN RAISE EXCEPTION 'invalid publication job transition'; END IF;
          RETURN NEW;
        END; $$
    """)
    op.execute(
        "CREATE TRIGGER protect_publication_jobs BEFORE UPDATE OR DELETE ON publishing.publication_jobs FOR EACH ROW EXECUTE FUNCTION publishing.protect_publication_jobs()"
    )
    op.execute("REVOKE ALL ON FUNCTION publishing.protect_publication_jobs() FROM PUBLIC")


def downgrade() -> None:
    for table in (
        "publication_jobs",
        "social_accounts",
        "publications",
        "publication_decisions",
        "publication_drafts",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS publishing.protect_{table}() CASCADE")
    for table in (
        "publications",
        "publication_jobs",
        "publication_decisions",
        "publication_drafts",
        "social_accounts",
    ):
        op.drop_table(table, schema="publishing")
    op.execute("DROP SCHEMA publishing")
