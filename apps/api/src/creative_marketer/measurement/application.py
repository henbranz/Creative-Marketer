from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import TracebackType
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from uuid import UUID

from creative_marketer.audit.application import AuditWriter
from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditOutcome
from creative_marketer.audit.safety import safe_metadata
from creative_marketer.events.application import OutboxWriter
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import tenant_event
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.identity.domain import MembershipRole, MembershipStatus

from .domain import (
    AttributionReference,
    AttributionReferenceInvalid,
    AttributionResult,
    CollectionRun,
    CollectionStatus,
    ConversionConflict,
    ConversionObservation,
    Freshness,
    MeasurementNotFound,
    MeasurementPermissionDenied,
    PerformanceObservation,
    PerformanceSnapshot,
)
from .provider import SocialMetricsProvider


@dataclass(frozen=True, slots=True)
class MeasurablePublication:
    tenant_id: UUID
    product_id: UUID
    publication_id: UUID
    external_post_id: str
    destination_url: str | None


@dataclass(frozen=True, slots=True)
class IssuedAttributionReference:
    reference: AttributionReference
    public_code: str
    tracked_destination_url: str


class MeasurementRepository(Protocol):
    async def publication(self, publication_id: UUID) -> MeasurablePublication | None: ...
    async def observations(self, publication_id: UUID) -> tuple[PerformanceObservation, ...]: ...
    async def add_observation(self, value: PerformanceObservation) -> bool: ...
    async def cursor(self, publication_id: UUID) -> str | None: ...
    async def add_run(self, value: CollectionRun) -> None: ...
    async def latest_snapshot(self, publication_id: UUID) -> PerformanceSnapshot | None: ...
    async def add_snapshot(self, value: PerformanceSnapshot) -> bool: ...
    async def add_reference(self, value: AttributionReference) -> None: ...
    async def reference_by_hash(self, code_hash: str) -> AttributionReference | None: ...
    async def references(self, publication_id: UUID) -> tuple[AttributionReference, ...]: ...
    async def conversion_by_external_id(
        self, source: str, external_id: str
    ) -> ConversionObservation | None: ...
    async def add_conversion(self, value: ConversionObservation) -> None: ...
    async def add_attribution(self, value: AttributionResult) -> None: ...
    async def attributed_conversions(
        self, publication_id: UUID
    ) -> tuple[tuple[ConversionObservation, AttributionResult], ...]: ...
    async def attributed_conversions_for_conversion(
        self, conversion_id: UUID
    ) -> tuple[AttributionResult, ...]: ...
    async def snapshots_for_product(self, product_id: UUID) -> tuple[PerformanceSnapshot, ...]: ...


class MeasurementUnitOfWork(Protocol):
    measurement: MeasurementRepository
    audit: AuditWriter
    outbox: OutboxWriter

    async def __aenter__(self) -> MeasurementUnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class MeasurementUnitOfWorkFactory(Protocol):
    def __call__(self, tenant_id: UUID) -> MeasurementUnitOfWork: ...


@dataclass(slots=True)
class MeasurementService:
    uow_factory: MeasurementUnitOfWorkFactory
    provider: SocialMetricsProvider
    contracts: EventContractRegistry = field(default_factory=EventContractRegistry)

    @staticmethod
    def _write(context: ExecutionContext) -> None:
        if (
            context.membership_status is not MembershipStatus.ACTIVE
            or context.membership_role not in {MembershipRole.OWNER, MembershipRole.ADMIN}
        ):
            raise MeasurementPermissionDenied("OWNER or ADMIN membership required")

    async def collect(
        self, context: ExecutionContext, publication_id: UUID, *, checkpoint: str = "manual"
    ) -> PerformanceSnapshot:
        self._write(context)
        async with self.uow_factory(context.tenant_id) as uow:
            publication = await uow.measurement.publication(publication_id)
            if publication is None:
                raise MeasurementNotFound("publication not found")
            cursor = await uow.measurement.cursor(publication_id)
        started = CollectionRun(
            context.tenant_id, publication_id, CollectionStatus.RUNNING, checkpoint, cursor
        )
        try:
            page = await self.provider.collect(publication.external_post_id, cursor)
        except Exception as error:
            failed = CollectionRun(
                context.tenant_id,
                publication_id,
                CollectionStatus.FAILED,
                checkpoint,
                cursor,
                getattr(error, "code", "MEASUREMENT_PROVIDER_FAILED"),
                id=started.id,
                created_at=started.created_at,
                completed_at=datetime.now(UTC),
            )
            async with self.uow_factory(context.tenant_id) as uow:
                await uow.measurement.add_run(failed)
                await uow.audit.append(
                    tenant_audit(
                        context,
                        action="measurement.collection.failed",
                        outcome=AuditOutcome.FAILED,
                        resource_type="publication",
                        resource_id=str(publication_id),
                        metadata=safe_metadata({"failure_code": failed.failure_code}),
                    )
                )
                await uow.commit()
            raise

        async with self.uow_factory(context.tenant_id) as uow:
            for metric in page.metrics:
                if metric.available:
                    await uow.measurement.add_observation(
                        PerformanceObservation.from_provider(
                            tenant_id=context.tenant_id,
                            product_id=publication.product_id,
                            publication_id=publication_id,
                            metric=metric,
                            provider=page.provider,
                            provider_version=page.provider_version,
                        )
                    )
            run = CollectionRun(
                context.tenant_id,
                publication_id,
                CollectionStatus.SUCCEEDED,
                checkpoint,
                page.next_cursor,
                id=started.id,
                created_at=started.created_at,
                completed_at=datetime.now(UTC),
            )
            await uow.measurement.add_run(run)
            snapshot = await self._snapshot(uow.measurement, publication)
            snapshot_added = await uow.measurement.add_snapshot(snapshot)
            if snapshot_added:
                await uow.outbox.append(
                    tenant_event(
                        context,
                        event_type="measurement.performance_snapshot.created.v1",
                        schema_version=1,
                        aggregate_type="performance_snapshot",
                        aggregate_id=snapshot.id,
                        payload={
                            "performance_snapshot_id": str(snapshot.id),
                            "publication_id": str(snapshot.publication_id),
                            "product_id": str(snapshot.product_id),
                            "semantic_digest": snapshot.semantic_digest,
                        },
                        payload_schema_digest=self.contracts.schema_digest(
                            "measurement.performance_snapshot.created.v1"
                        ),
                        occurred_at=snapshot.created_at,
                    )
                )
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="measurement.collection.completed",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="publication",
                    resource_id=str(publication_id),
                    after_digest=snapshot.semantic_digest,
                )
            )
            await uow.commit()
            return snapshot

    async def get_publication(
        self, context: ExecutionContext, publication_id: UUID
    ) -> PerformanceSnapshot:
        async with self.uow_factory(context.tenant_id) as uow:
            publication = await uow.measurement.publication(publication_id)
            if publication is None:
                raise MeasurementNotFound("publication not found")
            existing = await uow.measurement.latest_snapshot(publication_id)
            if existing is not None:
                return existing
            return await self._snapshot(uow.measurement, publication)

    async def product(
        self, context: ExecutionContext, product_id: UUID
    ) -> tuple[PerformanceSnapshot, ...]:
        async with self.uow_factory(context.tenant_id) as uow:
            return await uow.measurement.snapshots_for_product(product_id)

    async def history(
        self, context: ExecutionContext, publication_id: UUID
    ) -> tuple[PerformanceObservation, ...]:
        async with self.uow_factory(context.tenant_id) as uow:
            if await uow.measurement.publication(publication_id) is None:
                raise MeasurementNotFound("publication not found")
            return await uow.measurement.observations(publication_id)

    async def issue_reference(
        self, context: ExecutionContext, publication_id: UUID, destination_url: str
    ) -> IssuedAttributionReference:
        self._write(context)
        parsed = urlparse(destination_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
            raise AttributionReferenceInvalid("destination must be an absolute HTTP(S) URL")
        async with self.uow_factory(context.tenant_id) as uow:
            if await uow.measurement.publication(publication_id) is None:
                raise MeasurementNotFound("publication not found")
            code = AttributionReference.issue_code()
            reference = AttributionReference(
                context.tenant_id,
                publication_id,
                AttributionReference.hash_code(code),
                destination_url,
            )
            await uow.measurement.add_reference(reference)
            query = parse_qsl(parsed.query, keep_blank_values=True)
            query.append(("cm_ref", code))
            tracked = urlunparse(parsed._replace(query=urlencode(query)))
            await uow.commit()
            return IssuedAttributionReference(reference, code, tracked)

    async def ingest_fake_conversion(
        self,
        context: ExecutionContext,
        *,
        external_id: str,
        amount: Decimal,
        currency: str,
        observed_at: datetime,
        public_code: str | None,
    ) -> tuple[ConversionObservation, AttributionResult | None]:
        return await self._ingest_conversion(
            context,
            source="fake",
            external_id=external_id,
            amount=amount,
            currency=currency,
            observed_at=observed_at,
            public_code=public_code,
        )

    async def ingest_commerce_conversion(
        self,
        context: ExecutionContext,
        *,
        external_id: str,
        amount: Decimal,
        currency: str,
        observed_at: datetime,
        public_code: str,
    ) -> tuple[ConversionObservation, AttributionResult | None]:
        """Clean commerce-to-measurement boundary; repositories remain isolated."""
        return await self._ingest_conversion(
            context,
            source="commerce.fake",
            external_id=external_id,
            amount=amount,
            currency=currency,
            observed_at=observed_at,
            public_code=public_code,
        )

    async def _ingest_conversion(
        self,
        context: ExecutionContext,
        *,
        source: str,
        external_id: str,
        amount: Decimal,
        currency: str,
        observed_at: datetime,
        public_code: str | None,
    ) -> tuple[ConversionObservation, AttributionResult | None]:
        self._write(context)
        code_hash = AttributionReference.hash_code(public_code) if public_code else None
        value = ConversionObservation(
            context.tenant_id,
            source,
            external_id,
            amount,
            currency,
            observed_at,
            code_hash,
        )
        async with self.uow_factory(context.tenant_id) as uow:
            existing = await uow.measurement.conversion_by_external_id(source, external_id)
            if existing is not None:
                if existing.semantic_digest != value.semantic_digest:
                    raise ConversionConflict("conversion idempotency key reused with new facts")
                pairs = await uow.measurement.attributed_conversions_for_conversion(existing.id)
                return existing, pairs[0] if pairs else None
            reference = (
                await uow.measurement.reference_by_hash(code_hash)
                if code_hash is not None
                else None
            )
            if code_hash is not None and reference is None:
                raise AttributionReferenceInvalid("attribution reference is invalid")
            await uow.measurement.add_conversion(value)
            result = (
                AttributionResult(
                    context.tenant_id,
                    value.id,
                    reference.id,
                    reference.publication_id,
                )
                if reference
                else None
            )
            if result:
                await uow.measurement.add_attribution(result)
                await uow.outbox.append(
                    tenant_event(
                        context,
                        event_type="measurement.attribution.recorded.v1",
                        schema_version=1,
                        aggregate_type="attribution_result",
                        aggregate_id=result.id,
                        payload={
                            "attribution_result_id": str(result.id),
                            "conversion_observation_id": str(result.conversion_observation_id),
                            "publication_id": str(result.publication_id),
                            "method": result.method.value,
                        },
                        payload_schema_digest=self.contracts.schema_digest(
                            "measurement.attribution.recorded.v1"
                        ),
                        occurred_at=result.created_at,
                    )
                )
                publication = await uow.measurement.publication(result.publication_id)
                if publication is None:
                    raise MeasurementNotFound("attributed publication not found")
                await uow.measurement.add_snapshot(
                    await self._snapshot(uow.measurement, publication)
                )
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="measurement.conversion.ingested",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="conversion_observation",
                    resource_id=str(value.id),
                    metadata=safe_metadata(
                        {"attributed": result is not None, "currency": value.currency}
                    ),
                )
            )
            await uow.commit()
            return value, result

    @staticmethod
    async def _snapshot(
        repository: MeasurementRepository, publication: MeasurablePublication
    ) -> PerformanceSnapshot:
        observations = await repository.observations(publication.publication_id)
        attributed = await repository.attributed_conversions(publication.publication_id)
        revenue: dict[str, Decimal] = {}
        for conversion, _result in attributed:
            revenue[conversion.currency] = (
                revenue.get(conversion.currency, Decimal(0)) + conversion.amount
            )
        latest_at = max((item.observed_at for item in observations), default=None)
        freshness = (
            Freshness.NO_DATA
            if latest_at is None
            else Freshness.CURRENT
            if datetime.now(UTC) - latest_at.astimezone(UTC) <= timedelta(days=7)
            else Freshness.STALE
        )
        return PerformanceSnapshot.build(
            tenant_id=publication.tenant_id,
            product_id=publication.product_id,
            publication_id=publication.publication_id,
            observations=observations,
            attributed_conversions=len(attributed),
            attributed_revenue=revenue,
            freshness=freshness,
        )
