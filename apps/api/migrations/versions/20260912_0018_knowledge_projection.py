# ruff: noqa: E501
"""Add disposable tenant-isolated human knowledge graph projection.

Revision ID: 20260912_0018
Revises: 20260912_0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260912_0018"
down_revision: str | None = "20260912_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME = "creative_marketer_runtime"
MIGRATOR = "creative_marketer_migrator"
TENANT = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute("CREATE SCHEMA knowledge_projection")
    op.execute(f"GRANT USAGE ON SCHEMA knowledge_projection TO {RUNTIME}, {MIGRATOR}")
    op.create_table(
        "projection_nodes",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_key", sa.String(340), nullable=False),
        sa.Column("node_type", sa.String(64), nullable=False),
        sa.Column("canonical_id", sa.String(256), nullable=False),
        sa.Column("projection_digest", sa.String(71), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "node_key"),
        sa.ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "node_type ~ '^[a-z][a-z0-9_]{0,63}$' AND length(canonical_id) BETWEEN 1 AND 256",
            name="ck_projection_nodes_identity",
        ),
        sa.CheckConstraint(
            "projection_digest ~ '^sha256:[0-9a-f]{64}$' AND jsonb_typeof(payload)='object' AND octet_length(payload::text)<=262144",
            name="ck_projection_nodes_payload",
        ),
        schema="knowledge_projection",
    )
    op.create_table(
        "projection_changes",
        sa.Column("revision", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_key", sa.String(340), nullable=False),
        sa.Column("node_type", sa.String(64), nullable=False),
        sa.Column("canonical_id", sa.String(256), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("payload", postgresql.JSONB()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("revision"),
        sa.ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "operation IN ('upsert','delete') AND ((operation='upsert' AND payload IS NOT NULL) OR (operation='delete' AND payload IS NULL))",
            name="ck_projection_changes_operation",
        ),
        sa.CheckConstraint(
            "payload IS NULL OR (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=262144)",
            name="ck_projection_changes_payload",
        ),
        schema="knowledge_projection",
    )
    op.create_index(
        "ix_projection_changes_tenant_revision",
        "projection_changes",
        ["tenant_id", "revision"],
        schema="knowledge_projection",
    )
    for table in ("projection_nodes", "projection_changes"):
        op.execute(f"ALTER TABLE knowledge_projection.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE knowledge_projection.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_migration_control ON knowledge_projection.{table} FOR ALL TO {MIGRATOR} USING (true) WITH CHECK (true)"
        )
        op.execute(
            f"CREATE POLICY {table}_runtime_tenant ON knowledge_projection.{table} FOR ALL TO {RUNTIME} USING (tenant_id = {TENANT}) WITH CHECK (tenant_id = {TENANT})"
        )
        op.execute(f"REVOKE ALL ON knowledge_projection.{table} FROM PUBLIC, {RUNTIME}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON knowledge_projection.projection_nodes TO {RUNTIME}"
    )
    op.execute(f"GRANT SELECT, INSERT ON knowledge_projection.projection_changes TO {RUNTIME}")
    op.execute(
        f"GRANT USAGE, SELECT ON SEQUENCE knowledge_projection.projection_changes_revision_seq TO {RUNTIME}"
    )
    op.execute(
        "CREATE FUNCTION knowledge_projection.protect_projection_changes() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'projection change history is immutable'; END; $$"
    )
    op.execute(
        "CREATE TRIGGER protect_projection_changes BEFORE UPDATE OR DELETE ON knowledge_projection.projection_changes FOR EACH ROW EXECUTE FUNCTION knowledge_projection.protect_projection_changes()"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION knowledge_projection.protect_projection_changes() FROM PUBLIC"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION knowledge_projection.protect_projection_changes() CASCADE")
    op.drop_table("projection_changes", schema="knowledge_projection")
    op.drop_table("projection_nodes", schema="knowledge_projection")
    op.execute("DROP SCHEMA knowledge_projection")
