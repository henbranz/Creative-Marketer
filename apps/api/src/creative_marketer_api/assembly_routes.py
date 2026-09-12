from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict

from creative_marketer.assembly.application import (
    AssemblyPlanRecord,
    AssemblyReadinessResult,
    AssemblyService,
    FinalCreativeRecord,
)
from creative_marketer.assembly.domain import (
    AssemblyError,
    AssemblyJob,
    FinalCreativeDecisionState,
)
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


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManualSourceWrite(Contract):
    asset_id: UUID


class ReadinessItemResponse(Contract):
    shot_key: str
    status: str


class AssemblyReadinessResponse(Contract):
    status: str
    ready: bool
    sources: list[ReadinessItemResponse]


class AssemblyItemResponse(Contract):
    item_key: str
    ordinal: int
    source_kind: str
    source_asset_id: UUID
    source_media_kind: str
    production_shot_ids: list[UUID]
    production_shot_keys: list[str]
    generation_segment_id: UUID | None
    timeline_start_ms: int
    timeline_duration_ms: int
    fit_mode: str
    audio_behavior: str
    transition_in: str
    transition_out: str


class CaptionResponse(Contract):
    text: str
    start_ms: int
    end_ms: int
    kind: str


class OverlayResponse(Contract):
    text: str
    start_ms: int
    end_ms: int
    position: str
    style_token: str


class AssemblyJobResponse(Contract):
    id: UUID
    assembly_plan_id: UUID
    status: str
    failure_code: str | None
    output_asset_id: UUID | None
    final_creative_id: UUID | None
    renderer: str | None
    renderer_version: str | None
    updated_at: datetime


class AssemblyPlanResponse(Contract):
    id: UUID
    production_plan_id: UUID
    semantic_digest: str
    render_profile_key: str
    render_profile_version: int
    timeline_duration_ms: int
    items: list[AssemblyItemResponse]
    captions: list[CaptionResponse]
    overlays: list[OverlayResponse]
    job: AssemblyJobResponse
    created_at: datetime


class FinalCreativeResponse(Contract):
    id: UUID
    product_id: UUID
    assembly_plan_id: UUID
    production_plan_id: UUID
    creative_concept_id: UUID
    output_asset_id: UUID
    semantic_digest: str
    duration_ms: int
    width: int
    height: int
    fps: int
    has_audio: bool
    source_count: int
    render_profile_key: str
    render_profile_version: int
    renderer: str
    renderer_version: str
    decision_state: str | None
    created_at: datetime


def _readiness(value: AssemblyReadinessResult) -> AssemblyReadinessResponse:
    return AssemblyReadinessResponse(
        status=value.status.value,
        ready=value.ready,
        sources=[
            ReadinessItemResponse(shot_key=key, status=state.value) for key, state in value.details
        ],
    )


def _job(value: AssemblyJob) -> AssemblyJobResponse:
    return AssemblyJobResponse(
        id=value.id,
        assembly_plan_id=value.assembly_plan_id,
        status=value.status.value,
        failure_code=value.failure_code,
        output_asset_id=value.output_asset_id,
        final_creative_id=value.final_creative_id,
        renderer=value.renderer,
        renderer_version=value.renderer_version,
        updated_at=value.updated_at,
    )


def _plan(value: AssemblyPlanRecord) -> AssemblyPlanResponse:
    plan = value.plan
    return AssemblyPlanResponse(
        id=plan.id,
        production_plan_id=plan.production_plan_id,
        semantic_digest=plan.semantic_digest,
        render_profile_key=plan.render_profile.key,
        render_profile_version=plan.render_profile.version,
        timeline_duration_ms=plan.timeline_duration_ms,
        items=[
            AssemblyItemResponse(
                item_key=item.item_key,
                ordinal=item.ordinal,
                source_kind=item.source_kind.value,
                source_asset_id=item.source_asset_id,
                source_media_kind=item.source_media_kind,
                production_shot_ids=list(item.production_shot_ids),
                production_shot_keys=list(item.production_shot_keys),
                generation_segment_id=item.generation_segment_id,
                timeline_start_ms=item.timeline_start_ms,
                timeline_duration_ms=item.timeline_duration_ms,
                fit_mode=item.fit_mode.value,
                audio_behavior=item.audio_behavior.value,
                transition_in=item.transition_in.value,
                transition_out=item.transition_out.value,
            )
            for item in plan.items
        ],
        captions=[
            CaptionResponse(
                text=item.text, start_ms=item.start_ms, end_ms=item.end_ms, kind=item.kind.value
            )
            for item in plan.captions
        ],
        overlays=[
            OverlayResponse(
                text=item.text,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                position=item.position,
                style_token=item.style_token.value,
            )
            for item in plan.overlays
        ],
        job=_job(value.job),
        created_at=plan.created_at,
    )


def _final(value: FinalCreativeRecord) -> FinalCreativeResponse:
    item = value.final_creative
    return FinalCreativeResponse(
        id=item.id,
        product_id=item.product_id,
        assembly_plan_id=item.assembly_plan_id,
        production_plan_id=item.production_plan_id,
        creative_concept_id=item.creative_concept_id,
        output_asset_id=item.output_asset_id,
        semantic_digest=item.semantic_digest,
        duration_ms=item.duration_ms,
        width=item.width,
        height=item.height,
        fps=item.fps,
        has_audio=item.has_audio,
        source_count=item.source_count,
        render_profile_key=item.render_profile_key,
        render_profile_version=item.render_profile_version,
        renderer=item.renderer,
        renderer_version=item.renderer_version,
        decision_state=value.decision.state.value if value.decision else None,
        created_at=item.created_at,
    )


def create_assembly_router(
    authenticator: AuthenticationPort,
    identity_uow: UnitOfWorkFactory,
    service: AssemblyService,
    environment: str,
    audit: IdentityAuditService,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["assembly"])

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

    def failure(error: AssemblyError) -> HTTPException:
        return HTTPException(
            status_code=404
            if "NOT_FOUND" in error.code
            else 403
            if "DENIED" in error.code
            else 409,
            detail=error.code.lower(),
        )

    @router.get(
        "/production/plans/{plan_id}/assembly-readiness", response_model=AssemblyReadinessResponse
    )
    async def readiness(plan_id: UUID, ctx: Context) -> AssemblyReadinessResponse:
        try:
            return _readiness(await service.readiness(ctx, plan_id))
        except AssemblyError as error:
            raise failure(error) from error

    @router.put(
        "/production/shots/{shot_id}/manual-source", response_model=AssemblyReadinessResponse
    )
    async def manual_source(
        shot_id: UUID, value: ManualSourceWrite, ctx: Context
    ) -> AssemblyReadinessResponse:
        try:
            return _readiness(await service.bind_manual_source(ctx, shot_id, value.asset_id))
        except AssemblyError as error:
            raise failure(error) from error

    @router.post(
        "/production/plans/{plan_id}/assembly-plans",
        response_model=AssemblyPlanResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_plan(plan_id: UUID, ctx: Context) -> AssemblyPlanResponse:
        try:
            return _plan(await service.create_plan(ctx, plan_id))
        except AssemblyError as error:
            raise failure(error) from error

    @router.get(
        "/production/plans/{plan_id}/assembly-plans", response_model=list[AssemblyPlanResponse]
    )
    async def list_plans(plan_id: UUID, ctx: Context) -> list[AssemblyPlanResponse]:
        return [_plan(item) for item in await service.list_plans(ctx, plan_id)]

    @router.get("/assembly/plans/{plan_id}", response_model=AssemblyPlanResponse)
    async def get_plan(plan_id: UUID, ctx: Context) -> AssemblyPlanResponse:
        try:
            return _plan(await service.get_plan(ctx, plan_id))
        except AssemblyError as error:
            raise failure(error) from error

    @router.get("/assembly/jobs/{job_id}", response_model=AssemblyJobResponse)
    async def get_job(job_id: UUID, ctx: Context) -> AssemblyJobResponse:
        try:
            return _job(await service.get_job(ctx, job_id))
        except AssemblyError as error:
            raise failure(error) from error

    @router.get("/final-creatives/{final_id}", response_model=FinalCreativeResponse)
    async def get_final(final_id: UUID, ctx: Context) -> FinalCreativeResponse:
        try:
            return _final(await service.get_final(ctx, final_id))
        except AssemblyError as error:
            raise failure(error) from error

    async def decide(
        final_id: UUID, state: FinalCreativeDecisionState, ctx: ExecutionContext
    ) -> FinalCreativeResponse:
        try:
            return _final(await service.decide_final(ctx, final_id, state))
        except AssemblyError as error:
            raise failure(error) from error

    @router.post(
        "/final-creatives/{final_id}/approve-publishing", response_model=FinalCreativeResponse
    )
    async def approve(final_id: UUID, ctx: Context) -> FinalCreativeResponse:
        return await decide(final_id, FinalCreativeDecisionState.APPROVED_FOR_PUBLISHING, ctx)

    @router.post("/final-creatives/{final_id}/reject", response_model=FinalCreativeResponse)
    async def reject(final_id: UUID, ctx: Context) -> FinalCreativeResponse:
        return await decide(final_id, FinalCreativeDecisionState.REJECTED, ctx)

    return router
