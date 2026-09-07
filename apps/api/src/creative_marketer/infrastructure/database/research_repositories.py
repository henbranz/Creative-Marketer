from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.infrastructure.database.catalog_schema import products
from creative_marketer.infrastructure.database.research_schema import (
    evidence_snapshots,
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


class SqlAlchemyProductReferenceReader:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def exists(self, product_id: UUID) -> bool:
        return (
            await self.session.execute(select(products.c.id).where(products.c.id == product_id))
        ).scalar_one_or_none() is not None
