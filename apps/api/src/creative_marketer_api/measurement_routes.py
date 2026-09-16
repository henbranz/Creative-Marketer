from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from creative_marketer.audit.identity import IdentityAuditService
from creative_marketer.identity.application.authentication import (
    AuthenticatedPrincipal,
    AuthenticationPort,
    ExecutionContext,
    TenantSelector,
)
from creative_marketer.identity.application.errors import (
    AuthenticationUnavailable,
    MembershipInactive,
    TenantAccessDenied,
    TenantSuspended,
    Unauthenticated,
    UnknownExternalIdentity,
    UserDisabled,
)
from creative_marketer.identity.application.identity_resolution import ResolveTenantExecutionContext
from creative_marketer.identity.application.ports import UnitOfWorkFactory
from creative_marketer.measurement.application import MeasurementService
from creative_marketer.measurement.domain import (
    MeasurementError,
    PerformanceObservation,
    PerformanceSnapshot,
)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DerivedMetricResponse(Contract):
    key: str
    value: Decimal | None
    formula_version: str
    unavailable_reason: str | None


class SnapshotResponse(Contract):
    id: UUID
    product_id: UUID
    publication_id: UUID
    observation_ids: list[UUID]
    latest_metrics: dict[str, Decimal]
    derived_metrics: list[DerivedMetricResponse]
    attributed_conversions: int
    attributed_revenue: dict[str, Decimal]
    freshness: str
    semantic_digest: str
    created_at: datetime


class ObservationResponse(Contract):
    id: UUID
    publication_id: UUID
    metric_key: str
    semantics: str
    value: Decimal
    unit: str
    observed_at: datetime
    provider: str
    provider_version: str


class AttributionReferenceWrite(Contract):
    destination_url: HttpUrl


class AttributionReferenceResponse(Contract):
    id: UUID
    publication_id: UUID
    public_code: str
    tracked_destination_url: str
    created_at: datetime


class FakeConversionWrite(Contract):
    external_id: str = Field(min_length=1, max_length=256)
    amount: Decimal = Field(ge=0, max_digits=30, decimal_places=9)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    observed_at: datetime
    attribution_code: str | None = Field(default=None, min_length=40, max_length=128)


class FakeConversionResponse(Contract):
    conversion_observation_id: UUID
    attribution_result_id: UUID | None
    publication_id: UUID | None
    method: str | None


def _snapshot(value: PerformanceSnapshot) -> SnapshotResponse:
    return SnapshotResponse(
        id=value.id,
        product_id=value.product_id,
        publication_id=value.publication_id,
        observation_ids=list(value.observation_ids),
        latest_metrics=dict(value.latest_metrics),
        derived_metrics=[
            DerivedMetricResponse(
                key=item.key,
                value=item.value,
                formula_version=item.formula_version,
                unavailable_reason=item.unavailable_reason,
            )
            for item in value.derived_metrics
        ],
        attributed_conversions=value.attributed_conversions,
        attributed_revenue=dict(value.attributed_revenue),
        freshness=value.freshness.value,
        semantic_digest=value.semantic_digest,
        created_at=value.created_at,
    )


def _observation(value: PerformanceObservation) -> ObservationResponse:
    return ObservationResponse(
        id=value.id,
        publication_id=value.publication_id,
        metric_key=value.metric_key,
        semantics=value.semantics.value,
        value=value.value,
        unit=value.unit,
        observed_at=value.observed_at,
        provider=value.provider,
        provider_version=value.provider_version,
    )


def create_measurement_router(
    authenticator: AuthenticationPort,
    identity_uow: UnitOfWorkFactory,
    service: MeasurementService,
    environment: str,
    audit: IdentityAuditService,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["measurement"])

    async def execution_context(
        authorization: Annotated[str | None, Header()] = None,
        tenant_id: Annotated[UUID | None, Header(alias="X-Tenant-ID")] = None,
        correlation_id: Annotated[UUID | None, Header(alias="X-Correlation-ID")] = None,
    ) -> ExecutionContext:
        if authorization is None or not authorization.startswith("Bearer ") or tenant_id is None:
            raise HTTPException(
                status_code=401, detail="authentication and tenant selection required"
            )
        try:
            principal: AuthenticatedPrincipal = await authenticator.authenticate(
                authorization.removeprefix("Bearer ")
            )
            return await ResolveTenantExecutionContext(identity_uow, audit)(
                principal, TenantSelector(tenant_id), environment, correlation_id or uuid4()
            )
        except AuthenticationUnavailable as error:
            raise HTTPException(status_code=503, detail=error.code) from error
        except (Unauthenticated, UnknownExternalIdentity, UserDisabled) as error:
            raise HTTPException(status_code=401, detail="identity_not_recognized") from error
        except (TenantAccessDenied, MembershipInactive, TenantSuspended) as error:
            raise HTTPException(status_code=403, detail="tenant_access_denied") from error

    Context = Annotated[ExecutionContext, Depends(execution_context)]

    def failure(error: MeasurementError) -> HTTPException:
        code = error.code
        return HTTPException(
            status_code=404 if "NOT_FOUND" in code else 403 if "DENIED" in code else 409,
            detail=code.lower(),
        )

    @router.get("/products/{product_id}/performance", response_model=list[SnapshotResponse])
    async def product_performance(product_id: UUID, ctx: Context) -> list[SnapshotResponse]:
        return [_snapshot(value) for value in await service.product(ctx, product_id)]

    @router.get("/publications/{publication_id}/performance", response_model=SnapshotResponse)
    async def publication_performance(publication_id: UUID, ctx: Context) -> SnapshotResponse:
        try:
            return _snapshot(await service.get_publication(ctx, publication_id))
        except MeasurementError as error:
            raise failure(error) from error

    @router.get(
        "/publications/{publication_id}/performance/history",
        response_model=list[ObservationResponse],
    )
    async def history(publication_id: UUID, ctx: Context) -> list[ObservationResponse]:
        try:
            return [_observation(value) for value in await service.history(ctx, publication_id)]
        except MeasurementError as error:
            raise failure(error) from error

    @router.post(
        "/publications/{publication_id}/performance/collect", response_model=SnapshotResponse
    )
    async def collect(publication_id: UUID, ctx: Context) -> SnapshotResponse:
        try:
            return _snapshot(await service.collect(ctx, publication_id))
        except MeasurementError as error:
            raise failure(error) from error

    @router.post(
        "/publications/{publication_id}/attribution-references",
        response_model=AttributionReferenceResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def issue_reference(
        publication_id: UUID, value: AttributionReferenceWrite, ctx: Context
    ) -> AttributionReferenceResponse:
        try:
            issued = await service.issue_reference(ctx, publication_id, str(value.destination_url))
            return AttributionReferenceResponse(
                id=issued.reference.id,
                publication_id=issued.reference.publication_id,
                public_code=issued.public_code,
                tracked_destination_url=issued.tracked_destination_url,
                created_at=issued.reference.created_at,
            )
        except MeasurementError as error:
            raise failure(error) from error

    @router.post("/measurement/dev/fake-conversions", response_model=FakeConversionResponse)
    async def fake_conversion(value: FakeConversionWrite, ctx: Context) -> FakeConversionResponse:
        if environment not in {"development", "test"}:
            raise HTTPException(status_code=404, detail="fake_conversion_ingestion_unavailable")
        try:
            conversion, result = await service.ingest_fake_conversion(
                ctx,
                external_id=value.external_id,
                amount=value.amount,
                currency=value.currency,
                observed_at=value.observed_at,
                public_code=value.attribution_code,
            )
            return FakeConversionResponse(
                conversion_observation_id=conversion.id,
                attribution_result_id=result.id if result else None,
                publication_id=result.publication_id if result else None,
                method=result.method.value if result else None,
            )
        except MeasurementError as error:
            raise failure(error) from error

    return router
