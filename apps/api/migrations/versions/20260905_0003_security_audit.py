"""Create append-only security audit foundation.

Revision ID: 20260905_0003
Revises: 20260905_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0003"
down_revision: str | None = "20260905_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME_ROLE = "creative_marketer_runtime"
TENANT_EXPRESSION = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.execute("CREATE SCHEMA audit")
    op.create_table(
        "audit_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_kind", sa.String(32), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True)),
        sa.Column("actor_kind", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(160)),
        sa.Column("action", sa.String(160), nullable=False),
        sa.Column("resource_type", sa.String(100)),
        sa.Column("resource_id", sa.String(200)),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(100)),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("causation_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("policy_version", sa.String(100)),
        sa.Column("tool_name", sa.String(160)),
        sa.Column("tool_version", sa.String(100)),
        sa.Column("agent_definition_id", postgresql.UUID(as_uuid=True)),
        sa.Column("agent_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("before_digest", sa.String(80)),
        sa.Column("after_digest", sa.String(80)),
        sa.Column("safe_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("audit_schema_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "(scope_kind = 'platform' AND tenant_id IS NULL) OR "
            "(scope_kind = 'tenant' AND tenant_id IS NOT NULL)",
            name="ck_audit_records_audit_scope_tenant",
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'denied', 'failed', 'error')",
            name="ck_audit_records_audit_outcome",
        ),
        sa.CheckConstraint(
            "actor_kind IN ('user', 'workload', 'system', 'agent', "
            "'external_principal', 'anonymous')",
            name="ck_audit_records_audit_actor_kind",
        ),
        sa.CheckConstraint(
            "audit_schema_version >= 1",
            name="ck_audit_records_audit_schema_version",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(safe_metadata) = 'object' AND octet_length(safe_metadata::text) <= 8192",
            name="ck_audit_records_safe_metadata_shape_size",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_records"),
        schema="audit",
    )
    op.create_index(
        "ix_audit_records_tenant_occurred",
        "audit_records",
        ["tenant_id", "occurred_at"],
        schema="audit",
    )
    op.create_index(
        "ix_audit_records_actor_occurred",
        "audit_records",
        ["actor_id", "occurred_at"],
        schema="audit",
    )
    op.create_index(
        "ix_audit_records_correlation", "audit_records", ["correlation_id"], schema="audit"
    )
    op.create_index(
        "ix_audit_records_action_occurred",
        "audit_records",
        ["action", "occurred_at"],
        schema="audit",
    )
    op.create_index(
        "ix_audit_records_resource",
        "audit_records",
        ["resource_type", "resource_id"],
        schema="audit",
    )
    op.execute("ALTER TABLE audit.audit_records ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit.audit_records FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY audit_append_scope ON audit.audit_records FOR INSERT TO {RUNTIME_ROLE} "
        "WITH CHECK ((scope_kind = 'platform' AND tenant_id IS NULL) OR "
        f"(scope_kind = 'tenant' AND tenant_id = {TENANT_EXPRESSION}))"
    )
    op.execute(f"REVOKE ALL ON SCHEMA audit FROM {RUNTIME_ROLE}")
    op.execute(f"REVOKE ALL ON audit.audit_records FROM {RUNTIME_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA audit TO {RUNTIME_ROLE}")
    op.execute(f"GRANT INSERT ON audit.audit_records TO {RUNTIME_ROLE}")


def downgrade() -> None:
    op.drop_index("ix_audit_records_resource", table_name="audit_records", schema="audit")
    op.drop_index("ix_audit_records_action_occurred", table_name="audit_records", schema="audit")
    op.drop_index("ix_audit_records_correlation", table_name="audit_records", schema="audit")
    op.drop_index("ix_audit_records_actor_occurred", table_name="audit_records", schema="audit")
    op.drop_index("ix_audit_records_tenant_occurred", table_name="audit_records", schema="audit")
    op.drop_table("audit_records", schema="audit")
    op.execute("DROP SCHEMA audit")
