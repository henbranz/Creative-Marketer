from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.measurement.application import MeasurablePublication
from creative_marketer.measurement.domain import (
    AttributionMethod,
    AttributionReference,
    AttributionResult,
    CollectionRun,
    CollectionStatus,
    ConversionObservation,
    DerivedMetric,
    Freshness,
    MetricSemantics,
    PerformanceObservation,
    PerformanceSnapshot,
)

from .measurement_schema import (
    attribution_references,
    attribution_results,
    collection_runs,
    conversion_observations,
    performance_observations,
    performance_snapshots,
)
from .publishing_schema import publication_drafts, publications


def _observation(row: Mapping[str, Any]) -> PerformanceObservation:
    return PerformanceObservation(
        row["tenant_id"],
        row["product_id"],
        row["publication_id"],
        row["metric_key"],
        MetricSemantics(row["semantics"]),
        Decimal(row["value"]),
        row["unit"],
        row["observed_at"],
        row["provider"],
        row["provider_version"],
        row["source_digest"],
        row["id"],
        row["created_at"],
    )


def _reference(row: Mapping[str, Any]) -> AttributionReference:
    return AttributionReference(
        row["tenant_id"],
        row["publication_id"],
        row["public_code_hash"],
        row["destination_url"],
        row["id"],
        row["created_at"],
    )


def _conversion(row: Mapping[str, Any]) -> ConversionObservation:
    return ConversionObservation(
        row["tenant_id"],
        row["source"],
        row["external_id"],
        Decimal(row["amount"]),
        row["currency"],
        row["observed_at"],
        row["attribution_code_hash"],
        row["id"],
        row["created_at"],
    )


def _result(row: Mapping[str, Any]) -> AttributionResult:
    return AttributionResult(
        row["tenant_id"],
        row["conversion_observation_id"],
        row["attribution_reference_id"],
        row["publication_id"],
        AttributionMethod(row["method"]),
        row["formula_version"],
        row["id"],
        row["created_at"],
    )


def _snapshot(row: Mapping[str, Any]) -> PerformanceSnapshot:
    derived = tuple(
        DerivedMetric(
            item["key"],
            Decimal(item["value"]) if item["value"] is not None else None,
            item["formula_version"],
            item.get("unavailable_reason"),
        )
        for item in row["derived_metrics"]
    )
    return PerformanceSnapshot(
        row["tenant_id"],
        row["product_id"],
        row["publication_id"],
        tuple(row["observation_ids"]),
        {key: Decimal(value) for key, value in row["latest_metrics"].items()},
        derived,
        row["attributed_conversions"],
        {key: Decimal(value) for key, value in row["attributed_revenue"].items()},
        Freshness(row["freshness"]),
        row["semantic_digest"],
        row["id"],
        row["created_at"],
    )


class SqlAlchemyMeasurementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def publication(self, publication_id: UUID) -> MeasurablePublication | None:
        row = (
            (
                await self.session.execute(
                    select(
                        publications.c.tenant_id,
                        publications.c.product_id,
                        publications.c.id,
                        publications.c.external_post_id,
                        publication_drafts.c.destination_url,
                    )
                    .join(
                        publication_drafts,
                        (publication_drafts.c.tenant_id == publications.c.tenant_id)
                        & (publication_drafts.c.id == publications.c.publication_draft_id),
                    )
                    .where(publications.c.id == publication_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        return (
            MeasurablePublication(
                row["tenant_id"],
                row["product_id"],
                row["id"],
                row["external_post_id"],
                row["destination_url"],
            )
            if row
            else None
        )

    async def observations(self, publication_id: UUID) -> tuple[PerformanceObservation, ...]:
        rows = (
            await self.session.execute(
                select(performance_observations)
                .where(performance_observations.c.publication_id == publication_id)
                .order_by(performance_observations.c.observed_at, performance_observations.c.id)
            )
        ).mappings()
        return tuple(_observation(cast(Mapping[str, Any], row)) for row in rows)

    async def add_observation(self, value: PerformanceObservation) -> bool:
        result = await self.session.execute(
            pg_insert(performance_observations)
            .values(
                id=value.id,
                tenant_id=value.tenant_id,
                product_id=value.product_id,
                publication_id=value.publication_id,
                metric_key=value.metric_key,
                semantics=value.semantics.value,
                value=value.value,
                unit=value.unit,
                observed_at=value.observed_at,
                provider=value.provider,
                provider_version=value.provider_version,
                source_digest=value.source_digest,
                created_at=value.created_at,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "source_digest"])
        )
        return bool(cast(CursorResult[Any], result).rowcount)

    async def cursor(self, publication_id: UUID) -> str | None:
        return await self.session.scalar(
            select(collection_runs.c.cursor)
            .where(
                collection_runs.c.publication_id == publication_id,
                collection_runs.c.status == CollectionStatus.SUCCEEDED.value,
            )
            .order_by(collection_runs.c.completed_at.desc())
            .limit(1)
        )

    async def add_run(self, value: CollectionRun) -> None:
        await self.session.execute(
            insert(collection_runs).values(
                id=value.id,
                tenant_id=value.tenant_id,
                publication_id=value.publication_id,
                status=value.status.value,
                checkpoint=value.checkpoint,
                cursor=value.cursor,
                failure_code=value.failure_code,
                created_at=value.created_at,
                completed_at=value.completed_at,
            )
        )

    async def latest_snapshot(self, publication_id: UUID) -> PerformanceSnapshot | None:
        row = (
            (
                await self.session.execute(
                    select(performance_snapshots)
                    .where(performance_snapshots.c.publication_id == publication_id)
                    .order_by(
                        performance_snapshots.c.created_at.desc(), performance_snapshots.c.id.desc()
                    )
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        return _snapshot(cast(Mapping[str, Any], row)) if row else None

    async def add_snapshot(self, value: PerformanceSnapshot) -> bool:
        result = await self.session.execute(
            pg_insert(performance_snapshots)
            .values(
                id=value.id,
                tenant_id=value.tenant_id,
                product_id=value.product_id,
                publication_id=value.publication_id,
                observation_ids=list(value.observation_ids),
                latest_metrics={key: str(item) for key, item in value.latest_metrics.items()},
                derived_metrics=[
                    {
                        "key": item.key,
                        "value": str(item.value) if item.value is not None else None,
                        "formula_version": item.formula_version,
                        "unavailable_reason": item.unavailable_reason,
                    }
                    for item in value.derived_metrics
                ],
                attributed_conversions=value.attributed_conversions,
                attributed_revenue={
                    key: str(item) for key, item in value.attributed_revenue.items()
                },
                freshness=value.freshness.value,
                semantic_digest=value.semantic_digest,
                created_at=value.created_at,
            )
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "publication_id", "semantic_digest"]
            )
        )
        return bool(cast(CursorResult[Any], result).rowcount)

    async def add_reference(self, value: AttributionReference) -> None:
        await self.session.execute(
            insert(attribution_references).values(
                id=value.id,
                tenant_id=value.tenant_id,
                publication_id=value.publication_id,
                public_code_hash=value.public_code_hash,
                destination_url=value.destination_url,
                created_at=value.created_at,
            )
        )

    async def reference_by_hash(self, code_hash: str) -> AttributionReference | None:
        row = (
            (
                await self.session.execute(
                    select(attribution_references).where(
                        attribution_references.c.public_code_hash == code_hash
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return _reference(cast(Mapping[str, Any], row)) if row else None

    async def references(self, publication_id: UUID) -> tuple[AttributionReference, ...]:
        rows = (
            await self.session.execute(
                select(attribution_references).where(
                    attribution_references.c.publication_id == publication_id
                )
            )
        ).mappings()
        return tuple(_reference(cast(Mapping[str, Any], row)) for row in rows)

    async def conversion_by_external_id(
        self, source: str, external_id: str
    ) -> ConversionObservation | None:
        row = (
            (
                await self.session.execute(
                    select(conversion_observations).where(
                        conversion_observations.c.source == source,
                        conversion_observations.c.external_id == external_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return _conversion(cast(Mapping[str, Any], row)) if row else None

    async def add_conversion(self, value: ConversionObservation) -> None:
        await self.session.execute(
            insert(conversion_observations).values(
                id=value.id,
                tenant_id=value.tenant_id,
                source=value.source,
                external_id=value.external_id,
                amount=value.amount,
                currency=value.currency,
                observed_at=value.observed_at,
                attribution_code_hash=value.attribution_code_hash,
                semantic_digest=value.semantic_digest,
                created_at=value.created_at,
            )
        )

    async def add_attribution(self, value: AttributionResult) -> None:
        await self.session.execute(
            insert(attribution_results).values(
                id=value.id,
                tenant_id=value.tenant_id,
                conversion_observation_id=value.conversion_observation_id,
                attribution_reference_id=value.attribution_reference_id,
                publication_id=value.publication_id,
                method=value.method.value,
                formula_version=value.formula_version,
                created_at=value.created_at,
            )
        )

    async def attributed_conversions(
        self, publication_id: UUID
    ) -> tuple[tuple[ConversionObservation, AttributionResult], ...]:
        result_rows = (
            await self.session.execute(
                select(attribution_results).where(
                    attribution_results.c.publication_id == publication_id
                )
            )
        ).mappings()
        results = tuple(_result(cast(Mapping[str, Any], row)) for row in result_rows)
        if not results:
            return ()
        conversion_rows = (
            await self.session.execute(
                select(conversion_observations).where(
                    conversion_observations.c.id.in_(
                        [item.conversion_observation_id for item in results]
                    )
                )
            )
        ).mappings()
        conversions = {
            item.id: item
            for item in (_conversion(cast(Mapping[str, Any], row)) for row in conversion_rows)
        }
        return tuple((conversions[item.conversion_observation_id], item) for item in results)

    async def attributed_conversions_for_conversion(
        self, conversion_id: UUID
    ) -> tuple[AttributionResult, ...]:
        rows = (
            await self.session.execute(
                select(attribution_results).where(
                    attribution_results.c.conversion_observation_id == conversion_id
                )
            )
        ).mappings()
        return tuple(_result(cast(Mapping[str, Any], row)) for row in rows)

    async def snapshots_for_product(self, product_id: UUID) -> tuple[PerformanceSnapshot, ...]:
        latest_ids = (
            select(performance_snapshots.c.id)
            .where(performance_snapshots.c.product_id == product_id)
            .distinct(performance_snapshots.c.publication_id)
            .order_by(
                performance_snapshots.c.publication_id,
                performance_snapshots.c.created_at.desc(),
                performance_snapshots.c.id.desc(),
            )
        )
        rows = (
            await self.session.execute(
                select(performance_snapshots)
                .where(performance_snapshots.c.id.in_(latest_ids))
                .order_by(performance_snapshots.c.created_at.desc())
            )
        ).mappings()
        return tuple(_snapshot(cast(Mapping[str, Any], row)) for row in rows)
