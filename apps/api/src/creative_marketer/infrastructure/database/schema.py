from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

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
    Column("email", String(320), nullable=False),
    Column("normalized_email", String(320), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("email = btrim(email) AND length(email) > 0", name="user_email_trimmed"),
    CheckConstraint("normalized_email = lower(btrim(email))", name="normalized_email"),
    CheckConstraint("status IN ('active', 'disabled')", name="user_status"),
    UniqueConstraint("normalized_email"),
    schema="identity",
)

external_identities = Table(
    "external_identities",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("issuer", String(500), nullable=False),
    Column("subject", String(500), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(["user_id"], ["identity.users.id"], ondelete="CASCADE"),
    CheckConstraint("length(btrim(issuer)) > 0", name="external_identity_issuer_present"),
    CheckConstraint("length(subject) > 0", name="external_identity_subject_present"),
    CheckConstraint("status IN ('active', 'disabled')", name="external_identity_status"),
    UniqueConstraint("issuer", "subject"),
    schema="identity",
)
Index("ix_external_identities_user_id", external_identities.c.user_id)

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

audit_records = Table(
    "audit_records",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("scope_kind", String(32), nullable=False),
    Column("tenant_id", UUID(as_uuid=True)),
    Column("actor_kind", String(32), nullable=False),
    Column("actor_id", String(160)),
    Column("action", String(160), nullable=False),
    Column("resource_type", String(100)),
    Column("resource_id", String(200)),
    Column("outcome", String(32), nullable=False),
    Column("reason_code", String(100)),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("causation_id", UUID(as_uuid=True)),
    Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("environment", String(32), nullable=False),
    Column("policy_version", String(100)),
    Column("tool_name", String(160)),
    Column("tool_version", String(100)),
    Column("agent_definition_id", UUID(as_uuid=True)),
    Column("agent_version_id", UUID(as_uuid=True)),
    Column("agent_run_id", UUID(as_uuid=True)),
    Column("before_digest", String(80)),
    Column("after_digest", String(80)),
    Column("safe_metadata", JSONB, nullable=False),
    Column("audit_schema_version", Integer, nullable=False),
    CheckConstraint(
        "(scope_kind = 'platform' AND tenant_id IS NULL) OR "
        "(scope_kind = 'tenant' AND tenant_id IS NOT NULL)",
        name="audit_scope_tenant",
    ),
    CheckConstraint("outcome IN ('success', 'denied', 'failed', 'error')", name="audit_outcome"),
    CheckConstraint(
        "actor_kind IN ('user', 'workload', 'system', 'agent', 'external_principal', 'anonymous')",
        name="audit_actor_kind",
    ),
    CheckConstraint("audit_schema_version >= 1", name="audit_schema_version"),
    CheckConstraint(
        "jsonb_typeof(safe_metadata) = 'object' AND octet_length(safe_metadata::text) <= 8192",
        name="safe_metadata_shape_size",
    ),
    schema="audit",
)
Index("ix_audit_records_tenant_occurred", audit_records.c.tenant_id, audit_records.c.occurred_at)
Index("ix_audit_records_actor_occurred", audit_records.c.actor_id, audit_records.c.occurred_at)
Index("ix_audit_records_correlation", audit_records.c.correlation_id)
Index("ix_audit_records_action_occurred", audit_records.c.action, audit_records.c.occurred_at)
Index("ix_audit_records_resource", audit_records.c.resource_type, audit_records.c.resource_id)
