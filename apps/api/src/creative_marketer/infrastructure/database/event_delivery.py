from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from creative_marketer.events.application import (
    ClaimedEvent,
    InboxReservation,
    assert_same_event,
)
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import DomainEvent, EventScopeKind
from creative_marketer.identity.application.authentication import ActorKind
from creative_marketer.infrastructure.database.event_delivery_schema import (
    inbox_receipts,
    outbox_events,
)


def _event_values(event: DomainEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "schema_version": event.schema_version,
        "scope_kind": event.scope_kind.value,
        "tenant_id": event.tenant_id,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "occurred_at": event.occurred_at,
        "actor_kind": event.actor_kind.value,
        "actor_id": event.actor_id,
        "agent_definition_id": event.agent_definition_id,
        "agent_version_id": event.agent_version_id,
        "agent_run_id": event.agent_run_id,
        "correlation_id": event.correlation_id,
        "causation_id": event.causation_id,
        "payload": event.semantic_envelope()["payload"],
        "payload_schema_digest": event.payload_schema_digest,
        "event_digest": event.event_digest,
        "canonicalization_version": event.canonicalization_version,
    }


def _row_event(row: object) -> DomainEvent:
    data = row._mapping  # type: ignore[attr-defined]
    event = DomainEvent(
        event_id=data["event_id"],
        event_type=data["event_type"],
        schema_version=data["schema_version"],
        scope_kind=EventScopeKind(data["scope_kind"]),
        tenant_id=data["tenant_id"],
        aggregate_type=data["aggregate_type"],
        aggregate_id=data["aggregate_id"],
        occurred_at=data["occurred_at"],
        actor_kind=ActorKind(data["actor_kind"]),
        actor_id=data["actor_id"],
        agent_definition_id=data["agent_definition_id"],
        agent_version_id=data["agent_version_id"],
        agent_run_id=data["agent_run_id"],
        correlation_id=data["correlation_id"],
        causation_id=data["causation_id"],
        payload=data["payload"],
        payload_schema_digest=data["payload_schema_digest"],
        canonicalization_version=data["canonicalization_version"],
    )
    if event.event_digest != data["event_digest"]:
        raise ValueError("persisted event digest mismatch")
    return event


class PostgresOutboxWriter:
    def __init__(
        self, session: AsyncSession, contracts: EventContractRegistry | None = None
    ) -> None:
        self._session = session
        self._contracts = contracts or EventContractRegistry()

    async def append(self, event: DomainEvent) -> None:
        self._contracts.validate_event(event)
        now = event.occurred_at
        await self._session.execute(
            insert(outbox_events).values(
                **_event_values(event),
                publication_state="PENDING",
                attempt_count=0,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
        )


class PostgresPublisherStore:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def claim_ready(
        self, worker_id: UUID, *, batch_size: int, now: datetime, lease_duration: timedelta
    ) -> tuple[ClaimedEvent, ...]:
        if batch_size < 1 or batch_size > 1000:
            raise ValueError("publisher batch size must be between 1 and 1000")
        async with self._factory.begin() as session:
            ready = or_(
                and_(
                    outbox_events.c.publication_state == "PENDING",
                    outbox_events.c.next_attempt_at <= now,
                ),
                and_(
                    outbox_events.c.publication_state == "PUBLISHING",
                    outbox_events.c.lease_expires_at <= now,
                ),
            )
            ids = (
                await session.scalars(
                    select(outbox_events.c.event_id)
                    .where(ready)
                    .order_by(outbox_events.c.next_attempt_at, outbox_events.c.event_id)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            if not ids:
                return ()
            rows = (
                await session.execute(
                    update(outbox_events)
                    .where(outbox_events.c.event_id.in_(ids))
                    .values(
                        publication_state="PUBLISHING",
                        attempt_count=outbox_events.c.attempt_count + 1,
                        lease_owner=worker_id,
                        lease_expires_at=now + lease_duration,
                        updated_at=now,
                    )
                    .returning(outbox_events)
                )
            ).all()
            return tuple(
                ClaimedEvent(_row_event(row), worker_id, row._mapping["attempt_count"])
                for row in rows
            )

    async def _finish(self, claimed: ClaimedEvent, values: dict[str, object]) -> bool:
        async with self._factory.begin() as session:
            result = await session.execute(
                update(outbox_events)
                .where(
                    outbox_events.c.event_id == claimed.event.event_id,
                    outbox_events.c.publication_state == "PUBLISHING",
                    outbox_events.c.lease_owner == claimed.lease_owner,
                    outbox_events.c.attempt_count == claimed.attempt_count,
                )
                .values(**values)
            )
            return bool(cast(CursorResult[Any], result).rowcount)

    async def mark_published(self, claimed: ClaimedEvent, *, now: datetime) -> bool:
        return await self._finish(
            claimed,
            {
                "publication_state": "PUBLISHED",
                "published_at": now,
                "lease_owner": None,
                "lease_expires_at": None,
                "last_error_code": None,
                "last_error_digest": None,
                "updated_at": now,
            },
        )

    async def mark_retryable(
        self,
        claimed: ClaimedEvent,
        *,
        next_attempt_at: datetime,
        error_code: str,
        error_digest: str,
        now: datetime,
    ) -> bool:
        return await self._finish(
            claimed,
            {
                "publication_state": "PENDING",
                "next_attempt_at": next_attempt_at,
                "lease_owner": None,
                "lease_expires_at": None,
                "last_error_code": error_code,
                "last_error_digest": error_digest,
                "updated_at": now,
            },
        )

    async def mark_terminal(
        self, claimed: ClaimedEvent, *, error_code: str, error_digest: str, now: datetime
    ) -> bool:
        return await self._finish(
            claimed,
            {
                "publication_state": "FAILED_TERMINAL",
                "lease_owner": None,
                "lease_expires_at": None,
                "last_error_code": error_code,
                "last_error_digest": error_digest,
                "updated_at": now,
            },
        )

    async def terminal_failures(self, *, limit: int = 100) -> tuple[DomainEvent, ...]:
        async with self._factory() as session:
            rows = (
                await session.execute(
                    select(outbox_events)
                    .where(outbox_events.c.publication_state == "FAILED_TERMINAL")
                    .order_by(outbox_events.c.updated_at.desc())
                    .limit(limit)
                )
            ).all()
            return tuple(_row_event(row) for row in rows)


class PostgresInboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve(
        self, consumer_name: str, handler_version: str, event: DomainEvent, processed_at: datetime
    ) -> InboxReservation:
        inserted = await self._session.scalar(
            pg_insert(inbox_receipts)
            .values(
                consumer_name=consumer_name,
                event_id=event.event_id,
                event_digest=event.event_digest,
                event_type=event.event_type,
                scope_kind=event.scope_kind.value,
                tenant_id=event.tenant_id,
                handler_version=handler_version,
                processed_at=processed_at,
            )
            .on_conflict_do_nothing(index_elements=["consumer_name", "event_id"])
            .returning(inbox_receipts.c.event_digest)
        )
        if inserted is not None:
            return InboxReservation.RESERVED
        existing = await self._session.scalar(
            select(inbox_receipts.c.event_digest).where(
                inbox_receipts.c.consumer_name == consumer_name,
                inbox_receipts.c.event_id == event.event_id,
            )
        )
        if existing is None:
            raise RuntimeError("inbox conflict row disappeared")
        assert_same_event(existing, event)
        return InboxReservation.ALREADY_PROCESSED
