"""Extract external identities from users.

Revision ID: 20260905_0002
Revises: 20260905_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0002"
down_revision: str | None = "20260905_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issuer", sa.String(500), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "length(btrim(issuer)) > 0",
            name="ck_external_identities_external_identity_issuer_present",
        ),
        sa.CheckConstraint(
            "length(subject) > 0", name="ck_external_identities_external_identity_subject_present"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_external_identities_external_identity_status",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["identity.users.id"],
            name="fk_external_identities_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_external_identities"),
        sa.UniqueConstraint("issuer", "subject", name="uq_external_identities_issuer_subject"),
        schema="identity",
    )
    op.create_index(
        "ix_external_identities_user_id",
        "external_identities",
        ["user_id"],
        schema="identity",
    )
    op.execute(
        "INSERT INTO identity.external_identities "
        "(id, user_id, issuer, subject, status, created_at, updated_at) "
        "SELECT gen_random_uuid(), id, external_identity_issuer, external_identity_subject, "
        "'active', created_at, updated_at FROM identity.users "
        "WHERE external_identity_issuer IS NOT NULL AND external_identity_subject IS NOT NULL"
    )
    op.drop_constraint(
        "uq_users_external_identity_issuer_external_identity_subject",
        "users",
        schema="identity",
        type_="unique",
    )
    op.drop_constraint("ck_users_external_identity_pair", "users", schema="identity", type_="check")
    op.drop_column("users", "external_identity_subject", schema="identity")
    op.drop_column("users", "external_identity_issuer", schema="identity")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON identity.external_identities "
        "TO creative_marketer_runtime"
    )


def downgrade() -> None:
    connection = op.get_bind()
    multiple = connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM identity.external_identities "
            "GROUP BY user_id HAVING count(*) > 1)"
        )
    ).scalar_one()
    if multiple:
        raise RuntimeError("downgrade would discard multiple external identities for one user")

    op.add_column("users", sa.Column("external_identity_issuer", sa.String(500)), schema="identity")
    op.add_column(
        "users", sa.Column("external_identity_subject", sa.String(500)), schema="identity"
    )
    op.execute(
        "UPDATE identity.users AS users SET "
        "external_identity_issuer = identities.issuer, "
        "external_identity_subject = identities.subject "
        "FROM identity.external_identities AS identities "
        "WHERE identities.user_id = users.id"
    )
    op.create_check_constraint(
        "ck_users_external_identity_pair",
        "users",
        "(external_identity_issuer IS NULL) = (external_identity_subject IS NULL)",
        schema="identity",
    )
    op.create_unique_constraint(
        "uq_users_external_identity_issuer_external_identity_subject",
        "users",
        ["external_identity_issuer", "external_identity_subject"],
        schema="identity",
    )
    op.drop_index(
        "ix_external_identities_user_id",
        table_name="external_identities",
        schema="identity",
    )
    op.drop_table("external_identities", schema="identity")
