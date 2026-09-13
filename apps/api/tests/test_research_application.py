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
    DisabledSocialResearchProvider,
    FetchedPage,
    FetchPolicyError,
    ResearchConflict,
    ResearchNotFound,
    ResearchPermissionDenied,
    ResearchService,
    SocialCapabilityNotSupported,
)
from creative_marketer.research.domain import (
    EvidenceSnapshot,
    FetchFailureCode,
    FetchStatus,
    ResearchCategory,
    ResearchSource,
    ResearchSourceStatus,
    ResearchTarget,
    ResearchTargetKind,
    SocialEvidenceProvenance,
    SocialEvidenceSnapshot,
    SocialEvidenceType,
    SocialPlatform,
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


class Targets:
    def __init__(self) -> None:
        self.values: dict[UUID, ResearchTarget] = {}

    async def add(self, value: ResearchTarget) -> None:
        self.values[value.id] = value

    async def get(self, value_id: UUID) -> ResearchTarget | None:
        return self.values.get(value_id)

    async def list_for_product(self, product_id: UUID) -> tuple[ResearchTarget, ...]:
        return tuple(item for item in self.values.values() if item.product_id == product_id)

    async def update(self, value: ResearchTarget, expected_status: ResearchSourceStatus) -> None:
        assert self.values[value.id].status is expected_status
        self.values[value.id] = value


class SocialEvidence:
    def __init__(self) -> None:
        self.values: dict[UUID, SocialEvidenceSnapshot] = {}

    async def add(self, value: SocialEvidenceSnapshot) -> None:
        self.values[value.id] = value

    async def get(self, value_id: UUID) -> SocialEvidenceSnapshot | None:
        return self.values.get(value_id)

    async def list_for_product(self, product_id: UUID) -> tuple[SocialEvidenceSnapshot, ...]:
        return tuple(item for item in self.values.values() if item.product_id == product_id)

    async def latest_matching(
        self, target_id: UUID, platform_content_id: str | None, semantic_digest: str
    ) -> SocialEvidenceSnapshot | None:
        del platform_content_id
        return next(
            (
                item
                for item in self.values.values()
                if item.research_target_id == target_id and item.semantic_digest == semantic_digest
            ),
            None,
        )


class Assets:
    def __init__(self) -> None:
        self.allowed: set[UUID] = set()

    async def is_restricted_analysis_asset(self, asset_id: UUID, product_id: UUID) -> bool:
        del product_id
        return asset_id in self.allowed


class Uow:
    def __init__(self, product_id: UUID) -> None:
        self.sources, self.fetches, self.evidence = Sources(), Fetches(), Evidence()
        self.targets, self.social_evidence, self.assets = Targets(), SocialEvidence(), Assets()
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


@dataclass
class SocialProvider:
    context: ExecutionContext
    product_id: UUID
    target_id: UUID
    headlines: list[str]
    platform: SocialPlatform = SocialPlatform.FACEBOOK
    calls: int = 0

    def capabilities(self) -> frozenset[str]:
        return frozenset({"search_ads"})

    async def query(
        self, capability: str, target: ResearchTarget
    ) -> tuple[SocialEvidenceSnapshot, ...]:
        assert capability == "search_ads" and target.id == self.target_id
        headline = self.headlines[min(self.calls, len(self.headlines) - 1)]
        self.calls += 1
        return (
            SocialEvidenceSnapshot(
                tenant_id=self.context.tenant_id,
                product_id=self.product_id,
                research_target_id=self.target_id,
                platform=self.platform,
                evidence_type=SocialEvidenceType.AD,
                provenance=SocialEvidenceProvenance.PROVIDER_FETCHED,
                source_provider="official-meta-ad-library",
                platform_content_id="public-ad-1",
                source_url="https://www.facebook.com/ads/library/?id=public-ad-1",
                headline=headline,
                captured_by=self.context.user_id,
                captured_at=datetime.now(UTC),
                semantic_digest="",
            ),
        )


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
async def test_manual_social_evidence_is_restricted_deduplicated_and_manifest_bounded() -> None:
    context, product_id, uow = setup()
    service = ResearchService(Factory(uow), Fetcher([b"<p>unused</p>"]), Store())
    target = await service.create_target(
        context,
        product_id=product_id,
        kind=ResearchTargetKind.COMPETITOR_BRAND,
        display_name="Competitor",
        platform=SocialPlatform.INSTAGRAM,
        platform_profile_url="https://instagram.com/competitor",
    )
    media_asset_id = uuid4()
    with pytest.raises(ResearchConflict, match="restricted analysis-only"):
        await service.add_manual_social_evidence(
            context,
            target_id=target.id,
            platform=SocialPlatform.INSTAGRAM,
            evidence_type=SocialEvidenceType.REEL,
            source_url="https://instagram.com/reel/unsafe-media",
            media_asset_id=media_asset_id,
        )
    uow.assets.allowed.add(media_asset_id)
    first = await service.add_manual_social_evidence(
        context,
        target_id=target.id,
        platform=SocialPlatform.INSTAGRAM,
        evidence_type=SocialEvidenceType.REEL,
        source_url="https://instagram.com/reel/public",
        headline="A repeated hook",
        body_text="Public caption supplied by the user.",
        media_asset_id=media_asset_id,
    )
    second = await service.add_manual_social_evidence(
        context,
        target_id=target.id,
        platform=SocialPlatform.INSTAGRAM,
        evidence_type=SocialEvidenceType.REEL,
        source_url="https://instagram.com/reel/public",
        headline="A repeated hook",
        body_text="Public caption supplied by the user.",
        media_asset_id=media_asset_id,
    )
    assert second.id == first.id
    changed = await service.add_manual_social_evidence(
        context,
        target_id=target.id,
        platform=SocialPlatform.INSTAGRAM,
        evidence_type=SocialEvidenceType.REEL,
        source_url="https://instagram.com/reel/public",
        headline="A repeated hook",
        body_text="The public caption changed.",
    )
    assert changed.id != first.id
    assert len(uow.social_evidence.values) == 2
    assert first.rights_status == "restricted"
    assert first.allowed_uses == ("internal_analysis",)
    assert first.provenance.value == "user_provided"
    manifest = await service.manifest(context, product_id)
    assert manifest.schema_version == 2
    assert manifest.social_evidence[0].social_evidence_snapshot_id == changed.id
    assert len(manifest.evidence) + len(manifest.social_evidence) <= 50
    for index in range(25):
        await service.add_manual_social_evidence(
            context,
            target_id=target.id,
            platform=SocialPlatform.INSTAGRAM,
            evidence_type=SocialEvidenceType.POST,
            source_url=f"https://instagram.com/p/public-{index}",
            platform_content_id=f"public-{index}",
        )
    bounded = await service.manifest(context, product_id)
    assert len(bounded.social_evidence) == 20
    assert len(bounded.evidence) + len(bounded.social_evidence) <= 50
    archived = await service.archive_target(context, target.id)
    assert archived.status is ResearchSourceStatus.ARCHIVED
    assert not (await service.manifest(context, product_id)).social_evidence
    assert (await service.get_social_evidence(context, first.id)).id == first.id


@pytest.mark.asyncio
async def test_social_provider_is_disabled_without_scraping_fallback() -> None:
    context, product_id, uow = setup()
    service = ResearchService(Factory(uow), Fetcher([b"<p>unused</p>"]), Store())
    target = await service.create_target(
        context,
        product_id=product_id,
        kind=ResearchTargetKind.ADVERTISER,
        display_name="Advertiser",
        platform=SocialPlatform.FACEBOOK,
    )
    assert not service.social_capabilities()[SocialPlatform.FACEBOOK]
    with pytest.raises(SocialCapabilityNotSupported) as error:
        await service.query_social_provider(context, target.id, "search_ads")
    assert error.value.code == "CAPABILITY_NOT_SUPPORTED"


@pytest.mark.asyncio
async def test_social_boundaries_fail_closed_for_missing_archived_and_ungoverned_data() -> None:
    context, product_id, uow = setup()
    service = ResearchService(Factory(uow), Fetcher([b"<p>unused</p>"]), Store())
    unknown = uuid4()
    with pytest.raises(ResearchNotFound):
        await service.create_target(
            context,
            product_id=unknown,
            kind=ResearchTargetKind.COMPETITOR_BRAND,
            display_name="Missing product",
        )
    with pytest.raises(ResearchNotFound):
        await service.list_targets(context, unknown)
    with pytest.raises(ResearchNotFound):
        await service.list_social_evidence(context, unknown)
    with pytest.raises(ResearchNotFound):
        await service.get_social_evidence(context, unknown)
    with pytest.raises(ResearchNotFound):
        await service.add_manual_social_evidence(
            context,
            target_id=unknown,
            platform=SocialPlatform.INSTAGRAM,
            evidence_type=SocialEvidenceType.POST,
            source_url="https://instagram.com/p/missing",
        )
    with pytest.raises(ResearchNotFound):
        await service.query_social_provider(context, unknown, "search_ads")

    website_target = await service.create_target(
        context,
        product_id=product_id,
        kind=ResearchTargetKind.COMPETITOR_BRAND,
        display_name="Website only",
    )
    with pytest.raises(SocialCapabilityNotSupported, match="no social platform"):
        await service.query_social_provider(context, website_target.id, "search_ads")

    target = await service.create_target(
        context,
        product_id=product_id,
        kind=ResearchTargetKind.ADVERTISER,
        display_name="Advertiser",
        platform=SocialPlatform.FACEBOOK,
    )
    with pytest.raises(ResearchConflict, match="platform does not match"):
        await service.add_manual_social_evidence(
            context,
            target_id=target.id,
            platform=SocialPlatform.TIKTOK,
            evidence_type=SocialEvidenceType.AD,
            source_url="https://tiktok.com/public",
        )
    archived = await service.archive_target(context, target.id)
    assert await service.archive_target(context, target.id) is archived
    with pytest.raises(ResearchConflict, match="archived"):
        await service.add_manual_social_evidence(
            context,
            target_id=target.id,
            platform=SocialPlatform.FACEBOOK,
            evidence_type=SocialEvidenceType.AD,
            source_url="https://facebook.com/public",
        )

    with pytest.raises(SocialCapabilityNotSupported):
        await DisabledSocialResearchProvider(SocialPlatform.FACEBOOK).query("search_ads", target)


@pytest.mark.asyncio
async def test_provider_evidence_is_governed_deduplicated_and_versioned() -> None:
    context, product_id, uow = setup()
    service = ResearchService(Factory(uow), Fetcher([b"<p>unused</p>"]), Store())
    target = await service.create_target(
        context,
        product_id=product_id,
        kind=ResearchTargetKind.ADVERTISER,
        display_name="Advertiser",
        platform=SocialPlatform.FACEBOOK,
    )
    provider = SocialProvider(context, product_id, target.id, ["Hook one", "Hook one", "Hook two"])
    service.social_providers[SocialPlatform.FACEBOOK] = provider
    first = (await service.query_social_provider(context, target.id, "search_ads"))[0]
    repeated = (await service.query_social_provider(context, target.id, "search_ads"))[0]
    changed = (await service.query_social_provider(context, target.id, "search_ads"))[0]
    assert repeated.id == first.id
    assert changed.id != first.id
    assert len(uow.social_evidence.values) == 2
    assert all(item.allowed_uses == ("internal_analysis",) for item in (first, changed))


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
