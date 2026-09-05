from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from creative_marketer.infrastructure.database.schema import metadata

outbox_events = Table(
    "outbox_events",
    metadata,
    Column("event_id", UUID(as_uuid=True), primary_key=True),
    Column("event_type", String(160), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("scope_kind", String(16), nullable=False),
    Column("tenant_id", UUID(as_uuid=True)),
    Column("aggregate_type", String(100), nullable=False),
    Column("aggregate_id", UUID(as_uuid=True), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("actor_kind", String(32), nullable=False),
    Column("actor_id", UUID(as_uuid=True), nullable=False),
    Column("agent_definition_id", UUID(as_uuid=True)),
    Column("agent_version_id", UUID(as_uuid=True)),
    Column("agent_run_id", UUID(as_uuid=True)),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("causation_id", UUID(as_uuid=True)),
    Column("payload", JSONB, nullable=False),
    Column("payload_schema_digest", String(71), nullable=False),
    Column("event_digest", String(71), nullable=False),
    Column("canonicalization_version", Integer, nullable=False),
    Column("publication_state", String(32), nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("lease_owner", UUID(as_uuid=True)),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("published_at", DateTime(timezone=True)),
    Column("last_error_code", String(100)),
    Column("last_error_digest", String(71)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "(scope_kind = 'tenant' AND tenant_id IS NOT NULL) OR "
        "(scope_kind = 'platform' AND tenant_id IS NULL)",
        name="scope_tenant",
    ),
    CheckConstraint("schema_version >= 1", name="schema_version"),
    CheckConstraint("canonicalization_version = 1", name="canonicalization_version"),
    CheckConstraint("attempt_count >= 0", name="attempt_count"),
    CheckConstraint(
        "jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 32768",
        name="payload_shape_size",
    ),
    CheckConstraint(
        "payload_schema_digest ~ '^sha256:[0-9a-f]{64}$' AND "
        "event_digest ~ '^sha256:[0-9a-f]{64}$'",
        name="digests",
    ),
    CheckConstraint(
        "last_error_digest IS NULL OR last_error_digest ~ '^sha256:[0-9a-f]{64}$'",
        name="error_digest",
    ),
    CheckConstraint(
        "last_error_code IS NULL OR last_error_code ~ '^[A-Z][A-Z0-9_]{0,99}$'",
        name="error_code",
    ),
    CheckConstraint(
        "publication_state IN ('PENDING','PUBLISHING','PUBLISHED','FAILED_TERMINAL')",
        name="publication_state",
    ),
    CheckConstraint(
        "(publication_state = 'PUBLISHING') = "
        "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
        name="lease_state",
    ),
    CheckConstraint(
        "(publication_state = 'PUBLISHED') = (published_at IS NOT NULL)", name="published_state"
    ),
    schema="event_delivery",
)
Index("ix_outbox_ready", outbox_events.c.publication_state, outbox_events.c.next_attempt_at)
Index("ix_outbox_lease", outbox_events.c.lease_expires_at)
Index("ix_outbox_tenant_created", outbox_events.c.tenant_id, outbox_events.c.created_at)
Index("ix_outbox_type_created", outbox_events.c.event_type, outbox_events.c.created_at)
Index("ix_outbox_correlation", outbox_events.c.correlation_id)

inbox_receipts = Table(
    "inbox_receipts",
    metadata,
    Column("consumer_name", String(160), primary_key=True),
    Column("event_id", UUID(as_uuid=True), primary_key=True),
    Column("event_digest", String(71), nullable=False),
    Column("event_type", String(160), nullable=False),
    Column("scope_kind", String(16), nullable=False),
    Column("tenant_id", UUID(as_uuid=True)),
    Column("handler_version", String(100), nullable=False),
    Column("processed_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "(scope_kind = 'tenant' AND tenant_id IS NOT NULL) OR "
        "(scope_kind = 'platform' AND tenant_id IS NULL)",
        name="scope_tenant",
    ),
    CheckConstraint("event_digest ~ '^sha256:[0-9a-f]{64}$'", name="event_digest"),
    UniqueConstraint("tenant_id", "consumer_name", "event_id"),
    schema="event_delivery",
)
Index("ix_inbox_tenant_processed", inbox_receipts.c.tenant_id, inbox_receipts.c.processed_at)
Index("ix_inbox_type_processed", inbox_receipts.c.event_type, inbox_receipts.c.processed_at)
