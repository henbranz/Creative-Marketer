from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from creative_marketer.agent_runtime.application import AgentRunService
from creative_marketer.agent_runtime.domain import AgentRuntimeError
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
from creative_marketer.production.domain import (
    GenerationJob,
    MediaKind,
    ProductionError,
    ProductionPlanDecisionState,
    ProductionPlanningRequest,
    SourceStrategy,
)
from creative_marketer.production.service import ProductionPlanRecord, ProductionService

from .research_routes import AgentRunResponse, _agent_run


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProducerRunStart(Contract):
    idempotency_key: str = Field(min_length=1, max_length=128)
    target_format: str = "SHORT_FORM_VERTICAL_VIDEO"
    aspect_ratio: str = "9:16"


class ProductionShotResponse(Contract):
    shot_key: str
    source_strategy: SourceStrategy


class ProductionSceneResponse(Contract):
    scene_key: str
    ordinal: int
    shots: list[ProductionShotResponse]


class ProductionPlanResponse(Contract):
    id: UUID
    concept_id: UUID
    strategy: str
    status: str
    scenes: list[ProductionSceneResponse]
    generated_image_count: int
    video_segment_count: int
    existing_asset_count: int
    manual_shot_count: int
    planning_cost: str
    estimated_max_image_cost: str
    estimated_max_video_cost: str
    estimated_total_cost: str
    currency: str
    created_at: datetime


class ProductionJobResponse(Contract):
    id: UUID
    kind: MediaKind
    status: str
    actual_cost: str
    unknown_cost: str
    currency: str
    output_asset_id: UUID | None


def _plan(value: ProductionPlanRecord) -> ProductionPlanResponse:
    plan = value.plan
    shots = [shot for scene in plan.scenes for shot in scene.shots]
    return ProductionPlanResponse(
        id=plan.id,
        concept_id=plan.context.concept_id,
        strategy=plan.strategy,
        status=value.decision.state.value if value.decision else "UNREVIEWED",
        scenes=[
            ProductionSceneResponse(
                scene_key=scene.scene_key,
                ordinal=scene.ordinal,
                shots=[
                    ProductionShotResponse(
                        shot_key=shot.shot_key, source_strategy=shot.source_strategy
                    )
                    for shot in scene.shots
                ],
            )
            for scene in plan.scenes
        ],
        generated_image_count=sum(
            item.media_kind is MediaKind.IMAGE for item in plan.generation_segments
        ),
        video_segment_count=sum(
            item.media_kind is MediaKind.VIDEO for item in plan.generation_segments
        ),
        existing_asset_count=sum(
            item.source_strategy is SourceStrategy.USE_EXISTING_ASSET for item in shots
        ),
        manual_shot_count=sum(
            item.source_strategy is SourceStrategy.MANUAL_CAPTURE for item in shots
        ),
        planning_cost=str(value.planning_cost),
        estimated_max_image_cost=str(plan.cost.estimated_max_image_cost),
        estimated_max_video_cost=str(plan.cost.estimated_max_video_cost),
        estimated_total_cost=str(plan.cost.estimated_total_cost),
        currency=plan.cost.currency,
        created_at=plan.created_at,
    )


def _job(value: GenerationJob) -> ProductionJobResponse:
    return ProductionJobResponse(
        id=value.id,
        kind=value.kind,
        status=value.status.value,
        actual_cost=str(value.actual_cost),
        unknown_cost=str(value.unknown_cost),
        currency=value.currency,
        output_asset_id=value.output_asset_id,
    )


def create_production_router(
    authenticator: AuthenticationPort,
    identity_uow: UnitOfWorkFactory,
    agent_service: AgentRunService,
    production_service: ProductionService,
    environment: str,
    audit: IdentityAuditService,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["production"])

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

    def failure(error: Exception) -> HTTPException:
        code = getattr(error, "code", "PRODUCTION_INVALID_REQUEST")
        return HTTPException(
            status_code=404 if "NOT_FOUND" in code else 403 if "DENIED" in code else 409,
            detail=code.lower(),
        )

    @router.post(
        "/creative/concepts/{concept_id}/production/runs",
        response_model=AgentRunResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def start_producer(
        concept_id: UUID, value: ProducerRunStart, ctx: Context
    ) -> AgentRunResponse:
        try:
            return _agent_run(
                await agent_service.request_producer(
                    ctx,
                    concept_id=concept_id,
                    request=ProductionPlanningRequest(value.target_format, value.aspect_ratio),
                    idempotency_key=value.idempotency_key,
                )
            )
        except (AgentRuntimeError, ProductionError, ValueError) as error:
            raise failure(error) from error

    @router.get(
        "/products/{product_id}/production/plans",
        response_model=list[ProductionPlanResponse],
    )
    async def list_plans(product_id: UUID, ctx: Context) -> list[ProductionPlanResponse]:
        return [_plan(item) for item in await production_service.list_plans(ctx, product_id)]

    @router.get("/production/plans/{plan_id}", response_model=ProductionPlanResponse)
    async def get_plan(plan_id: UUID, ctx: Context) -> ProductionPlanResponse:
        try:
            return _plan(await production_service.get_plan(ctx, plan_id))
        except ProductionError as error:
            raise failure(error) from error

    async def decide(
        plan_id: UUID, state: ProductionPlanDecisionState, ctx: ExecutionContext
    ) -> ProductionPlanResponse:
        try:
            return _plan(await production_service.decide(ctx, plan_id, state))
        except ProductionError as error:
            raise failure(error) from error

    @router.post(
        "/production/plans/{plan_id}/approve-generation",
        response_model=ProductionPlanResponse,
    )
    async def approve(plan_id: UUID, ctx: Context) -> ProductionPlanResponse:
        return await decide(plan_id, ProductionPlanDecisionState.APPROVED_FOR_GENERATION, ctx)

    @router.post("/production/plans/{plan_id}/reject", response_model=ProductionPlanResponse)
    async def reject(plan_id: UUID, ctx: Context) -> ProductionPlanResponse:
        return await decide(plan_id, ProductionPlanDecisionState.REJECTED, ctx)

    @router.get("/production/plans/{plan_id}/jobs", response_model=list[ProductionJobResponse])
    async def list_jobs(plan_id: UUID, ctx: Context) -> list[ProductionJobResponse]:
        try:
            return [_job(item) for item in await production_service.list_jobs(ctx, plan_id)]
        except ProductionError as error:
            raise failure(error) from error

    @router.get("/production/jobs/{job_id}", response_model=ProductionJobResponse)
    async def get_job(job_id: UUID, ctx: Context) -> ProductionJobResponse:
        try:
            return _job(await production_service.get_job(ctx, job_id))
        except ProductionError as error:
            raise failure(error) from error

    return router
