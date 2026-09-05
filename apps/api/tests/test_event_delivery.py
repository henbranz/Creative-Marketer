# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,assignment,index"

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from creative_marketer.events.application import (
    ClaimedEvent,
    ConsumerRegistration,
    ConsumerRegistry,
    InboxReservation,
    ProcessEvent,
    PublicationState,
    PublishOutboxBatch,
    RetryableTransportError,
    TerminalTransportError,
    assert_same_event,
)
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import (
    EventContractError,
    EventIdConflict,
    EventScopeKind,
    canonical_event_json_v1,
    event_sha256_v1,
    tenant_event,
    tenant_event_caused_by,
)
from creative_marketer.events.worker import main as worker_main
from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.observability.ports import NullTelemetry

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)


def context():
    user = uuid4()
    return ExecutionContext(
        uuid4(),
        Actor(ActorKind.USER, user),
        user,
        MembershipRole.OWNER,
        MembershipStatus.ACTIVE,
        "test",
        AuthenticationAssurance(NOW, "test", "mfa"),
        uuid4(),
    )


def event(*, ctx=None, event_id=None):
    ctx = ctx or context()
    contracts = EventContractRegistry()
    event_type = "governance.approval.granted.v1"
    return (
        tenant_event(
            ctx,
            event_type=event_type,
            schema_version=1,
            aggregate_type="approval_request",
            aggregate_id=uuid4(),
            occurred_at=NOW,
            payload_schema_digest=contracts.schema_digest(event_type),
            payload={
                "approval_request_id": str(uuid4()),
                "action_digest": "sha256:" + "a" * 64,
                "decided_by_user_id": str(ctx.user_id),
                "reason_code": None,
            },
        )
        if event_id is None
        else replace(event(ctx=ctx), event_id=event_id)
    )


def test_event_envelope_is_canonical_immutable_and_authoritative() -> None:
    ctx = context()
    built = event(ctx=ctx)
    assert built.tenant_id == ctx.tenant_id
    assert built.actor_id == ctx.actor.id
    assert built.correlation_id == ctx.correlation_id
    assert built.event_digest == event_sha256_v1(built.semantic_envelope())
    assert canonical_event_json_v1({"b": 2, "a": "é"}) == '{"a":"é","b":2}'
    with pytest.raises(TypeError):
        built.payload["reason_code"] = "changed"
    nested = replace(built, payload={**built.payload, "nested": {"items": [1, 2]}})
    with pytest.raises(TypeError):
        nested.payload["nested"]["items"] = ()
    with pytest.raises(FrozenInstanceError):
        built.event_type = "changed.v1"


@pytest.mark.parametrize(
    "value",
    [
        9_007_199_254_740_992,
        1.5,
        b"bytes",
        {1: "bad"},
        "\ud800",
        "Bearer abcdefgh",
        {"api_key": "x"},
        {"customer_email": "person@example.test"},
        {"cookie": "x"},
    ],
)
def test_event_canonicalization_rejects_nonportable_sensitive_values(value) -> None:
    with pytest.raises(EventContractError):
        canonical_event_json_v1(value)


def test_event_envelope_rejects_scope_version_time_digest_and_size_errors() -> None:
    built = event()
    for changes in (
        {"event_type": "governance.approval.granted.v2"},
        {"event_type": "bad"},
        {"tenant_id": None},
        {"scope_kind": EventScopeKind.PLATFORM},
        {"occurred_at": NOW.replace(tzinfo=None)},
        {"aggregate_type": ""},
        {"aggregate_type": "x" * 101},
        {"payload_schema_digest": "bad"},
        {"canonicalization_version": 2},
        {"payload": {"x": "a" * 17_000}},
    ):
        with pytest.raises(EventContractError):
            replace(built, **changes)
    platform = replace(built, scope_kind=EventScopeKind.PLATFORM, tenant_id=None)
    assert platform.tenant_id is None


def test_contract_registry_validates_strict_payloads_and_rejects_unknown_or_refs(tmp_path) -> None:
    registry = EventContractRegistry()
    valid = event()
    registry.validate_event(valid)
    for changes in (
        {"payload": {**valid.payload, "unexpected": "data"}},
        {"payload": {**valid.payload, "approval_request_id": "not-a-uuid"}},
        {"payload_schema_digest": "sha256:" + "b" * 64},
        {"event_type": "evil.execute_everything.v1"},
    ):
        with pytest.raises(EventContractError):
            registry.validate_event(replace(valid, **changes))
    assert set(registry.event_types) == {
        "catalog.asset.archived.v1",
        "catalog.asset.ready.v1",
        "catalog.brand.created.v1",
        "catalog.product.created.v1",
        "catalog.product.updated.v1",
        "catalog.product.brief_completed.v1",
        "catalog.product.snapshot_created.v1",
        "catalog.product.snapshot_created.v2",
        "governance.approval.requested.v1",
        "governance.approval.granted.v1",
        "governance.approval.denied.v1",
        "governance.approval.revoked.v1",
        "governance.tool.execution_succeeded.v1",
        "governance.tool.execution_failed.v1",
        "governance.tool.execution_outcome_unknown.v1",
    }
    (tmp_path / "bad.json").write_text(
        '{"$schema":"https://json-schema.org/draft/2020-12/schema",'
        '"x-event-type":"bad.ref.v1","$ref":"https://evil.test/schema"}'
    )
    with pytest.raises(EventContractError):
        EventContractRegistry(Path(tmp_path))
    (tmp_path / "bad.json").write_text('{"type":"object"}')
    with pytest.raises(EventContractError):
        EventContractRegistry(Path(tmp_path))


def test_caused_event_requires_tenant_source_and_worker_fails_closed() -> None:
    source = event()
    platform = replace(source, scope_kind=EventScopeKind.PLATFORM, tenant_id=None)
    with pytest.raises(EventContractError):
        tenant_event_caused_by(
            platform,
            source_actor := Actor(ActorKind.SYSTEM, uuid4()),
            event_type=source.event_type,
            schema_version=1,
            aggregate_type="approval_request",
            aggregate_id=uuid4(),
            payload=source.payload,
            payload_schema_digest=source.payload_schema_digest,
            occurred_at=NOW,
        )
    assert source_actor.kind is ActorKind.SYSTEM
    with pytest.raises(SystemExit, match="No event transport"):
        worker_main()


class MemoryPublisherStore:
    def __init__(self, items):
        self.items = list(items)
        self.actions = []

    async def claim_ready(self, worker_id, *, batch_size, now, lease_duration):
        return tuple(self.items[:batch_size])

    async def delivery_health(self):
        from creative_marketer.events.application import DeliveryHealth

        return DeliveryHealth(len(self.items), NOW if self.items else None, 0)

    async def mark_published(self, claimed, *, now):
        self.actions.append((PublicationState.PUBLISHED, claimed))
        return True

    async def mark_retryable(self, claimed, **kwargs):
        self.actions.append((PublicationState.PENDING, claimed, kwargs))
        return True

    async def mark_terminal(self, claimed, **kwargs):
        self.actions.append((PublicationState.FAILED_TERMINAL, claimed, kwargs))
        return True


class MemoryTransport:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.events = []

    async def publish(self, candidate, trace_context=None):
        self.events.append(candidate)
        outcome = self.outcomes.pop(0)
        if outcome is not None:
            raise outcome


class RecordingTelemetry(NullTelemetry):
    def __init__(self):
        self.gauges = {}

    def gauge(self, name, value, attributes=None):
        self.gauges[name] = value


class FailingHealthStore(MemoryPublisherStore):
    async def delivery_health(self):
        raise RuntimeError("diagnostic query failed")


@pytest.mark.asyncio
async def test_publisher_reports_success_retry_and_terminal_failures() -> None:
    worker = uuid4()
    items = tuple(ClaimedEvent(event(), worker, attempt) for attempt in (1, 2, 8, 1))
    store = MemoryPublisherStore(items)
    telemetry = RecordingTelemetry()
    transport = MemoryTransport(
        [
            None,
            RetryableTransportError("UNAVAILABLE", "sha256:" + "a" * 64),
            RetryableTransportError("UNAVAILABLE", "sha256:" + "b" * 64),
            TerminalTransportError("REJECTED", "sha256:" + "c" * 64),
        ]
    )
    result = await PublishOutboxBatch(store, transport, worker, telemetry=telemetry)(now=NOW)
    assert (result.claimed_count, result.published_count) == (4, 1)
    assert (result.retry_count, result.terminal_failure_count) == (1, 2)
    assert store.actions[1][2]["next_attempt_at"] == NOW + timedelta(seconds=2)
    assert telemetry.gauges == {
        "outbox.pending": 4,
        "outbox.terminal_failures": 0,
        "outbox.oldest_pending_age": 0.0,
    }
    empty = await PublishOutboxBatch(MemoryPublisherStore(()), MemoryTransport([]), worker)(now=NOW)
    assert empty.claimed_count == 0
    with pytest.raises(ValueError):
        RetryableTransportError("unsafe error", "bad")
    with pytest.raises(ValueError):
        RetryableTransportError("UNAVAILABLE", "bad")


@pytest.mark.asyncio
async def test_operational_snapshot_failure_does_not_stop_publication() -> None:
    worker = uuid4()
    store = FailingHealthStore((ClaimedEvent(event(), worker, 1),))
    transport = MemoryTransport([None])
    result = await PublishOutboxBatch(store, transport, worker)(now=NOW)
    assert result.published_count == 1
    for kwargs in (
        {"max_attempts": 0},
        {"lease_duration": timedelta(0)},
        {"base_backoff": timedelta(seconds=2), "max_backoff": timedelta(seconds=1)},
    ):
        with pytest.raises(ValueError):
            PublishOutboxBatch(store, transport, worker, **kwargs)


class MemoryInbox:
    def __init__(self):
        self.items = {}

    async def reserve(self, consumer_name, handler_version, candidate, processed_at):
        key = (consumer_name, candidate.event_id)
        if key in self.items:
            assert_same_event(self.items[key], candidate)
            return InboxReservation.ALREADY_PROCESSED
        self.items[key] = candidate.event_digest
        return InboxReservation.RESERVED


class MemoryConsumerUow:
    def __init__(self, inbox):
        self.inbox = inbox
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_consumer_registry_processing_duplicate_conflict_and_causation() -> None:
    first = event()
    inbox = MemoryInbox()
    uow = MemoryConsumerUow(inbox)
    effects = []

    async def handler(candidate, transaction):
        effects.append(candidate.event_id)

    consumers = ConsumerRegistry(
        (
            ConsumerRegistration(
                "production.approval_granted",
                frozenset({first.event_type}),
                "1.0.0",
                handler,
            ),
        )
    )
    processor = ProcessEvent(EventContractRegistry(), consumers, lambda _context: uow)
    assert (await processor("production.approval_granted", first)).processed_count == 1
    assert (await processor("production.approval_granted", first)).duplicate_count == 1
    assert effects == [first.event_id]
    assert uow.commits == 1
    with pytest.raises(ValueError):
        await processor("unknown", first)
    conflict = replace(first, payload={**first.payload, "reason_code": "different"})
    with pytest.raises(EventIdConflict):
        await processor("production.approval_granted", conflict)
    emitted = replace(
        event(ctx=context()),
        correlation_id=first.correlation_id,
        causation_id=first.event_id,
    )
    assert emitted.correlation_id == first.correlation_id
    assert emitted.causation_id == first.event_id
    platform = replace(first, scope_kind=EventScopeKind.PLATFORM, tenant_id=None)
    with pytest.raises(ValueError):
        await processor("production.approval_granted", platform)
    with pytest.raises(ValueError):
        ConsumerRegistry((ConsumerRegistration("", frozenset(), "1", handler),))
    with pytest.raises(ValueError):
        ConsumerRegistry(
            (
                ConsumerRegistration("duplicate", frozenset({first.event_type}), "1", handler),
                ConsumerRegistration("duplicate", frozenset({first.event_type}), "2", handler),
            )
        )
