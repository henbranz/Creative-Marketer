from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.infrastructure.database.catalog_schema import assets, products
from creative_marketer.infrastructure.database.research_schema import (
    evidence_snapshots,
    research_targets,
    social_evidence_snapshots,
    source_fetches,
    sources,
)
from creative_marketer.research.application import ResearchConflict
from creative_marketer.research.domain import (
    EvidenceBlock,
    EvidenceBlockKind,
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
    SourceType,
)


def _data(row: object) -> Any:
    return row._mapping  # type: ignore[attr-defined]


def _source(row: object) -> ResearchSource:
    d = _data(row)
    return ResearchSource(
        id=d["id"],
        tenant_id=d["tenant_id"],
        product_id=d["product_id"],
        source_type=SourceType(d["source_type"]),
        canonical_url=d["canonical_url"],
        display_name=d["display_name"],
        category=ResearchCategory(d["category"]),
        status=ResearchSourceStatus(d["status"]),
        created_by=d["created_by"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


def _fetch(row: object) -> SourceFetch:
    d = _data(row)
    return SourceFetch(
        id=d["id"],
        tenant_id=d["tenant_id"],
        source_id=d["source_id"],
        requested_url=d["requested_url"],
        requested_by=d["requested_by"],
        raw_object_key=d["raw_object_key"],
        status=FetchStatus(d["status"]),
        final_url=d["final_url"],
        http_status=d["http_status"],
        content_type=d["content_type"],
        raw_digest=d["raw_digest"],
        raw_byte_size=d["raw_byte_size"],
        evidence_snapshot_id=d["evidence_snapshot_id"],
        failure_code=FetchFailureCode(d["failure_code"]) if d["failure_code"] else None,
        started_at=d["started_at"],
        completed_at=d["completed_at"],
        created_at=d["created_at"],
    )


def _evidence(row: object) -> EvidenceSnapshot:
    d = _data(row)
    blocks = tuple(
        EvidenceBlock(EvidenceBlockKind(item["kind"]), item["text"], item["ordinal"])
        for item in d["content_blocks"]
    )
    return EvidenceSnapshot(
        id=d["id"],
        tenant_id=d["tenant_id"],
        product_id=d["product_id"],
        source_id=d["source_id"],
        source_fetch_id=d["source_fetch_id"],
        final_url=d["final_url"],
        title=d["title"],
        blocks=blocks,
        outbound_links=tuple(d["outbound_links"]),
        structured_metadata=dict(d["structured_metadata"]),
        raw_digest=d["raw_digest"],
        semantic_digest=d["semantic_digest"],
        schema_version=d["schema_version"],
        extractor_version=d["extractor_version"],
        instruction_like_content=d["instruction_like_content"],
        captured_at=d["captured_at"],
    )


def _target(row: object) -> ResearchTarget:
    d = _data(row)
    return ResearchTarget(
        id=d["id"],
        tenant_id=d["tenant_id"],
        product_id=d["product_id"],
        kind=ResearchTargetKind(d["kind"]),
        display_name=d["display_name"],
        website_url=d["website_url"],
        platform=SocialPlatform(d["platform"]) if d["platform"] else None,
        platform_handle=d["platform_handle"],
        platform_profile_url=d["platform_profile_url"],
        platform_identifier=d["platform_identifier"],
        status=ResearchSourceStatus(d["status"]),
        created_by=d["created_by"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


def _social_evidence(row: object) -> SocialEvidenceSnapshot:
    d = _data(row)
    return SocialEvidenceSnapshot(
        id=d["id"],
        tenant_id=d["tenant_id"],
        product_id=d["product_id"],
        research_target_id=d["research_target_id"],
        platform=SocialPlatform(d["platform"]),
        evidence_type=SocialEvidenceType(d["evidence_type"]),
        provenance=SocialEvidenceProvenance(d["provenance"]),
        source_url=d["source_url"],
        destination_url=d["destination_url"],
        advertiser_name=d["advertiser_name"],
        advertiser_platform_id=d["advertiser_platform_id"],
        platform_content_id=d["platform_content_id"],
        headline=d["headline"],
        body_text=d["body_text"],
        cta=d["cta"],
        ad_objective=d["ad_objective"],
        media_type=d["media_type"],
        placements=tuple(d["placements"]),
        first_seen_at=d["first_seen_at"],
        last_seen_at=d["last_seen_at"],
        activity_status=d["activity_status"],
        region=d["region"],
        reach_range=d["reach_range"],
        source_provider=d["source_provider"],
        media_asset_id=d["media_asset_id"],
        raw_provider_metadata_digest=d["raw_provider_metadata_digest"],
        semantic_digest=d["semantic_digest"],
        schema_version=d["schema_version"],
        rights_status=d["rights_status"],
        allowed_uses=tuple(d["allowed_uses"]),
        captured_by=d["captured_by"],
        captured_at=d["captured_at"],
    )


def _source_values(value: ResearchSource) -> dict[str, object]:
    return {
        "id": value.id,
        "tenant_id": value.tenant_id,
        "product_id": value.product_id,
        "source_type": value.source_type.value,
        "canonical_url": value.canonical_url,
        "display_name": value.display_name,
        "category": value.category.value,
        "status": value.status.value,
        "created_by": value.created_by,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
    }


def _fetch_values(value: SourceFetch) -> dict[str, object]:
    return {
        "id": value.id,
        "tenant_id": value.tenant_id,
        "source_id": value.source_id,
        "requested_url": value.requested_url,
        "requested_by": value.requested_by,
        "raw_object_key": value.raw_object_key,
        "status": value.status.value,
        "final_url": value.final_url,
        "http_status": value.http_status,
        "content_type": value.content_type,
        "raw_digest": value.raw_digest,
        "raw_byte_size": value.raw_byte_size,
        "evidence_snapshot_id": value.evidence_snapshot_id,
        "failure_code": value.failure_code.value if value.failure_code else None,
        "started_at": value.started_at,
        "completed_at": value.completed_at,
        "created_at": value.created_at,
    }


class SqlAlchemySourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, value: ResearchSource) -> None:
        try:
            await self.session.execute(insert(sources).values(**_source_values(value)))
        except IntegrityError as error:
            raise ResearchConflict("research source already exists") from error

    async def get(self, value_id: UUID) -> ResearchSource | None:
        row = (await self.session.execute(select(sources).where(sources.c.id == value_id))).first()
        return _source(row) if row else None

    async def list_for_product(self, product_id: UUID) -> tuple[ResearchSource, ...]:
        rows = (
            await self.session.execute(
                select(sources)
                .where(sources.c.product_id == product_id)
                .order_by(sources.c.created_at.desc())
            )
        ).all()
        return tuple(_source(row) for row in rows)

    async def update(self, value: ResearchSource, expected_status: ResearchSourceStatus) -> None:
        result = await self.session.execute(
            update(sources)
            .where(sources.c.id == value.id, sources.c.status == expected_status.value)
            .values(status=value.status.value, updated_at=value.updated_at)
        )
        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise RuntimeError("concurrent source update")


class SqlAlchemyFetchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, value: SourceFetch) -> None:
        try:
            await self.session.execute(insert(source_fetches).values(**_fetch_values(value)))
        except IntegrityError as error:
            raise ResearchConflict("source already has an active fetch") from error

    async def get(self, value_id: UUID) -> SourceFetch | None:
        row = (
            await self.session.execute(
                select(source_fetches).where(source_fetches.c.id == value_id)
            )
        ).first()
        return _fetch(row) if row else None

    async def list_for_source(self, source_id: UUID) -> tuple[SourceFetch, ...]:
        rows = (
            await self.session.execute(
                select(source_fetches)
                .where(source_fetches.c.source_id == source_id)
                .order_by(source_fetches.c.created_at.desc())
            )
        ).all()
        return tuple(_fetch(row) for row in rows)

    async def active_for_source(self, source_id: UUID) -> SourceFetch | None:
        row = (
            await self.session.execute(
                select(source_fetches).where(
                    source_fetches.c.source_id == source_id,
                    source_fetches.c.status.in_(["pending", "fetching"]),
                )
            )
        ).first()
        return _fetch(row) if row else None

    async def update(self, value: SourceFetch, expected_status: FetchStatus) -> None:
        result = await self.session.execute(
            update(source_fetches)
            .where(
                source_fetches.c.id == value.id, source_fetches.c.status == expected_status.value
            )
            .values(
                **{
                    k: v
                    for k, v in _fetch_values(value).items()
                    if k
                    not in {
                        "id",
                        "tenant_id",
                        "source_id",
                        "requested_url",
                        "requested_by",
                        "raw_object_key",
                        "created_at",
                    }
                }
            )
        )
        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise RuntimeError("concurrent fetch update")


class SqlAlchemyEvidenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, value: EvidenceSnapshot) -> None:
        await self.session.execute(
            insert(evidence_snapshots).values(
                id=value.id,
                tenant_id=value.tenant_id,
                product_id=value.product_id,
                source_id=value.source_id,
                source_fetch_id=value.source_fetch_id,
                final_url=value.final_url,
                title=value.title,
                content_blocks=[item.semantic() for item in value.blocks],
                outbound_links=list(value.outbound_links),
                structured_metadata=dict(value.structured_metadata),
                raw_digest=value.raw_digest,
                semantic_digest=value.semantic_digest,
                schema_version=value.schema_version,
                extractor_version=value.extractor_version,
                instruction_like_content=value.instruction_like_content,
                captured_at=value.captured_at,
            )
        )

    async def get(self, value_id: UUID) -> EvidenceSnapshot | None:
        row = (
            await self.session.execute(
                select(evidence_snapshots).where(evidence_snapshots.c.id == value_id)
            )
        ).first()
        return _evidence(row) if row else None

    async def latest_for_source(self, source_id: UUID) -> EvidenceSnapshot | None:
        row = (
            await self.session.execute(
                select(evidence_snapshots)
                .where(evidence_snapshots.c.source_id == source_id)
                .order_by(evidence_snapshots.c.captured_at.desc())
                .limit(1)
            )
        ).first()
        return _evidence(row) if row else None

    async def latest_for_product(self, product_id: UUID) -> tuple[EvidenceSnapshot, ...]:
        ranked = (
            select(
                evidence_snapshots,
                sources.c.category,
            )
            .join(sources, sources.c.id == evidence_snapshots.c.source_id)
            .where(
                evidence_snapshots.c.product_id == product_id,
                sources.c.status == "active",
            )
            .distinct(evidence_snapshots.c.source_id)
            .order_by(evidence_snapshots.c.source_id, evidence_snapshots.c.captured_at.desc())
            .subquery()
        )
        rows = (
            await self.session.execute(
                select(ranked).order_by(ranked.c.category, ranked.c.source_id).limit(50)
            )
        ).all()
        return tuple(_evidence(row) for row in rows)


class SqlAlchemyResearchTargetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, value: ResearchTarget) -> None:
        await self.session.execute(
            insert(research_targets).values(
                id=value.id,
                tenant_id=value.tenant_id,
                product_id=value.product_id,
                kind=value.kind.value,
                display_name=value.display_name,
                website_url=value.website_url,
                platform=value.platform.value if value.platform else None,
                platform_handle=value.platform_handle,
                platform_profile_url=value.platform_profile_url,
                platform_identifier=value.platform_identifier,
                status=value.status.value,
                created_by=value.created_by,
                created_at=value.created_at,
                updated_at=value.updated_at,
            )
        )

    async def get(self, value_id: UUID) -> ResearchTarget | None:
        row = (
            await self.session.execute(
                select(research_targets).where(research_targets.c.id == value_id)
            )
        ).first()
        return _target(row) if row else None

    async def list_for_product(self, product_id: UUID) -> tuple[ResearchTarget, ...]:
        rows = (
            await self.session.execute(
                select(research_targets)
                .where(research_targets.c.product_id == product_id)
                .order_by(research_targets.c.created_at.desc())
            )
        ).all()
        return tuple(_target(row) for row in rows)

    async def update(self, value: ResearchTarget, expected_status: ResearchSourceStatus) -> None:
        result = await self.session.execute(
            update(research_targets)
            .where(
                research_targets.c.id == value.id,
                research_targets.c.status == expected_status.value,
            )
            .values(status=value.status.value, updated_at=value.updated_at)
        )
        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise RuntimeError("concurrent research target update")


class SqlAlchemySocialEvidenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, value: SocialEvidenceSnapshot) -> None:
        await self.session.execute(
            insert(social_evidence_snapshots).values(
                **{
                    "id": value.id,
                    "tenant_id": value.tenant_id,
                    "product_id": value.product_id,
                    "research_target_id": value.research_target_id,
                    "platform": value.platform.value,
                    "evidence_type": value.evidence_type.value,
                    "provenance": value.provenance.value,
                    "source_url": value.source_url,
                    "destination_url": value.destination_url,
                    "advertiser_name": value.advertiser_name,
                    "advertiser_platform_id": value.advertiser_platform_id,
                    "platform_content_id": value.platform_content_id,
                    "headline": value.headline,
                    "body_text": value.body_text,
                    "cta": value.cta,
                    "ad_objective": value.ad_objective,
                    "media_type": value.media_type,
                    "placements": list(value.placements),
                    "first_seen_at": value.first_seen_at,
                    "last_seen_at": value.last_seen_at,
                    "activity_status": value.activity_status,
                    "region": value.region,
                    "reach_range": value.reach_range,
                    "source_provider": value.source_provider,
                    "media_asset_id": value.media_asset_id,
                    "raw_provider_metadata_digest": value.raw_provider_metadata_digest,
                    "semantic_digest": value.semantic_digest,
                    "schema_version": value.schema_version,
                    "rights_status": value.rights_status,
                    "allowed_uses": list(value.allowed_uses),
                    "captured_by": value.captured_by,
                    "captured_at": value.captured_at,
                }
            )
        )

    async def get(self, value_id: UUID) -> SocialEvidenceSnapshot | None:
        row = (
            await self.session.execute(
                select(social_evidence_snapshots).where(social_evidence_snapshots.c.id == value_id)
            )
        ).first()
        return _social_evidence(row) if row else None

    async def list_for_product(self, product_id: UUID) -> tuple[SocialEvidenceSnapshot, ...]:
        rows = (
            await self.session.execute(
                select(social_evidence_snapshots)
                .where(social_evidence_snapshots.c.product_id == product_id)
                .order_by(social_evidence_snapshots.c.captured_at.desc())
            )
        ).all()
        return tuple(_social_evidence(row) for row in rows)

    async def latest_matching(
        self, target_id: UUID, platform_content_id: str | None, semantic_digest: str
    ) -> SocialEvidenceSnapshot | None:
        query = select(social_evidence_snapshots).where(
            social_evidence_snapshots.c.research_target_id == target_id,
            social_evidence_snapshots.c.semantic_digest == semantic_digest,
        )
        if platform_content_id is not None:
            query = query.where(
                social_evidence_snapshots.c.platform_content_id == platform_content_id
            )
        row = (await self.session.execute(query.limit(1))).first()
        return _social_evidence(row) if row else None


class SqlAlchemyAnalysisAssetReader:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def is_restricted_analysis_asset(self, asset_id: UUID, product_id: UUID) -> bool:
        row = await self.session.scalar(
            select(assets.c.id).where(
                assets.c.id == asset_id,
                assets.c.product_id == product_id,
                assets.c.status == "ready",
                assets.c.rights_status == "restricted",
                assets.c.allowed_uses == ["internal_analysis"],
            )
        )
        return row is not None


class SqlAlchemyProductReferenceReader:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def exists(self, product_id: UUID) -> bool:
        return (
            await self.session.execute(select(products.c.id).where(products.c.id == product_id))
        ).scalar_one_or_none() is not None
