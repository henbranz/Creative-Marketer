from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=NAMING_CONVENTION)

tenants = Table(
    "tenants",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("slug", String(100), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("name = btrim(name) AND length(name) > 0", name="tenant_name_normalized"),
    CheckConstraint("slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'", name="tenant_slug_format"),
    CheckConstraint("status IN ('active', 'suspended')", name="tenant_status"),
    UniqueConstraint("slug"),
    schema="identity",
)

users = Table(
    "users",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("external_identity_issuer", String(500)),
    Column("external_identity_subject", String(500)),
    Column("email", String(320), nullable=False),
    Column("normalized_email", String(320), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "(external_identity_issuer IS NULL) = (external_identity_subject IS NULL)",
        name="external_identity_pair",
    ),
    CheckConstraint("email = btrim(email) AND length(email) > 0", name="user_email_trimmed"),
    CheckConstraint("normalized_email = lower(btrim(email))", name="normalized_email"),
    CheckConstraint("status IN ('active', 'disabled')", name="user_status"),
    UniqueConstraint("normalized_email"),
    UniqueConstraint("external_identity_issuer", "external_identity_subject"),
    schema="identity",
)

memberships = Table(
    "memberships",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), primary_key=True),
    Column("role", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="CASCADE"),
    ForeignKeyConstraint(["user_id"], ["identity.users.id"], ondelete="CASCADE"),
    CheckConstraint("role IN ('owner', 'admin', 'member')", name="membership_role"),
    CheckConstraint("status IN ('active', 'inactive')", name="membership_status"),
    schema="identity",
)
Index("ix_memberships_user_id", memberships.c.user_id)
