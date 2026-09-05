"""Create transactional Outbox and idempotent Inbox delivery state.

Revision ID: 20260905_0008
Revises: 20260905_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0008"
down_revision: str | None = "20260905_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME = "creative_marketer_runtime"
PUBLISHER = "creative_marketer_event_publisher"
MIGRATOR = "creative_marketer_migrator"
TENANT = "nullif(current_setting('app.current_tenant_id', true), '')::uuid"


def _create_tables() -> None:
    op.execute("CREATE SCHEMA event_delivery")
    op.create_table(
        "outbox_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(160), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("scope_kind", sa.String(16), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True)),
        sa.Column("aggregate_type", sa.String(100), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_kind", sa.String(32), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_definition_id", postgresql.UUID(as_uuid=True)),
        sa.Column("agent_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("causation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_schema_digest", sa.String(71), nullable=False),
        sa.Column("event_digest", sa.String(71), nullable=False),
        sa.Column("canonicalization_version", sa.Integer(), nullable=False),
        sa.Column("publication_state", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", postgresql.UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_digest", sa.String(71)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(scope_kind = 'tenant' AND tenant_id IS NOT NULL) OR "
            "(scope_kind = 'platform' AND tenant_id IS NULL)",
            name="ck_outbox_events_scope_tenant",
        ),
        sa.CheckConstraint("schema_version >= 1", name="ck_outbox_events_schema_version"),
        sa.CheckConstraint(
            "event_type ~ ('\\.v' || schema_version::text || '$')",
            name="ck_outbox_events_type_version",
        ),
        sa.CheckConstraint(
            "actor_kind IN ('user','agent','workload','system')",
            name="ck_outbox_events_actor_kind",
        ),
        sa.CheckConstraint(
            "canonicalization_version = 1",
            name="ck_outbox_events_canonicalization_version",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_outbox_events_attempt_count"),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 32768",
            name="ck_outbox_events_payload_shape_size",
        ),
        sa.CheckConstraint(
            "payload_schema_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "event_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_outbox_events_digests",
        ),
        sa.CheckConstraint(
            "last_error_digest IS NULL OR last_error_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_outbox_events_error_digest",
        ),
        sa.CheckConstraint(
            "last_error_code IS NULL OR last_error_code ~ '^[A-Z][A-Z0-9_]{0,99}$'",
            name="ck_outbox_events_error_code",
        ),
        sa.CheckConstraint(
            "publication_state IN ('PENDING','PUBLISHING','PUBLISHED','FAILED_TERMINAL')",
            name="ck_outbox_events_publication_state",
        ),
        sa.CheckConstraint(
            "(publication_state = 'PUBLISHING') = "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_outbox_events_lease_state",
        ),
        sa.CheckConstraint(
            "(publication_state = 'PUBLISHED') = (published_at IS NOT NULL)",
            name="ck_outbox_events_published_state",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("event_id", name="pk_outbox_events"),
        schema="event_delivery",
    )
    op.create_index(
        "ix_outbox_ready",
        "outbox_events",
        ["publication_state", "next_attempt_at"],
        schema="event_delivery",
    )
    for name, columns in (
        ("ix_outbox_lease", ["lease_expires_at"]),
        ("ix_outbox_tenant_created", ["tenant_id", "created_at"]),
        ("ix_outbox_type_created", ["event_type", "created_at"]),
        ("ix_outbox_correlation", ["correlation_id"]),
    ):
        op.create_index(name, "outbox_events", columns, schema="event_delivery")
    op.create_table(
        "inbox_receipts",
        sa.Column("consumer_name", sa.String(160), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_digest", sa.String(71), nullable=False),
        sa.Column("event_type", sa.String(160), nullable=False),
        sa.Column("scope_kind", sa.String(16), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True)),
        sa.Column("handler_version", sa.String(100), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(scope_kind = 'tenant' AND tenant_id IS NOT NULL) OR "
            "(scope_kind = 'platform' AND tenant_id IS NULL)",
            name="ck_inbox_receipts_scope_tenant",
        ),
        sa.CheckConstraint(
            "event_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_inbox_receipts_event_digest",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["identity.tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("consumer_name", "event_id", name="pk_inbox_receipts"),
        schema="event_delivery",
    )
    op.create_index(
        "ix_inbox_tenant_processed",
        "inbox_receipts",
        ["tenant_id", "processed_at"],
        schema="event_delivery",
    )
    op.create_index(
        "ix_inbox_type_processed",
        "inbox_receipts",
        ["event_type", "processed_at"],
        schema="event_delivery",
    )


def _immutability_and_transitions() -> None:
    op.execute(
        """
        CREATE FUNCTION event_delivery.protect_outbox_event()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (NEW.event_id, NEW.event_type, NEW.schema_version, NEW.scope_kind, NEW.tenant_id,
              NEW.aggregate_type, NEW.aggregate_id, NEW.occurred_at, NEW.actor_kind, NEW.actor_id,
              NEW.agent_definition_id, NEW.agent_version_id, NEW.agent_run_id,
              NEW.correlation_id, NEW.causation_id, NEW.payload, NEW.payload_schema_digest,
              NEW.event_digest, NEW.canonicalization_version, NEW.created_at)
             IS DISTINCT FROM
             (OLD.event_id, OLD.event_type, OLD.schema_version, OLD.scope_kind, OLD.tenant_id,
              OLD.aggregate_type, OLD.aggregate_id, OLD.occurred_at, OLD.actor_kind, OLD.actor_id,
              OLD.agent_definition_id, OLD.agent_version_id, OLD.agent_run_id,
              OLD.correlation_id, OLD.causation_id, OLD.payload, OLD.payload_schema_digest,
              OLD.event_digest, OLD.canonicalization_version, OLD.created_at)
          THEN RAISE EXCEPTION 'outbox event content is immutable'; END IF;
          IF NOT (
            (OLD.publication_state = 'PENDING' AND NEW.publication_state = 'PUBLISHING'
              AND NEW.attempt_count = OLD.attempt_count + 1) OR
            (OLD.publication_state = 'PUBLISHING' AND NEW.publication_state = 'PUBLISHING'
              AND OLD.lease_expires_at <= clock_timestamp()
              AND NEW.attempt_count = OLD.attempt_count + 1) OR
            (OLD.publication_state = 'PUBLISHING'
              AND NEW.publication_state IN ('PENDING','PUBLISHED','FAILED_TERMINAL')
              AND NEW.attempt_count = OLD.attempt_count)
          ) THEN RAISE EXCEPTION 'invalid outbox publication transition'; END IF;
          RETURN NEW;
        END; $$
        """
    )
    op.execute(
        "CREATE TRIGGER protect_outbox_event BEFORE UPDATE ON event_delivery.outbox_events "
        "FOR EACH ROW EXECUTE FUNCTION event_delivery.protect_outbox_event()"
    )
    op.execute(
        "CREATE FUNCTION event_delivery.reject_inbox_mutation() RETURNS trigger LANGUAGE plpgsql "
        "AS $$ BEGIN RAISE EXCEPTION 'inbox receipt is immutable'; END; $$"
    )
    op.execute(
        "CREATE TRIGGER reject_inbox_mutation BEFORE UPDATE OR DELETE ON "
        "event_delivery.inbox_receipts FOR EACH ROW EXECUTE FUNCTION "
        "event_delivery.reject_inbox_mutation()"
    )


def _rls_and_privileges() -> None:
    op.execute(f"REVOKE ALL ON SCHEMA event_delivery FROM {RUNTIME}, {PUBLISHER}")
    op.execute(f"GRANT USAGE ON SCHEMA event_delivery TO {RUNTIME}, {PUBLISHER}")
    for table in ("outbox_events", "inbox_receipts"):
        op.execute(f"ALTER TABLE event_delivery.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE event_delivery.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_migration_control ON event_delivery.{table} FOR ALL "
            f"TO {MIGRATOR} USING (true) WITH CHECK (true)"
        )
        op.execute(f"REVOKE ALL ON event_delivery.{table} FROM {RUNTIME}, {PUBLISHER}")
    op.execute(
        "CREATE POLICY outbox_runtime_insert ON event_delivery.outbox_events FOR INSERT "
        f"TO {RUNTIME} WITH CHECK (scope_kind = 'tenant' AND tenant_id = {TENANT})"
    )
    op.execute(
        "CREATE POLICY outbox_publisher_select ON event_delivery.outbox_events FOR SELECT "
        f"TO {PUBLISHER} USING (true)"
    )
    op.execute(
        "CREATE POLICY outbox_publisher_update ON event_delivery.outbox_events FOR UPDATE "
        f"TO {PUBLISHER} USING (true) WITH CHECK (true)"
    )
    op.execute(f"GRANT INSERT ON event_delivery.outbox_events TO {RUNTIME}")
    op.execute(f"GRANT SELECT ON event_delivery.outbox_events TO {PUBLISHER}")
    op.execute(
        "GRANT UPDATE (publication_state, attempt_count, next_attempt_at, lease_owner, "
        "lease_expires_at, published_at, last_error_code, last_error_digest, updated_at) "
        f"ON event_delivery.outbox_events TO {PUBLISHER}"
    )
    op.execute(
        "CREATE POLICY inbox_runtime_read ON event_delivery.inbox_receipts FOR SELECT "
        f"TO {RUNTIME} USING (scope_kind = 'tenant' AND tenant_id = {TENANT})"
    )
    op.execute(
        "CREATE POLICY inbox_runtime_insert ON event_delivery.inbox_receipts FOR INSERT "
        f"TO {RUNTIME} WITH CHECK (scope_kind = 'tenant' AND tenant_id = {TENANT})"
    )
    op.execute(f"GRANT SELECT, INSERT ON event_delivery.inbox_receipts TO {RUNTIME}")


def upgrade() -> None:
    _create_tables()
    _immutability_and_transitions()
    _rls_and_privileges()


def downgrade() -> None:
    connection = op.get_bind()
    count = connection.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM event_delivery.outbox_events) + "
            "(SELECT count(*) FROM event_delivery.inbox_receipts)"
        )
    ).scalar_one()
    if count:
        raise RuntimeError("refusing lossy event-delivery downgrade while history exists")
    op.drop_table("inbox_receipts", schema="event_delivery")
    op.drop_table("outbox_events", schema="event_delivery")
    op.execute("DROP FUNCTION event_delivery.reject_inbox_mutation()")
    op.execute("DROP FUNCTION event_delivery.protect_outbox_event()")
    op.execute("DROP SCHEMA event_delivery")
