"""Add immutable asynchronous trace delivery metadata.

Revision ID: 20260905_0010
Revises: 20260905_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_0010"
down_revision: str | None = "20260905_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("outbox_events", sa.Column("traceparent", sa.String(55)), schema="event_delivery")
    op.add_column("outbox_events", sa.Column("tracestate", sa.String(512)), schema="event_delivery")
    op.create_index(
        "ix_outbox_state_created",
        "outbox_events",
        ["publication_state", "created_at"],
        schema="event_delivery",
    )
    op.create_check_constraint(
        "ck_outbox_traceparent",
        "outbox_events",
        "traceparent IS NULL OR traceparent ~ '^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$'",
        schema="event_delivery",
    )
    op.create_check_constraint(
        "ck_outbox_traceparent_nonzero",
        "outbox_events",
        "traceparent IS NULL OR (substring(traceparent from 4 for 32) <> repeat('0', 32) "
        "AND substring(traceparent from 37 for 16) <> repeat('0', 16))",
        schema="event_delivery",
    )
    op.execute("""
    CREATE FUNCTION event_delivery.protect_outbox_trace_context() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
      IF (NEW.traceparent, NEW.tracestate) IS DISTINCT FROM (OLD.traceparent, OLD.tracestate)
      THEN RAISE EXCEPTION 'Outbox event content is immutable'; END IF;
      RETURN NEW;
    END; $$
    """)
    op.execute(
        "CREATE TRIGGER protect_outbox_trace_context BEFORE UPDATE ON "
        "event_delivery.outbox_events FOR EACH ROW EXECUTE FUNCTION "
        "event_delivery.protect_outbox_trace_context()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER protect_outbox_trace_context ON event_delivery.outbox_events")
    op.execute("DROP FUNCTION event_delivery.protect_outbox_trace_context()")
    op.drop_index("ix_outbox_state_created", table_name="outbox_events", schema="event_delivery")
    op.drop_constraint(
        "ck_outbox_traceparent_nonzero",
        "outbox_events",
        schema="event_delivery",
        type_="check",
    )
    op.drop_constraint(
        "ck_outbox_traceparent", "outbox_events", schema="event_delivery", type_="check"
    )
    op.drop_column("outbox_events", "tracestate", schema="event_delivery")
    op.drop_column("outbox_events", "traceparent", schema="event_delivery")
