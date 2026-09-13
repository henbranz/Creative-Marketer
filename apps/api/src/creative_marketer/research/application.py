from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from types import TracebackType
from typing import Protocol
from uuid import UUID, uuid4

from creative_marketer.audit.application import AuditWriter
from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditOutcome
from creative_marketer.audit.safety import safe_metadata
from creative_marketer.events.application import OutboxWriter
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import tenant_event
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.observability.ports import NullTelemetry, OperationalTelemetry
from creative_marketer.research.domain import (
    EvidenceSnapshot,
    FetchFailureCode,
    FetchStatus,
    ResearchCategory,
    ResearchContextManifest,
    ResearchEvidenceReference,
    ResearchSource,
    ResearchSourceStatus,
    ResearchTarget,
    ResearchTargetKind,
    SocialEvidenceProvenance,
    SocialEvidenceReference,
    SocialEvidenceSnapshot,
    SocialEvidenceType,
    SocialPlatform,
    SourceFetch,
    canonicalize_url,
    sha256_bytes,
)
from creative_marketer.research.extraction import DeterministicEvidenceExtractor


class ResearchNotFound(Exception):
    pass


class ResearchConflict(Exception):
    pass


class ResearchPermissionDenied(Exception):
    pass


class ResearchUnavailable(Exception):
    pass


class SocialCapabilityNotSupported(Exception):
    code = "CAPABILITY_NOT_SUPPORTED"


class FetchPolicyError(Exception):
    def __init__(self, code: FetchFailureCode, *, rejected: bool = True) -> None:
        super().__init__(code.value)
        self.code, self.rejected = code, rejected


@dataclass(frozen=True, slots=True)
class FetchedPage:
    requested_url: str
    final_url: str
    http_status: int
    content_type: str
    body: bytes
    retrieved_at: datetime


class SourceRepository(Protocol):
    async def add(self, value: ResearchSource) -> None: ...
    async def get(self, value_id: UUID) -> ResearchSource | None: ...
    async def list_for_product(self, product_id: UUID) -> tuple[ResearchSource, ...]: ...
    async def update(
        self, value: ResearchSource, expected_status: ResearchSourceStatus
    ) -> None: ...


class FetchRepository(Protocol):
    async def add(self, value: SourceFetch) -> None: ...
    async def get(self, value_id: UUID) -> SourceFetch | None: ...
    async def list_for_source(self, source_id: UUID) -> tuple[SourceFetch, ...]: ...
    async def update(self, value: SourceFetch, expected_status: FetchStatus) -> None: ...
    async def active_for_source(self, source_id: UUID) -> SourceFetch | None: ...


class EvidenceRepository(Protocol):
    async def add(self, value: EvidenceSnapshot) -> None: ...
    async def get(self, value_id: UUID) -> EvidenceSnapshot | None: ...
    async def latest_for_source(self, source_id: UUID) -> EvidenceSnapshot | None: ...
    async def latest_for_product(self, product_id: UUID) -> tuple[EvidenceSnapshot, ...]: ...


class ResearchTargetRepository(Protocol):
    async def add(self, value: ResearchTarget) -> None: ...
    async def get(self, value_id: UUID) -> ResearchTarget | None: ...
    async def list_for_product(self, product_id: UUID) -> tuple[ResearchTarget, ...]: ...
    async def update(
        self, value: ResearchTarget, expected_status: ResearchSourceStatus
    ) -> None: ...


class SocialEvidenceRepository(Protocol):
    async def add(self, value: SocialEvidenceSnapshot) -> None: ...
    async def get(self, value_id: UUID) -> SocialEvidenceSnapshot | None: ...
    async def list_for_product(self, product_id: UUID) -> tuple[SocialEvidenceSnapshot, ...]: ...
    async def latest_matching(
        self,
        target_id: UUID,
        platform_content_id: str | None,
        semantic_digest: str,
    ) -> SocialEvidenceSnapshot | None: ...


class AnalysisAssetReader(Protocol):
    async def is_restricted_analysis_asset(self, asset_id: UUID, product_id: UUID) -> bool: ...


class ProductReferenceReader(Protocol):
    async def exists(self, product_id: UUID) -> bool: ...


class ResearchUnitOfWork(Protocol):
    sources: SourceRepository
    fetches: FetchRepository
    evidence: EvidenceRepository
    targets: ResearchTargetRepository
    social_evidence: SocialEvidenceRepository
    assets: AnalysisAssetReader
    products: ProductReferenceReader
    audit: AuditWriter
    outbox: OutboxWriter

    async def __aenter__(self) -> ResearchUnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class ResearchUnitOfWorkFactory(Protocol):
    def __call__(self, context: ExecutionContext) -> ResearchUnitOfWork: ...


class ResearchFetcher(Protocol):
    async def fetch(self, requested_url: str) -> FetchedPage: ...


class RawObjectStore(Protocol):
    async def put_private(self, *, key: str, content_type: str, body: bytes) -> None: ...


class SocialResearchProvider(Protocol):
    platform: SocialPlatform

    def capabilities(self) -> frozenset[str]: ...

    async def query(
        self, capability: str, target: ResearchTarget
    ) -> tuple[SocialEvidenceSnapshot, ...]: ...


@dataclass(frozen=True, slots=True)
class DisabledSocialResearchProvider:
    platform: SocialPlatform

    def capabilities(self) -> frozenset[str]:
        return frozenset()

    async def query(
        self, capability: str, target: ResearchTarget
    ) -> tuple[SocialEvidenceSnapshot, ...]:
        del capability, target
        raise SocialCapabilityNotSupported("official provider capability is not enabled")


def require_research_mutation(context: ExecutionContext) -> None:
    if context.membership_status is not MembershipStatus.ACTIVE or context.membership_role not in {
        MembershipRole.OWNER,
        MembershipRole.ADMIN,
    }:
        raise ResearchPermissionDenied("research mutations require an active owner or admin")


async def _event(
    uow: ResearchUnitOfWork,
    context: ExecutionContext,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    payload: dict[str, object],
) -> None:
    contracts = EventContractRegistry()
    await uow.outbox.append(
        tenant_event(
            context,
            event_type=event_type,
            schema_version=1,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            payload_schema_digest=contracts.schema_digest(event_type),
            occurred_at=datetime.now(UTC),
        )
    )


@dataclass(slots=True)
class ResearchService:
    uow_factory: ResearchUnitOfWorkFactory
    fetcher: ResearchFetcher
    object_store: RawObjectStore
    extractor: DeterministicEvidenceExtractor = field(
        default_factory=DeterministicEvidenceExtractor
    )
    telemetry: OperationalTelemetry = field(default_factory=NullTelemetry)
    social_providers: dict[SocialPlatform, SocialResearchProvider] = field(default_factory=dict)

    def social_capabilities(self) -> dict[SocialPlatform, frozenset[str]]:
        return {
            platform: self.social_providers.get(
                platform, DisabledSocialResearchProvider(platform)
            ).capabilities()
            for platform in SocialPlatform
        }

    async def create_target(
        self,
        context: ExecutionContext,
        *,
        product_id: UUID,
        kind: ResearchTargetKind,
        display_name: str,
        website_url: str | None = None,
        platform: SocialPlatform | None = None,
        platform_handle: str | None = None,
        platform_profile_url: str | None = None,
        platform_identifier: str | None = None,
    ) -> ResearchTarget:
        require_research_mutation(context)
        target = ResearchTarget(
            tenant_id=context.tenant_id,
            product_id=product_id,
            kind=kind,
            display_name=display_name,
            website_url=website_url,
            platform=platform,
            platform_handle=platform_handle,
            platform_profile_url=platform_profile_url,
            platform_identifier=platform_identifier,
            created_by=context.user_id,
        )
        async with self.uow_factory(context) as uow:
            if not await uow.products.exists(product_id):
                raise ResearchNotFound("product not found")
            await uow.targets.add(target)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="research.target.created",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="research_target",
                    resource_id=str(target.id),
                    metadata=safe_metadata(
                        {
                            "product_id": str(product_id),
                            "kind": kind.value,
                            "platform": platform.value if platform else "none",
                        }
                    ),
                )
            )
            await uow.commit()
        return target

    async def list_targets(
        self, context: ExecutionContext, product_id: UUID
    ) -> tuple[ResearchTarget, ...]:
        async with self.uow_factory(context) as uow:
            if not await uow.products.exists(product_id):
                raise ResearchNotFound("product not found")
            return await uow.targets.list_for_product(product_id)

    async def archive_target(self, context: ExecutionContext, target_id: UUID) -> ResearchTarget:
        require_research_mutation(context)
        async with self.uow_factory(context) as uow:
            current = await uow.targets.get(target_id)
            if current is None:
                raise ResearchNotFound("research target not found")
            archived = current.archive()
            if archived is current:
                return current
            await uow.targets.update(archived, current.status)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="research.target.archived",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="research_target",
                    resource_id=str(target_id),
                    metadata=safe_metadata(
                        {"product_id": str(current.product_id), "status": "archived"}
                    ),
                )
            )
            await uow.commit()
            return archived

    async def add_manual_social_evidence(
        self,
        context: ExecutionContext,
        *,
        target_id: UUID,
        platform: SocialPlatform,
        evidence_type: SocialEvidenceType,
        source_url: str | None = None,
        destination_url: str | None = None,
        advertiser_name: str | None = None,
        platform_content_id: str | None = None,
        headline: str | None = None,
        body_text: str | None = None,
        cta: str | None = None,
        media_type: str | None = None,
        placements: tuple[str, ...] = (),
        first_seen_at: datetime | None = None,
        last_seen_at: datetime | None = None,
        activity_status: str | None = None,
        region: str | None = None,
        media_asset_id: UUID | None = None,
    ) -> SocialEvidenceSnapshot:
        require_research_mutation(context)
        async with self.uow_factory(context) as uow:
            target = await uow.targets.get(target_id)
            if target is None:
                raise ResearchNotFound("research target not found")
            if target.status is not ResearchSourceStatus.ACTIVE:
                raise ResearchConflict("archived research target cannot receive evidence")
            if target.platform is not None and target.platform is not platform:
                raise ResearchConflict("evidence platform does not match research target")
            if media_asset_id is not None and not await uow.assets.is_restricted_analysis_asset(
                media_asset_id, target.product_id
            ):
                raise ResearchConflict("competitor media must be a restricted analysis-only asset")
            candidate = SocialEvidenceSnapshot(
                tenant_id=context.tenant_id,
                product_id=target.product_id,
                research_target_id=target.id,
                platform=platform,
                evidence_type=evidence_type,
                provenance=SocialEvidenceProvenance.USER_PROVIDED,
                captured_by=context.user_id,
                captured_at=datetime.now(UTC),
                semantic_digest="",
                source_url=source_url,
                destination_url=destination_url,
                advertiser_name=advertiser_name,
                platform_content_id=platform_content_id,
                headline=headline,
                body_text=body_text,
                cta=cta,
                media_type=media_type,
                placements=placements,
                first_seen_at=first_seen_at,
                last_seen_at=last_seen_at,
                activity_status=activity_status,
                region=region,
                media_asset_id=media_asset_id,
            )
            previous = await uow.social_evidence.latest_matching(
                target.id, platform_content_id, candidate.semantic_digest
            )
            if previous is not None:
                return previous
            await uow.social_evidence.add(candidate)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="research.social_evidence.captured",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="social_evidence_snapshot",
                    resource_id=str(candidate.id),
                    after_digest=candidate.semantic_digest,
                    metadata=safe_metadata(
                        {
                            "target_id": str(target.id),
                            "product_id": str(target.product_id),
                            "platform": platform.value,
                            "provenance": candidate.provenance.value,
                            "rights_status": candidate.rights_status,
                        }
                    ),
                )
            )
            await uow.commit()
            return candidate

    async def list_social_evidence(
        self, context: ExecutionContext, product_id: UUID
    ) -> tuple[SocialEvidenceSnapshot, ...]:
        async with self.uow_factory(context) as uow:
            if not await uow.products.exists(product_id):
                raise ResearchNotFound("product not found")
            return await uow.social_evidence.list_for_product(product_id)

    async def get_social_evidence(
        self, context: ExecutionContext, evidence_id: UUID
    ) -> SocialEvidenceSnapshot:
        async with self.uow_factory(context) as uow:
            value = await uow.social_evidence.get(evidence_id)
            if value is None:
                raise ResearchNotFound("social evidence not found")
            return value

    async def query_social_provider(
        self, context: ExecutionContext, target_id: UUID, capability: str
    ) -> tuple[SocialEvidenceSnapshot, ...]:
        require_research_mutation(context)
        async with self.uow_factory(context) as uow:
            target = await uow.targets.get(target_id)
        if target is None:
            raise ResearchNotFound("research target not found")
        if target.platform is None:
            raise SocialCapabilityNotSupported("target has no social platform")
        provider = self.social_providers.get(
            target.platform, DisabledSocialResearchProvider(target.platform)
        )
        if capability not in provider.capabilities():
            raise SocialCapabilityNotSupported("official provider capability is not supported")
        captured = await provider.query(capability, target)
        if len(captured) > 100:
            raise ResearchConflict("provider result exceeds the bounded capture limit")
        persisted: list[SocialEvidenceSnapshot] = []
        async with self.uow_factory(context) as uow:
            current = await uow.targets.get(target_id)
            if current is None or current.status is not ResearchSourceStatus.ACTIVE:
                raise ResearchConflict("research target is no longer active")
            for candidate in captured:
                if (
                    candidate.tenant_id != context.tenant_id
                    or candidate.product_id != current.product_id
                    or candidate.research_target_id != current.id
                    or candidate.platform is not current.platform
                    or candidate.provenance is not SocialEvidenceProvenance.PROVIDER_FETCHED
                    or not candidate.source_provider
                ):
                    raise ResearchConflict("provider returned evidence outside its governed target")
                previous = await uow.social_evidence.latest_matching(
                    current.id, candidate.platform_content_id, candidate.semantic_digest
                )
                value = previous or candidate
                if previous is None:
                    await uow.social_evidence.add(candidate)
                    await uow.audit.append(
                        tenant_audit(
                            context,
                            action="research.social_evidence.captured",
                            outcome=AuditOutcome.SUCCESS,
                            resource_type="social_evidence_snapshot",
                            resource_id=str(candidate.id),
                            after_digest=candidate.semantic_digest,
                            metadata=safe_metadata(
                                {
                                    "target_id": str(current.id),
                                    "product_id": str(current.product_id),
                                    "platform": candidate.platform.value,
                                    "provenance": candidate.provenance.value,
                                    "provider": candidate.source_provider,
                                    "rights_status": candidate.rights_status,
                                }
                            ),
                        )
                    )
                persisted.append(value)
            if captured:
                await uow.commit()
        return tuple(persisted)

    async def create_source(
        self,
        context: ExecutionContext,
        *,
        product_id: UUID,
        url: str,
        display_name: str,
        category: ResearchCategory,
        refresh: bool = True,
    ) -> tuple[ResearchSource, SourceFetch | None]:
        require_research_mutation(context)
        source = ResearchSource(
            tenant_id=context.tenant_id,
            product_id=product_id,
            canonical_url=canonicalize_url(url),
            display_name=display_name,
            category=category,
            created_by=context.user_id,
        )
        async with self.uow_factory(context) as uow:
            if not await uow.products.exists(product_id):
                raise ResearchNotFound("product not found")
            await uow.sources.add(source)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="research.source.created",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="research_source",
                    resource_id=str(source.id),
                    metadata=safe_metadata(
                        {
                            "product_id": str(product_id),
                            "category": category.value,
                            "status": "active",
                        }
                    ),
                )
            )
            await _event(
                uow,
                context,
                "research.source.created.v1",
                "research_source",
                source.id,
                {
                    "source_id": str(source.id),
                    "product_id": str(product_id),
                    "category": category.value,
                },
            )
            await uow.commit()
        return source, await self.refresh_source(context, source.id) if refresh else None

    async def list_sources(
        self, context: ExecutionContext, product_id: UUID
    ) -> tuple[ResearchSource, ...]:
        async with self.uow_factory(context) as uow:
            if not await uow.products.exists(product_id):
                raise ResearchNotFound("product not found")
            return await uow.sources.list_for_product(product_id)

    async def get_source(self, context: ExecutionContext, source_id: UUID) -> ResearchSource:
        async with self.uow_factory(context) as uow:
            value = await uow.sources.get(source_id)
            if value is None:
                raise ResearchNotFound("source not found")
            return value

    async def list_fetches(
        self, context: ExecutionContext, source_id: UUID
    ) -> tuple[SourceFetch, ...]:
        async with self.uow_factory(context) as uow:
            if await uow.sources.get(source_id) is None:
                raise ResearchNotFound("source not found")
            return await uow.fetches.list_for_source(source_id)

    async def get_evidence(self, context: ExecutionContext, evidence_id: UUID) -> EvidenceSnapshot:
        async with self.uow_factory(context) as uow:
            value = await uow.evidence.get(evidence_id)
            if value is None:
                raise ResearchNotFound("evidence not found")
            return value

    async def archive_source(self, context: ExecutionContext, source_id: UUID) -> ResearchSource:
        require_research_mutation(context)
        async with self.uow_factory(context) as uow:
            current = await uow.sources.get(source_id)
            if current is None:
                raise ResearchNotFound("source not found")
            archived = current.archive()
            if archived is current:
                return current
            await uow.sources.update(archived, current.status)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="research.source.archived",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="research_source",
                    resource_id=str(source_id),
                    metadata=safe_metadata(
                        {"product_id": str(current.product_id), "status": "archived"}
                    ),
                )
            )
            await _event(
                uow,
                context,
                "research.source.archived.v1",
                "research_source",
                source_id,
                {"source_id": str(source_id), "product_id": str(current.product_id)},
            )
            await uow.commit()
            return archived

    async def refresh_source(self, context: ExecutionContext, source_id: UUID) -> SourceFetch:
        require_research_mutation(context)
        timer_started = monotonic()
        now = datetime.now(UTC)
        async with self.uow_factory(context) as uow:
            source = await uow.sources.get(source_id)
            if source is None:
                raise ResearchNotFound("source not found")
            if source.status is not ResearchSourceStatus.ACTIVE:
                raise ResearchConflict("archived source cannot be refreshed")
            active = await uow.fetches.active_for_source(source_id)
            if active is not None:
                raise ResearchConflict("source already has an active fetch")
            fetch_id = uuid4()
            attempt = SourceFetch(
                id=fetch_id,
                tenant_id=context.tenant_id,
                source_id=source_id,
                requested_url=source.canonical_url,
                requested_by=context.user_id,
                raw_object_key=(
                    f"tenants/{context.tenant_id}/research/{source_id}/{fetch_id}/raw/source"
                ),
            ).begin(now)
            await uow.fetches.add(attempt)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="research.source.refreshed",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="source_fetch",
                    resource_id=str(attempt.id),
                    metadata=safe_metadata(
                        {
                            "source_id": str(source.id),
                            "category": source.category.value,
                            "status": attempt.status.value,
                        }
                    ),
                )
            )
            await uow.commit()

        dimensions = {"source.category": source.category.value}
        self.telemetry.count("research.fetch.attempts", attributes=dimensions)
        try:
            page = await self.fetcher.fetch(source.canonical_url)
        except FetchPolicyError as error:
            return await self._finish_failure(
                context, attempt, error.code, error.rejected, dimensions, timer_started
            )
        except Exception:
            return await self._finish_failure(
                context,
                attempt,
                FetchFailureCode.HTTP_ERROR,
                False,
                dimensions,
                timer_started,
            )

        raw_digest = sha256_bytes(page.body)
        try:
            await self.object_store.put_private(
                key=attempt.raw_object_key, content_type=page.content_type, body=page.body
            )
        except Exception:
            return await self._finish_failure(
                context,
                attempt,
                FetchFailureCode.STORAGE_FAILED,
                False,
                dimensions,
                timer_started,
            )

        try:
            extracted = self.extractor.extract(page.body, page.content_type, page.final_url)
            self.telemetry.count(
                "research.extraction.results",
                attributes={
                    **dimensions,
                    "result": "succeeded",
                    "content.type.class": "text",
                },
            )
        except Exception:
            self.telemetry.count(
                "research.extraction.results",
                attributes={**dimensions, "result": "failed"},
            )
            return await self._finish_failure(
                context,
                attempt,
                FetchFailureCode.EXTRACTION_FAILED,
                False,
                dimensions,
                timer_started,
            )

        captured = EvidenceSnapshot(
            tenant_id=context.tenant_id,
            product_id=source.product_id,
            source_id=source.id,
            source_fetch_id=attempt.id,
            final_url=page.final_url,
            title=extracted.title,
            blocks=extracted.blocks,
            outbound_links=extracted.outbound_links,
            structured_metadata=extracted.structured_metadata,
            raw_digest=raw_digest,
            semantic_digest=extracted.semantic_digest,
            captured_at=page.retrieved_at,
            extractor_version=self.extractor.version,
            instruction_like_content=extracted.instruction_like_content,
        )
        async with self.uow_factory(context) as uow:
            current = await uow.fetches.get(attempt.id)
            if current is None or current.status is not FetchStatus.FETCHING:
                raise ResearchConflict("fetch lifecycle changed concurrently")
            previous = await uow.evidence.latest_for_source(source.id)
            evidence = (
                previous
                if previous and previous.semantic_digest == captured.semantic_digest
                else captured
            )
            if evidence is captured:
                await uow.evidence.add(captured)
            complete = current.succeed(
                final_url=page.final_url,
                http_status=page.http_status,
                content_type=page.content_type,
                raw_digest=raw_digest,
                raw_byte_size=len(page.body),
                evidence_snapshot_id=evidence.id,
                now=datetime.now(UTC),
            )
            await uow.fetches.update(complete, FetchStatus.FETCHING)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="research.evidence.capture",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="evidence_snapshot",
                    resource_id=str(evidence.id),
                    after_digest=evidence.semantic_digest,
                    metadata=safe_metadata(
                        {
                            "source_id": str(source.id),
                            "fetch_id": str(attempt.id),
                            "category": source.category.value,
                            "content_type_class": "text",
                            "reused": evidence is previous,
                        }
                    ),
                )
            )
            if evidence is captured:
                await _event(
                    uow,
                    context,
                    "research.evidence.captured.v1",
                    "evidence_snapshot",
                    evidence.id,
                    {
                        "evidence_snapshot_id": str(evidence.id),
                        "source_id": str(source.id),
                        "product_id": str(source.product_id),
                        "fetch_id": str(attempt.id),
                        "category": source.category.value,
                        "semantic_digest": evidence.semantic_digest,
                        "raw_digest": evidence.raw_digest,
                        "changed_from_previous": previous is not None,
                        "retrieved_at": evidence.captured_at.astimezone(UTC).isoformat(),
                        "instruction_like_content": evidence.instruction_like_content,
                    },
                )
            await uow.commit()
        self.telemetry.count(
            "research.fetch.results", attributes={**dimensions, "result": "succeeded"}
        )
        self.telemetry.duration(
            "research.fetch.duration", monotonic() - timer_started, attributes=dimensions
        )
        return complete

    async def _finish_failure(
        self,
        context: ExecutionContext,
        attempt: SourceFetch,
        code: FetchFailureCode,
        rejected: bool,
        dimensions: dict[str, str],
        timer_started: float,
    ) -> SourceFetch:
        async with self.uow_factory(context) as uow:
            current = await uow.fetches.get(attempt.id)
            if current is None:
                raise ResearchConflict("fetch attempt disappeared")
            terminal = current.stop(rejected=rejected, code=code, now=datetime.now(UTC))
            await uow.fetches.update(terminal, current.status)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="research.fetch.rejected" if rejected else "research.fetch.failed",
                    outcome=AuditOutcome.DENIED if rejected else AuditOutcome.FAILED,
                    reason_code=code.value,
                    resource_type="source_fetch",
                    resource_id=str(attempt.id),
                    metadata=safe_metadata(
                        {"source_id": str(attempt.source_id), "status": terminal.status.value}
                    ),
                )
            )
            await uow.commit()
        self.telemetry.count(
            "research.fetch.results",
            attributes={**dimensions, "result": terminal.status.value},
        )
        self.telemetry.duration(
            "research.fetch.duration", monotonic() - timer_started, attributes=dimensions
        )
        return terminal

    async def manifest(
        self, context: ExecutionContext, product_id: UUID
    ) -> ResearchContextManifest:
        async with self.uow_factory(context) as uow:
            if not await uow.products.exists(product_id):
                raise ResearchNotFound("product not found")
            sources = {
                item.id: item
                for item in await uow.sources.list_for_product(product_id)
                if item.status is ResearchSourceStatus.ACTIVE
            }
            evidence = await uow.evidence.latest_for_product(product_id)
            social_repository = getattr(uow, "social_evidence", None)
            targets_repository = getattr(uow, "targets", None)
            social = (
                await social_repository.list_for_product(product_id)
                if social_repository is not None
                else ()
            )
            active_targets = (
                {
                    item.id
                    for item in await targets_repository.list_for_product(product_id)
                    if item.status is ResearchSourceStatus.ACTIVE
                }
                if targets_repository is not None
                else set()
            )
        refs = tuple(
            ResearchEvidenceReference(
                source_id=item.source_id,
                evidence_snapshot_id=item.id,
                semantic_digest=item.semantic_digest,
                category=sources[item.source_id].category,
                captured_at=item.captured_at,
            )
            for item in evidence
            if item.source_id in sources
        )
        latest_social: list[SocialEvidenceSnapshot] = []
        seen_social: set[tuple[UUID, str]] = set()
        for item in sorted(social, key=lambda value: value.captured_at, reverse=True):
            identity = item.platform_content_id or item.source_url or str(item.id)
            key = (item.research_target_id, identity)
            if item.research_target_id not in active_targets or key in seen_social:
                continue
            seen_social.add(key)
            latest_social.append(item)
        social_refs = tuple(
            SocialEvidenceReference(
                item.research_target_id,
                item.id,
                item.semantic_digest,
                item.platform,
                item.captured_at,
            )
            for item in latest_social[:20]
        )
        return ResearchContextManifest.build(context.tenant_id, product_id, refs, social_refs)
