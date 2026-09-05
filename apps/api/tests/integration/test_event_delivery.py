# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,union-attr"

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from creative_marketer.action_binding import NormalizedToolInput
from creative_marketer.approval_governance.application import (
    CreateApprovalRequest,
    DecideApproval,
    RevokeApproval,
)
from creative_marketer.approval_governance.domain import HumanDecision
from creative_marketer.events.application import (
    ConsumerRegistration,
    ConsumerRegistry,
    ProcessEvent,
    PublishOutboxBatch,
)
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import DomainEvent, EventIdConflict, tenant_event_caused_by
from creative_marketer.identity.application.authentication import Actor, ActorKind
from creative_marketer.infrastructure.database.approval_schema import (
    approval_decisions,
    approval_requests,
)
from creative_marketer.infrastructure.database.approval_uow import (
    SqlAlchemyApprovalUnitOfWork,
)
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.event_consumer_uow import (
    SqlAlchemyConsumerUnitOfWork,
    SqlAlchemyConsumerUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.event_delivery import (
    PostgresOutboxWriter,
    PostgresPublisherStore,
)
from creative_marketer.infrastructure.database.event_delivery_schema import (
    inbox_receipts,
    outbox_events,
)
from creative_marketer.infrastructure.database.schema import audit_records, users
from tests.integration.test_approval_idempotency import approval_subject

NOW = datetime.now(UTC)


class CollectingTransport:
    def __init__(self, processor=None, consumer_name=None) -> None:
        self.events: list[DomainEvent] = []
        self.processor, self.consumer_name = processor, consumer_name

    async def publish(self, event: DomainEvent) -> None:
        self.events.append(event)
        if self.processor is not None:
            await self.processor(self.consumer_name, event)


class FailingWriter:
    async def append(self, _value) -> None:
        raise RuntimeError("forced writer failure")


class FailingApprovalUnitOfWork(SqlAlchemyApprovalUnitOfWork):
    def __init__(self, factory, context, *, fail_audit: bool) -> None:
        super().__init__(factory, context)
        self._fail_audit = fail_audit

    async def __aenter__(self):
        await super().__aenter__()
        if self._fail_audit:
            self.audit = FailingWriter()
        else:
            self.outbox = FailingWriter()
        return self


class FailingApprovalFactory:
    def __init__(self, sessions, *, fail_audit: bool) -> None:
        self._sessions, self._fail_audit = sessions, fail_audit

    def __call__(self, context):
        return FailingApprovalUnitOfWork(self._sessions, context, fail_audit=self._fail_audit)


async def _request(
    identity_stack,
    agent_registry_factory,
    tool_control_factory,
    tool_runtime_factory,
    permission_factory,
    approval_factory,
):
    ctx, permission, _, _ = await approval_subject(
        identity_stack,
        agent_registry_factory,
        tool_control_factory,
        tool_runtime_factory,
        permission_factory,
    )
    request = await CreateApprovalRequest(approval_factory, clock=lambda: NOW)(
        ctx,
        permission,
        NormalizedToolInput.from_trusted_value({"post_id": "123"}),
    )
    return ctx, permission, request


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_approval_state_audit_and_outbox_are_atomic(
    admin_engine,
    runtime_database_url,
    identity_stack,
    agent_registry_factory,
    tool_control_factory,
    tool_runtime_factory,
    permission_factory,
    approval_factory,
) -> None:
    ctx, permission, request = await _request(
        identity_stack,
        agent_registry_factory,
        tool_control_factory,
        tool_runtime_factory,
        permission_factory,
        approval_factory,
    )
    decision = await DecideApproval(approval_factory, clock=lambda: NOW + timedelta(seconds=1))(
        ctx, request.id, HumanDecision.APPROVE, reason_code="owner_approved"
    )
    await RevokeApproval(approval_factory, clock=lambda: NOW + timedelta(seconds=2))(
        ctx, request.id, "owner_revoked"
    )
    async with admin_engine.connect() as connection:
        types = (
            await connection.scalars(
                select(outbox_events.c.event_type)
                .where(outbox_events.c.aggregate_id == request.id)
                .order_by(outbox_events.c.occurred_at)
            )
        ).all()
        assert types == [
            "governance.approval.requested.v1",
            "governance.approval.granted.v1",
            "governance.approval.revoked.v1",
        ]
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(audit_records)
                .where(audit_records.c.approval_request_id == request.id)
            )
            == 3
        )
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(approval_decisions)
                .where(approval_decisions.c.id == decision.id)
            )
            == 1
        )

    sessions = create_session_factory(runtime_database_url)
    for fail_audit in (True, False):
        async with admin_engine.connect() as connection:
            before = await connection.scalar(select(func.count()).select_from(approval_requests))
            audit_before = await connection.scalar(select(func.count()).select_from(audit_records))
            outbox_before = await connection.scalar(select(func.count()).select_from(outbox_events))
        failing_factory = FailingApprovalFactory(sessions, fail_audit=fail_audit)
        with pytest.raises(RuntimeError, match="forced writer failure"):
            await CreateApprovalRequest(failing_factory, clock=lambda: NOW)(
                ctx,
                permission,
                NormalizedToolInput.from_trusted_value({"post_id": f"rollback-{fail_audit}"}),
            )
        async with admin_engine.connect() as connection:
            assert (
                await connection.scalar(select(func.count()).select_from(approval_requests))
                == before
            )
            assert (
                await connection.scalar(select(func.count()).select_from(audit_records))
                == audit_before
            )
            assert (
                await connection.scalar(select(func.count()).select_from(outbox_events))
                == outbox_before
            )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_publisher_role_claims_but_cannot_mutate_content_or_read_business_data(
    admin_engine: AsyncEngine,
    publisher_engine: AsyncEngine,
    publisher_database_url: str,
    runtime_engine: AsyncEngine,
    identity_stack,
    agent_registry_factory,
    tool_control_factory,
    tool_runtime_factory,
    permission_factory,
    approval_factory,
) -> None:
    ctx, permission, request = await _request(
        identity_stack,
        agent_registry_factory,
        tool_control_factory,
        tool_runtime_factory,
        permission_factory,
        approval_factory,
    )
    async with publisher_engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(outbox_events)) == 1
        for statement in (
            select(users),
            text("SELECT * FROM approval_governance.approval_requests"),
            text("SELECT * FROM agent_governance.agent_versions"),
            insert(outbox_events).values(event_id=uuid4()),
            update(outbox_events)
            .where(outbox_events.c.aggregate_id == request.id)
            .values(payload={}),
        ):
            with pytest.raises(DBAPIError):
                await connection.execute(statement)
            await connection.rollback()
        role = await connection.execute(
            text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = 'creative_marketer_event_publisher'"
            )
        )
        assert role.one() == (False, False)
    async with runtime_engine.connect() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(select(outbox_events))
        await connection.rollback()

    store = PostgresPublisherStore(create_session_factory(publisher_database_url))
    claimed = await store.claim_ready(
        uuid4(), batch_size=10, now=NOW, lease_duration=timedelta(seconds=30)
    )
    assert len(claimed) == 1
    assert claimed[0].event.aggregate_id == request.id
    assert await store.mark_published(claimed[0], now=NOW)
    async with admin_engine.connect() as connection:
        assert (
            await connection.scalar(
                select(outbox_events.c.publication_state).where(
                    outbox_events.c.event_id == claimed[0].event.event_id
                )
            )
            == "PUBLISHED"
        )
        immutable_changes: tuple[dict[str, object], ...] = (
            {"event_type": "governance.approval.denied.v1"},
            {"tenant_id": uuid4()},
            {"actor_id": uuid4()},
            {"payload": {}},
            {"payload_schema_digest": "sha256:" + "a" * 64},
            {"event_digest": "sha256:" + "b" * 64},
        )
        for values in immutable_changes:
            with pytest.raises(DBAPIError):
                await connection.execute(
                    update(outbox_events)
                    .where(outbox_events.c.event_id == claimed[0].event.event_id)
                    .values(**values)
                )
            await connection.rollback()

    create = CreateApprovalRequest(approval_factory, clock=lambda: NOW + timedelta(seconds=3))
    for index in range(4):
        await create(
            ctx,
            permission,
            NormalizedToolInput.from_trusted_value({"post_id": f"concurrent-{index}"}),
        )
    first_store = PostgresPublisherStore(create_session_factory(publisher_database_url))
    second_store = PostgresPublisherStore(create_session_factory(publisher_database_url))
    left, right = await asyncio.gather(
        first_store.claim_ready(
            uuid4(),
            batch_size=2,
            now=NOW + timedelta(seconds=4),
            lease_duration=timedelta(seconds=30),
        ),
        second_store.claim_ready(
            uuid4(),
            batch_size=2,
            now=NOW + timedelta(seconds=4),
            lease_duration=timedelta(seconds=30),
        ),
    )
    assert len(left) == len(right) == 2
    assert {item.event.event_id for item in left}.isdisjoint(
        {item.event.event_id for item in right}
    )
    assert await first_store.mark_retryable(
        left[0],
        next_attempt_at=NOW + timedelta(seconds=10),
        error_code="TRANSPORT_UNAVAILABLE",
        error_digest="sha256:" + "d" * 64,
        now=NOW + timedelta(seconds=4),
    )
    assert await first_store.mark_terminal(
        left[1],
        error_code="TRANSPORT_REJECTED",
        error_digest="sha256:" + "e" * 64,
        now=NOW + timedelta(seconds=4),
    )
    failures = await first_store.terminal_failures()
    assert [item.event_id for item in failures] == [left[1].event.event_id]


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_crash_duplicate_concurrency_inbox_atomicity_and_causation(
    admin_engine,
    publisher_database_url,
    runtime_database_url,
    identity_stack,
    agent_registry_factory,
    tool_control_factory,
    tool_runtime_factory,
    permission_factory,
    approval_factory,
) -> None:
    _ctx, _permission, _request_value = await _request(
        identity_stack,
        agent_registry_factory,
        tool_control_factory,
        tool_runtime_factory,
        permission_factory,
        approval_factory,
    )
    publisher = PostgresPublisherStore(create_session_factory(publisher_database_url))
    first_claim = (
        await publisher.claim_ready(
            uuid4(), batch_size=1, now=NOW, lease_duration=timedelta(seconds=1)
        )
    )[0]
    effects: list[object] = []

    async def counting_handler(candidate, _uow) -> None:
        effects.append(candidate.event_id)

    processor = ProcessEvent(
        EventContractRegistry(),
        ConsumerRegistry(
            (
                ConsumerRegistration(
                    "production.approval_requested",
                    frozenset({first_claim.event.event_type}),
                    "1.0.0",
                    counting_handler,
                ),
            )
        ),
        SqlAlchemyConsumerUnitOfWorkFactory(create_session_factory(runtime_database_url)),
    )
    transport = CollectingTransport(processor, "production.approval_requested")
    await transport.publish(first_claim.event)
    # Simulated crash: transport accepted, but no PUBLISHED update occurred.
    result = await PublishOutboxBatch(
        publisher,
        transport,
        uuid4(),
        lease_duration=timedelta(seconds=1),
    )(batch_size=1, now=NOW + timedelta(seconds=2))
    assert result.published_count == 1
    assert transport.events == [first_claim.event, first_claim.event]
    assert effects == [first_claim.event.event_id]
    conflicting = replace(
        first_claim.event,
        payload={**first_claim.event.payload, "action_digest": "sha256:" + "f" * 64},
    )
    with pytest.raises(EventIdConflict):
        await processor("production.approval_requested", conflicting)

    async with admin_engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(inbox_receipts)) == 1

    # A handler-emitted fact and the Inbox receipt share the consumer transaction.
    chained_id = uuid4()
    chained = tenant_event_caused_by(
        first_claim.event,
        Actor(ActorKind.SYSTEM, uuid4()),
        event_type="governance.approval.denied.v1",
        schema_version=1,
        aggregate_type=first_claim.event.aggregate_type,
        aggregate_id=first_claim.event.aggregate_id,
        occurred_at=NOW,
        payload_schema_digest=EventContractRegistry().schema_digest(
            "governance.approval.denied.v1"
        ),
        payload={
            "approval_request_id": str(first_claim.event.aggregate_id),
            "action_digest": first_claim.event.payload["action_digest"],
            "decided_by_user_id": str(uuid4()),
            "reason_code": "test_projection",
        },
    )
    chained = replace(chained, event_id=chained_id)
    fail = True

    async def atomic_handler(_candidate, transaction) -> None:
        assert isinstance(transaction, SqlAlchemyConsumerUnitOfWork)
        await PostgresOutboxWriter(transaction.session).append(chained)
        if fail:
            raise RuntimeError("test handler failure")

    atomic_processor = ProcessEvent(
        EventContractRegistry(),
        ConsumerRegistry(
            (
                ConsumerRegistration(
                    "test.atomic_projection",
                    frozenset({first_claim.event.event_type}),
                    "1.0.0",
                    atomic_handler,
                ),
            )
        ),
        SqlAlchemyConsumerUnitOfWorkFactory(create_session_factory(runtime_database_url)),
    )
    with pytest.raises(RuntimeError, match="test handler failure"):
        await atomic_processor("test.atomic_projection", first_claim.event)
    async with admin_engine.connect() as connection:
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(outbox_events)
                .where(outbox_events.c.event_id == chained_id)
            )
            == 0
        )
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(inbox_receipts)
                .where(inbox_receipts.c.consumer_name == "test.atomic_projection")
            )
            == 0
        )
    fail = False
    outcomes = await asyncio.gather(
        atomic_processor("test.atomic_projection", first_claim.event),
        atomic_processor("test.atomic_projection", first_claim.event),
    )
    assert sum(item.processed_count for item in outcomes) == 1
    assert sum(item.duplicate_count for item in outcomes) == 1
    async with admin_engine.connect() as connection:
        row = (
            await connection.execute(
                select(outbox_events.c.correlation_id, outbox_events.c.causation_id).where(
                    outbox_events.c.event_id == chained_id
                )
            )
        ).one()
        assert row == (first_claim.event.correlation_id, first_claim.event.event_id)
