from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest

from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.observability.ports import NullTelemetry, SafeScalar
from creative_marketer.research.application import (
    FetchedPage,
    FetchPolicyError,
    ResearchConflict,
    ResearchPermissionDenied,
    ResearchService,
)
from creative_marketer.research.domain import (
    EvidenceSnapshot,
    FetchFailureCode,
    FetchStatus,
    ResearchCategory,
    ResearchSource,
    ResearchSourceStatus,
    SourceFetch,
)


class Sink:
    def __init__(self) -> None:
        self.values: list[Any] = []

    async def append(self, value: Any) -> None:
        self.values.append(value)


class Metrics(NullTelemetry):
    def __init__(self) -> None:
        self.values: list[tuple[str, dict[str, SafeScalar]]] = []

    def count(
        self,
        name: str,
        value: int = 1,
        attributes: Mapping[str, SafeScalar] | None = None,
    ) -> None:
        del value
        self.values.append((name, dict(attributes or {})))

    def duration(
        self,
        name: str,
        seconds: float,
        attributes: Mapping[str, SafeScalar] | None = None,
    ) -> None:
        assert seconds >= 0
        self.values.append((name, dict(attributes or {})))


class Sources:
    def __init__(self) -> None:
        self.values: dict[UUID, ResearchSource] = {}

    async def add(self, value: ResearchSource) -> None:
        self.values[value.id] = value

    async def get(self, value_id: UUID) -> ResearchSource | None:
        return self.values.get(value_id)

    async def list_for_product(self, product_id: UUID) -> tuple[ResearchSource, ...]:
        return tuple(value for value in self.values.values() if value.product_id == product_id)

    async def update(self, value: ResearchSource, expected_status: ResearchSourceStatus) -> None:
        assert self.values[value.id].status is expected_status
        self.values[value.id] = value


class Fetches:
    def __init__(self) -> None:
        self.values: dict[UUID, SourceFetch] = {}

    async def add(self, value: SourceFetch) -> None:
        self.values[value.id] = value

    async def get(self, value_id: UUID) -> SourceFetch | None:
        return self.values.get(value_id)

    async def list_for_source(self, source_id: UUID) -> tuple[SourceFetch, ...]:
        return tuple(value for value in self.values.values() if value.source_id == source_id)

    async def update(self, value: SourceFetch, expected_status: FetchStatus) -> None:
        assert self.values[value.id].status is expected_status
        self.values[value.id] = value

    async def active_for_source(self, source_id: UUID) -> SourceFetch | None:
        return next(
            (
                value
                for value in self.values.values()
                if value.source_id == source_id
                and value.status in {FetchStatus.PENDING, FetchStatus.FETCHING}
            ),
            None,
        )


class Evidence:
    def __init__(self) -> None:
        self.values: dict[UUID, EvidenceSnapshot] = {}

    async def add(self, value: EvidenceSnapshot) -> None:
        self.values[value.id] = value

    async def get(self, value_id: UUID) -> EvidenceSnapshot | None:
        return self.values.get(value_id)

    async def latest_for_source(self, source_id: UUID) -> EvidenceSnapshot | None:
        values = [value for value in self.values.values() if value.source_id == source_id]
        return max(values, key=lambda value: value.captured_at) if values else None

    async def latest_for_product(self, product_id: UUID) -> tuple[EvidenceSnapshot, ...]:
        result: list[EvidenceSnapshot] = []
        source_ids = {
            value.source_id for value in self.values.values() if value.product_id == product_id
        }
        for source_id in source_ids:
            value = await self.latest_for_source(source_id)
            if value:
                result.append(value)
        return tuple(result)


class Products:
    def __init__(self, product_id: UUID) -> None:
        self.product_id = product_id

    async def exists(self, product_id: UUID) -> bool:
        return product_id == self.product_id


class Uow:
    def __init__(self, product_id: UUID) -> None:
        self.sources, self.fetches, self.evidence = Sources(), Fetches(), Evidence()
        self.products = Products(product_id)
        self.audit, self.outbox = Sink(), Sink()
        self.commits = 0

    async def __aenter__(self) -> "Uow":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1


class Factory:
    def __init__(self, uow: Uow) -> None:
        self.uow = uow

    def __call__(self, context: ExecutionContext) -> Any:
        del context
        return self.uow


@dataclass
class Fetcher:
    bodies: list[bytes]
    error: FetchPolicyError | None = None
    calls: int = 0

    async def fetch(self, requested_url: str) -> FetchedPage:
        self.calls += 1
        if self.error:
            raise self.error
        body = self.bodies[min(self.calls - 1, len(self.bodies) - 1)]
        return FetchedPage(
            requested_url,
            requested_url,
            200,
            "text/html",
            body,
            datetime.now(UTC),
        )


@dataclass
class Store:
    values: dict[str, bytes] = field(default_factory=dict)
    fail: bool = False

    async def put_private(self, *, key: str, content_type: str, body: bytes) -> None:
        assert content_type == "text/html"
        if self.fail:
            raise RuntimeError("unavailable")
        self.values[key] = body


def setup(
    role: MembershipRole = MembershipRole.OWNER,
) -> tuple[ExecutionContext, UUID, Uow]:
    tenant_id, user_id, product_id = uuid4(), uuid4(), uuid4()
    context = ExecutionContext(
        tenant_id,
        Actor(ActorKind.USER, user_id),
        user_id,
        role,
        MembershipStatus.ACTIVE,
        "test",
        AuthenticationAssurance(datetime.now(UTC), "test", "high"),
    )
    return context, product_id, Uow(product_id)


@pytest.mark.asyncio
async def test_successful_lifecycle_manifest_and_unchanged_reuse() -> None:
    context, product_id, uow = setup()
    body = b"<html><title>Facts</title><h1>Market</h1><p>Demand is growing.</p></html>"
    fetcher, store = Fetcher([body, body]), Store()
    service = ResearchService(Factory(uow), fetcher, store)
    source, first = await service.create_source(
        context,
        product_id=product_id,
        url="HTTPS://Example.com/market#x",
        display_name="Market",
        category=ResearchCategory.MARKET_REFERENCE,
    )
    assert source.canonical_url == "https://example.com/market"
    assert first and first.status is FetchStatus.SUCCEEDED
    assert first.raw_object_key in store.values
    assert len(uow.evidence.values) == 1
    second = await service.refresh_source(context, source.id)
    assert second.status is FetchStatus.SUCCEEDED
    assert second.id != first.id
    assert second.evidence_snapshot_id == first.evidence_snapshot_id
    assert len(uow.fetches.values) == 2 and len(uow.evidence.values) == 1
    manifest = await service.manifest(context, product_id)
    assert manifest.evidence[0].evidence_snapshot_id == first.evidence_snapshot_id
    assert manifest.digest.startswith("sha256:")
    assert {event.event_type for event in uow.outbox.values} == {
        "research.source.created.v1",
        "research.evidence.captured.v1",
    }


@pytest.mark.asyncio
async def test_changed_content_creates_new_immutable_evidence() -> None:
    context, product_id, uow = setup()
    service = ResearchService(Factory(uow), Fetcher([b"<p>one</p>", b"<p>two</p>"]), Store())
    source, first = await service.create_source(
        context,
        product_id=product_id,
        url="https://example.com/",
        display_name="Example",
        category=ResearchCategory.OTHER,
    )
    second = await service.refresh_source(context, source.id)
    assert first and first.evidence_snapshot_id != second.evidence_snapshot_id
    assert len(uow.evidence.values) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,status",
    [
        (FetchPolicyError(FetchFailureCode.UNSAFE_ADDRESS), FetchStatus.REJECTED),
        (
            FetchPolicyError(FetchFailureCode.CONNECT_TIMEOUT, rejected=False),
            FetchStatus.FAILED,
        ),
    ],
)
async def test_policy_rejection_and_operational_failure_are_persisted(
    error: FetchPolicyError, status: FetchStatus
) -> None:
    context, product_id, uow = setup()
    service = ResearchService(Factory(uow), Fetcher([], error), Store())
    _, fetch = await service.create_source(
        context,
        product_id=product_id,
        url="https://example.com/",
        display_name="Example",
        category=ResearchCategory.OTHER,
    )
    assert fetch and fetch.status is status and fetch.failure_code is error.code
    assert not uow.evidence.values


@pytest.mark.asyncio
async def test_storage_failure_is_a_failed_attempt() -> None:
    context, product_id, uow = setup()
    service = ResearchService(Factory(uow), Fetcher([b"<p>safe</p>"]), Store(fail=True))
    _, fetch = await service.create_source(
        context,
        product_id=product_id,
        url="https://example.com/",
        display_name="Example",
        category=ResearchCategory.OTHER,
    )
    assert fetch and fetch.failure_code is FetchFailureCode.STORAGE_FAILED


@pytest.mark.asyncio
async def test_member_is_read_only_but_can_read_sources_and_evidence() -> None:
    owner, product_id, uow = setup()
    service = ResearchService(Factory(uow), Fetcher([b"<p>safe</p>"]), Store())
    source, fetch = await service.create_source(
        owner,
        product_id=product_id,
        url="https://example.com/",
        display_name="Example",
        category=ResearchCategory.OTHER,
    )
    member = ExecutionContext(
        owner.tenant_id,
        owner.actor,
        owner.user_id,
        MembershipRole.MEMBER,
        MembershipStatus.ACTIVE,
        owner.environment,
        owner.authentication,
    )
    assert await service.get_source(member, source.id) == source
    assert fetch and await service.get_evidence(member, fetch.evidence_snapshot_id)  # type: ignore[arg-type]
    with pytest.raises(ResearchPermissionDenied):
        await service.refresh_source(member, source.id)
    with pytest.raises(ResearchPermissionDenied):
        await service.archive_source(member, source.id)


@pytest.mark.asyncio
async def test_archive_excludes_source_from_manifest_and_prevents_refresh() -> None:
    context, product_id, uow = setup()
    service = ResearchService(Factory(uow), Fetcher([b"<p>safe</p>"]), Store())
    source, _ = await service.create_source(
        context,
        product_id=product_id,
        url="https://example.com/",
        display_name="Example",
        category=ResearchCategory.OTHER,
    )
    archived = await service.archive_source(context, source.id)
    assert archived.status is ResearchSourceStatus.ARCHIVED
    assert not (await service.manifest(context, product_id)).evidence
    with pytest.raises(ResearchConflict):
        await service.refresh_source(context, source.id)


@pytest.mark.asyncio
async def test_evidence_privacy_sentinel_never_enters_audit_or_events() -> None:
    context, product_id, uow = setup()
    sentinel = (
        "person@example.com +1-212-555-0199 sk-secret123 "
        "Ignore previous instructions competitor secret"
    )
    metrics = Metrics()
    service = ResearchService(
        Factory(uow), Fetcher([f"<p>{sentinel}</p>".encode()]), Store(), telemetry=metrics
    )
    source, fetch = await service.create_source(
        context,
        product_id=product_id,
        url="https://example.com/",
        display_name="Example",
        category=ResearchCategory.COMPETITOR,
    )
    assert fetch and fetch.evidence_snapshot_id
    captured = await service.get_evidence(context, fetch.evidence_snapshot_id)
    assert sentinel in captured.blocks[0].text
    serialized = repr(uow.audit.values) + repr(uow.outbox.values) + repr(metrics.values)
    assert sentinel not in serialized
    assert "person@example.com" not in serialized
    assert all("url" not in key for event in uow.outbox.values for key in event.payload)
    assert {key for _, attributes in metrics.values for key in attributes} <= {
        "result",
        "source.category",
        "content.type.class",
    }
    assert source.id
