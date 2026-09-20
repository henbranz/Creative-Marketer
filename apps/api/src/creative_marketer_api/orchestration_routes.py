from datetime import datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict

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
from creative_marketer.orchestration.application import CreativeCycleService
from creative_marketer.orchestration.domain import CreativeCycle, OrchestrationError


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartCycleRequest(Contract):
    parent_cycle_id: UUID | None = None
    source_experiment_proposal_id: UUID | None = None


class RequirementResponse(Contract):
    key: str
    state: str
    message: str
    required: bool
    resource_ref: str | None


class ReadinessResponse(Contract):
    state: str
    requirements: list[RequirementResponse]
    allowed_actions: list[str]


class TransitionResponse(Contract):
    from_stage: str | None
    to_stage: str
    status: str
    reason_code: str
    occurred_at: datetime


class StepResponse(Contract):
    step_key: str
    attempt: int
    status: str
    agent_run_id: UUID | None
    workflow_ref: str | None
    artifact_ref: str | None
    failure_code: str | None
    created_at: datetime


class SupervisorReportResponse(Contract):
    id: UUID
    summary: str
    current_stage_explanation: str
    blockers: list[str]
    attention_items: list[str]
    suggested_next_actions: list[str]
    completion_summary: str | None
    semantic_digest: str
    created_at: datetime


class SupervisorRunResponse(Contract):
    id: UUID
    status: str
    agent_type: str


class CycleResponse(Contract):
    id: UUID
    product_id: UUID
    status: str
    current_stage: str
    mode: str
    product_snapshot_id: UUID
    product_snapshot_digest: str
    product_changed_after_start: bool
    artifact_bindings: dict[str, str | None]
    parent_cycle_id: UUID | None
    source_experiment_proposal_id: UUID | None
    blocker_code: str | None
    failure_code: str | None
    state_machine_version: str
    cycle_version: int
    provider_mode: str
    created_at: datetime
    updated_at: datetime
    readiness: ReadinessResponse | None = None
    steps: list[StepResponse] = []
    timeline: list[TransitionResponse] = []
    supervisor_report: SupervisorReportResponse | None = None


def _readiness(value: Any) -> ReadinessResponse:
    return ReadinessResponse(
        state=value.state.value,
        requirements=[
            RequirementResponse(
                key=item.key,
                state=item.state.value,
                message=item.message,
                required=item.required,
                resource_ref=item.resource_ref,
            )
            for item in value.requirements
        ],
        allowed_actions=[item.value for item in value.allowed_actions],
    )


def _cycle(
    value: CreativeCycle,
    *,
    state: Any = None,
    readiness: Any = None,
    steps: tuple[Any, ...] = (),
    transitions: tuple[Any, ...] = (),
    report: Any = None,
) -> CycleResponse:
    return CycleResponse(
        id=value.id,
        product_id=value.product_id,
        status=value.status.value,
        current_stage=value.current_stage.value,
        mode=value.mode.value,
        product_snapshot_id=value.product_snapshot_id,
        product_snapshot_digest=value.product_snapshot_digest,
        product_changed_after_start=bool(
            state
            and state.current_product_snapshot_id
            and state.current_product_snapshot_id != value.product_snapshot_id
        ),
        artifact_bindings=value.artifacts.as_dict(),
        parent_cycle_id=value.parent_cycle_id,
        source_experiment_proposal_id=value.source_experiment_proposal_id,
        blocker_code=value.blocker_code,
        failure_code=value.failure_code,
        state_machine_version=value.state_machine_version,
        cycle_version=value.cycle_version,
        provider_mode="DEMO_FAKE",
        created_at=value.created_at,
        updated_at=value.updated_at,
        readiness=_readiness(readiness) if readiness else None,
        steps=[
            StepResponse(
                step_key=item.step_key,
                attempt=item.attempt,
                status=item.status.value,
                agent_run_id=item.agent_run_id,
                workflow_ref=item.workflow_ref,
                artifact_ref=item.artifact_ref,
                failure_code=item.failure_code,
                created_at=item.created_at,
            )
            for item in steps
        ],
        timeline=[
            TransitionResponse(
                from_stage=item.from_stage.value if item.from_stage else None,
                to_stage=item.to_stage.value,
                status=item.status.value,
                reason_code=item.reason_code,
                occurred_at=item.occurred_at,
            )
            for item in transitions
        ],
        supervisor_report=SupervisorReportResponse(
            id=report.id,
            summary=report.summary,
            current_stage_explanation=report.current_stage_explanation,
            blockers=list(report.blockers),
            attention_items=list(report.attention_items),
            suggested_next_actions=[item.value for item in report.suggested_next_actions],
            completion_summary=report.completion_summary,
            semantic_digest=report.semantic_digest,
            created_at=report.created_at,
        )
        if report
        else None,
    )


def create_orchestration_router(
    authenticator: AuthenticationPort,
    identity_uow: UnitOfWorkFactory,
    service: CreativeCycleService,
    environment: str,
    audit: IdentityAuditService,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["orchestration"])

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

    def problem(error: Exception) -> HTTPException:
        code = getattr(error, "code", "ORCHESTRATION_ERROR")
        return HTTPException(
            status_code=404 if code == "CYCLE_NOT_FOUND" else 403 if "PERMISSION" in code else 409,
            detail=code.lower(),
        )

    @router.get(
        "/products/{product_id}/creative-cycles/readiness", response_model=ReadinessResponse
    )
    async def readiness(product_id: UUID, ctx: Context) -> ReadinessResponse:
        return _readiness(await service.preflight(ctx, product_id))

    @router.post(
        "/products/{product_id}/creative-cycles",
        response_model=CycleResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def start(product_id: UUID, request: StartCycleRequest, ctx: Context) -> CycleResponse:
        try:
            value = await service.start(
                ctx,
                product_id,
                parent_cycle_id=request.parent_cycle_id,
                source_experiment_proposal_id=request.source_experiment_proposal_id,
            )
            return _cycle(value)
        except (OrchestrationError, AgentRuntimeError, ValueError) as error:
            raise problem(error) from error

    @router.get(
        "/products/{product_id}/creative-cycles/active",
        response_model=CycleResponse,
        responses={204: {"description": "No active cycle"}},
    )
    async def active(product_id: UUID, ctx: Context) -> CycleResponse | Response:
        value = await service.active(ctx, product_id)
        if value is None:
            return Response(status_code=204)
        state, ready, steps, transitions, report = await service.get(ctx, value.id)
        return _cycle(
            value, state=state, readiness=ready, steps=steps, transitions=transitions, report=report
        )

    @router.get("/creative-cycles/{cycle_id}", response_model=CycleResponse)
    async def get(cycle_id: UUID, ctx: Context) -> CycleResponse:
        try:
            state, ready, steps, transitions, report = await service.get(ctx, cycle_id)
            return _cycle(
                state.cycle,
                state=state,
                readiness=ready,
                steps=steps,
                transitions=transitions,
                report=report,
            )
        except OrchestrationError as error:
            raise problem(error) from error

    @router.post("/creative-cycles/{cycle_id}/reconcile", response_model=CycleResponse)
    async def reconcile(cycle_id: UUID, ctx: Context) -> CycleResponse:
        try:
            value = await service.reconcile(ctx, cycle_id)
            state, ready, steps, transitions, report = await service.get(ctx, value.id)
            return _cycle(
                value,
                state=state,
                readiness=ready,
                steps=steps,
                transitions=transitions,
                report=report,
            )
        except (OrchestrationError, AgentRuntimeError, ValueError) as error:
            raise problem(error) from error

    @router.post("/creative-cycles/{cycle_id}/cancel", response_model=CycleResponse)
    async def cancel(cycle_id: UUID, ctx: Context) -> CycleResponse:
        try:
            return _cycle(await service.cancel(ctx, cycle_id))
        except OrchestrationError as error:
            raise problem(error) from error

    @router.post(
        "/creative-cycles/{cycle_id}/supervisor-report",
        response_model=SupervisorRunResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def supervisor_report(cycle_id: UUID, ctx: Context) -> SupervisorRunResponse:
        try:
            value = await service.create_supervisor_report(ctx, cycle_id)
            return SupervisorRunResponse(
                id=value.id,
                status=value.status.value,
                agent_type=value.agent_type,
            )
        except (OrchestrationError, AgentRuntimeError, ValueError) as error:
            raise problem(error) from error

    return router
