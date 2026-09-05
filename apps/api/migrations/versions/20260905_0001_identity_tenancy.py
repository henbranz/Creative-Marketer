"""Create identity and tenancy foundation.

Revision ID: 20260905_0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME_ROLE = "creative_marketer_runtime"
TENANT_EXPRESSION = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    connection = op.get_bind()
    role_exists = connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"),
        {"role": RUNTIME_ROLE},
    ).scalar_one()
    if not role_exists:
        raise RuntimeError(
            "creative_marketer_runtime role is missing; run the documented role bootstrap"
        )

    op.execute("CREATE SCHEMA identity")
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "name = btrim(name) AND length(name) > 0", name="ck_tenants_tenant_name_normalized"
        ),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'", name="ck_tenants_tenant_slug_format"
        ),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="ck_tenants_tenant_status"),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
        schema="identity",
    )
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_identity_issuer", sa.String(500)),
        sa.Column("external_identity_subject", sa.String(500)),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("normalized_email", sa.String(320), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(external_identity_issuer IS NULL) = (external_identity_subject IS NULL)",
            name="ck_users_external_identity_pair",
        ),
        sa.CheckConstraint(
            "email = btrim(email) AND length(email) > 0", name="ck_users_user_email_trimmed"
        ),
        sa.CheckConstraint(
            "normalized_email = lower(btrim(email))", name="ck_users_normalized_email"
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_users_user_status"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("normalized_email", name="uq_users_normalized_email"),
        sa.UniqueConstraint(
            "external_identity_issuer",
            "external_identity_subject",
            name="uq_users_external_identity_issuer_external_identity_subject",
        ),
        schema="identity",
    )
    op.create_table(
        "memberships",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'member')", name="ck_memberships_membership_role"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')", name="ck_memberships_membership_status"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["identity.tenants.id"],
            name="fk_memberships_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["identity.users.id"],
            name="fk_memberships_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "user_id", name="pk_memberships"),
        schema="identity",
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"], schema="identity")

    op.execute("ALTER TABLE identity.tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE identity.tenants FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE identity.memberships ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE identity.memberships FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON identity.tenants FOR ALL TO {RUNTIME_ROLE} "
        f"USING (id = {TENANT_EXPRESSION}) WITH CHECK (id = {TENANT_EXPRESSION})"
    )
    op.execute(
        f"CREATE POLICY tenant_isolation ON identity.memberships FOR ALL TO {RUNTIME_ROLE} "
        f"USING (tenant_id = {TENANT_EXPRESSION}) "
        f"WITH CHECK (tenant_id = {TENANT_EXPRESSION})"
    )
    op.execute(f"GRANT USAGE ON SCHEMA identity TO {RUNTIME_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA identity TO {RUNTIME_ROLE}"
    )


def downgrade() -> None:
    op.drop_index("ix_memberships_user_id", table_name="memberships", schema="identity")
    op.drop_table("memberships", schema="identity")
    op.drop_table("users", schema="identity")
    op.drop_table("tenants", schema="identity")
    op.execute("DROP SCHEMA identity")
