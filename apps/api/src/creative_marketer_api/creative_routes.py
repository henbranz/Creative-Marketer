from datetime import datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from creative_marketer.agent_runtime.application import AgentRunService
from creative_marketer.agent_runtime.domain import AgentRuntimeError
from creative_marketer.audit.identity import IdentityAuditService
from creative_marketer.creative.application import CreativeService
from creative_marketer.creative.domain import (
    ChannelIntent,
    CreativeDecisionState,
    CreativeError,
    CreativeRevalidationReason,
    CreativeRevalidationResult,
    CreativeStrategyRequest,
)
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

from .research_routes import AgentRunResponse, _agent_run


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreativeRunStart(Contract):
    idempotency_key: str = Field(min_length=1, max_length=128)
    concept_count: int = Field(default=5, ge=3, le=5)
    channel_intent: ChannelIntent = ChannelIntent.ORGANIC_SHORT_FORM
    approved_experiment_proposal_id: UUID | None = None


class CreativeReplacementStart(Contract):
    transition_id: UUID


class CreativeConceptResponse(Contract):
    id: UUID
    concept_set_id: UUID
    product_id: UUID
    concept_key: str
    ordinal: int
    payload: dict[str, Any]
    semantic_digest: str
    created_at: datetime
    decision_state: CreativeDecisionState | None = None


class CreativeConceptSetResponse(Contract):
    id: UUID
    product_id: UUID
    agent_run_id: UUID
    product_snapshot_id: UUID
    product_snapshot_digest: str
    research_snapshot_id: UUID
    research_snapshot_digest: str
    input_context_digest: str
    semantic_digest: str
    created_at: datetime
    freshness: str
    concepts: list[CreativeConceptResponse]
    experiment_proposal_id: UUID | None = None
    experiment_proposal_digest: str | None = None


class CreativeDecisionRequest(Contract):
    state: CreativeDecisionState
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    note: str | None = Field(default=None, max_length=1000)


class CreativeDecisionResponse(Contract):
    id: UUID
    concept_id: UUID
    state: CreativeDecisionState
    decided_by: UUID
    reason_code: str | None
    note: str | None
    created_at: datetime


class RevalidatedFindingAssertionResponse(Contract):
    finding_key: str
    original_assertion_digest: str
    current_assertion_digest: str | None


class CreativeConceptRevalidationResponse(Contract):
    id: UUID
    product_id: UUID
    concept_id: UUID
    concept_digest: str
    original_concept_set_id: UUID
    original_research_snapshot_id: UUID
    original_research_snapshot_digest: str
    current_research_snapshot_id: UUID
    current_research_snapshot_digest: str
    product_snapshot_id: UUID
    product_snapshot_digest: str
    original_decision_id: UUID
    result: CreativeRevalidationResult
    reason_codes: list[CreativeRevalidationReason]
    referenced_finding_assertions: list[RevalidatedFindingAssertionResponse]
    created_by: UUID
    semantic_digest: str
    created_at: datetime


def create_creative_router(
    authenticator: AuthenticationPort,
    identity_uow: UnitOfWorkFactory,
    agent_service: AgentRunService,
    creative_service: CreativeService,
    environment: str,
    audit: IdentityAuditService,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["creative"])

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

    def concept_response(value: Any, decision: Any = None) -> CreativeConceptResponse:
        return CreativeConceptResponse(
            id=value.id,
            concept_set_id=value.concept_set_id,
            product_id=value.product_id,
            concept_key=value.concept_key,
            ordinal=value.ordinal,
            payload=dict(value.payload),
            semantic_digest=value.semantic_digest,
            created_at=value.created_at,
            decision_state=decision.state if decision else None,
        )

    def revalidation_response(value: Any) -> CreativeConceptRevalidationResponse:
        return CreativeConceptRevalidationResponse(
            id=value.id,
            product_id=value.product_id,
            concept_id=value.concept_id,
            concept_digest=value.concept_digest,
            original_concept_set_id=value.original_concept_set_id,
            original_research_snapshot_id=value.original_research_snapshot_id,
            original_research_snapshot_digest=value.original_research_snapshot_digest,
            current_research_snapshot_id=value.current_research_snapshot_id,
            current_research_snapshot_digest=value.current_research_snapshot_digest,
            product_snapshot_id=value.product_snapshot_id,
            product_snapshot_digest=value.product_snapshot_digest,
            original_decision_id=value.original_decision_id,
            result=value.result,
            reason_codes=list(value.reason_codes),
            referenced_finding_assertions=[
                RevalidatedFindingAssertionResponse(**item.primitive())
                for item in value.referenced_finding_assertions
            ],
            created_by=value.created_by,
            semantic_digest=value.semantic_digest,
            created_at=value.created_at,
        )

    async def set_response(value: Any, ctx: ExecutionContext) -> CreativeConceptSetResponse:
        research_snapshot = await agent_service.get_snapshot(ctx, value.research_snapshot_id)
        research_freshness = await agent_service.snapshot_freshness(ctx, research_snapshot)
        concepts = []
        for concept in value.concepts:
            _, decision = await creative_service.get_concept(ctx, concept.id)
            concepts.append(concept_response(concept, decision))
        return CreativeConceptSetResponse(
            id=value.id,
            product_id=value.product_id,
            agent_run_id=value.agent_run_id,
            product_snapshot_id=value.product_snapshot_id,
            product_snapshot_digest=value.product_snapshot_digest,
            research_snapshot_id=value.research_snapshot_id,
            research_snapshot_digest=value.research_snapshot_digest,
            input_context_digest=value.input_context_digest,
            semantic_digest=value.semantic_digest,
            created_at=value.created_at,
            freshness="CURRENT" if research_freshness == "current" else "OUTDATED",
            concepts=concepts,
            experiment_proposal_id=value.experiment_proposal_id,
            experiment_proposal_digest=value.experiment_proposal_digest,
        )

    @router.post(
        "/products/{product_id}/creative/runs",
        response_model=AgentRunResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def start_run(
        product_id: UUID, value: CreativeRunStart, ctx: Context
    ) -> AgentRunResponse:
        try:
            run = await agent_service.request_creative_strategist(
                ctx,
                product_id=product_id,
                request=CreativeStrategyRequest(value.concept_count, value.channel_intent),
                idempotency_key=value.idempotency_key,
                approved_experiment_proposal_id=value.approved_experiment_proposal_id,
            )
            return _agent_run(run)
        except (AgentRuntimeError, CreativeError, ValueError) as error:
            code = getattr(error, "code", "CREATIVE_INVALID_REQUEST")
            raise HTTPException(
                status_code=403 if "DENIED" in code else 409, detail=code.lower()
            ) from error

    @router.post(
        "/products/{product_id}/creative/runs/{failed_run_id}/replacement",
        response_model=AgentRunResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def replace_output_limited_run(
        product_id: UUID,
        failed_run_id: UUID,
        value: CreativeReplacementStart,
        ctx: Context,
    ) -> AgentRunResponse:
        try:
            run = await agent_service.request_creative_replacement(
                ctx,
                product_id=product_id,
                failed_run_id=failed_run_id,
                transition_id=value.transition_id,
            )
            return _agent_run(run)
        except (AgentRuntimeError, CreativeError, ValueError) as error:
            code = getattr(error, "code", "CREATIVE_INVALID_REQUEST")
            raise HTTPException(
                status_code=403 if "DENIED" in code else 409, detail=code.lower()
            ) from error

    @router.get("/products/{product_id}/creative/runs", response_model=list[AgentRunResponse])
    async def list_runs(product_id: UUID, ctx: Context) -> list[AgentRunResponse]:
        return [
            _agent_run(item)
            for item in await agent_service.list_runs(ctx, product_id)
            if item.agent_type == "creative_strategist"
        ]

    @router.get(
        "/products/{product_id}/creative/concept-sets",
        response_model=list[CreativeConceptSetResponse],
    )
    async def list_sets(product_id: UUID, ctx: Context) -> list[CreativeConceptSetResponse]:
        return [
            await set_response(item, ctx)
            for item in await creative_service.list_sets(ctx, product_id)
        ]

    @router.get("/creative/concept-sets/{set_id}", response_model=CreativeConceptSetResponse)
    async def get_set(set_id: UUID, ctx: Context) -> CreativeConceptSetResponse:
        try:
            return await set_response(await creative_service.get_set(ctx, set_id), ctx)
        except CreativeError as error:
            raise HTTPException(status_code=404, detail=error.code.lower()) from error

    @router.get("/creative/concepts/{concept_id}", response_model=CreativeConceptResponse)
    async def get_concept(concept_id: UUID, ctx: Context) -> CreativeConceptResponse:
        try:
            concept, decision = await creative_service.get_concept(ctx, concept_id)
            return concept_response(concept, decision)
        except CreativeError as error:
            raise HTTPException(status_code=404, detail=error.code.lower()) from error

    @router.post(
        "/creative/concepts/{concept_id}/decision",
        response_model=CreativeDecisionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def decide(
        concept_id: UUID, value: CreativeDecisionRequest, ctx: Context
    ) -> CreativeDecisionResponse:
        try:
            result = await creative_service.decide(
                ctx, concept_id, value.state, reason_code=value.reason_code, note=value.note
            )
            return CreativeDecisionResponse(
                id=result.id,
                concept_id=result.concept_id,
                state=result.state,
                decided_by=result.decided_by,
                reason_code=result.reason_code,
                note=result.note,
                created_at=result.created_at,
            )
        except CreativeError as error:
            raise HTTPException(
                status_code=403 if "PERMISSION" in error.code else 404, detail=error.code.lower()
            ) from error

    @router.post(
        "/creative/concepts/{concept_id}/revalidate",
        response_model=CreativeConceptRevalidationResponse,
    )
    async def revalidate(concept_id: UUID, ctx: Context) -> CreativeConceptRevalidationResponse:
        try:
            return revalidation_response(await creative_service.revalidate(ctx, concept_id))
        except CreativeError as error:
            if "PERMISSION" in error.code:
                response_status = 403
            elif error.code == "CREATIVE_NOT_FOUND":
                response_status = 404
            else:
                response_status = 409
            raise HTTPException(status_code=response_status, detail=error.code.lower()) from error

    @router.get(
        "/creative/concepts/{concept_id}/revalidations",
        response_model=list[CreativeConceptRevalidationResponse],
    )
    async def list_revalidations(
        concept_id: UUID, ctx: Context
    ) -> list[CreativeConceptRevalidationResponse]:
        try:
            return [
                revalidation_response(item)
                for item in await creative_service.list_revalidations(ctx, concept_id)
            ]
        except CreativeError as error:
            raise HTTPException(status_code=404, detail=error.code.lower()) from error

    return router
