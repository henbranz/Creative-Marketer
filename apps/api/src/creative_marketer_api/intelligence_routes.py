from datetime import datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from creative_marketer.agent_runtime.application import AgentRunService
from creative_marketer.agent_runtime.domain import AgentRuntimeError
from creative_marketer.audit.identity import IdentityAuditService
from creative_marketer.creative.domain import ChannelIntent, CreativeStrategyRequest
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
from creative_marketer.intelligence.application import IntelligenceService
from creative_marketer.intelligence.domain import (
    ExperimentDecisionKind,
    InsightDecisionKind,
    IntelligenceError,
)

from .research_routes import AgentRunResponse, _agent_run


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalyzeRequest(Contract):
    idempotency_key: str = Field(min_length=1, max_length=128)


class DecisionRequest(Contract):
    decision: str


class GenerateConceptsRequest(Contract):
    idempotency_key: str = Field(min_length=1, max_length=128)
    concept_count: int = Field(default=5, ge=3, le=5)
    channel_intent: ChannelIntent = ChannelIntent.ORGANIC_SHORT_FORM


class CandidateResponse(Contract):
    id: UUID
    statement: str
    sample_size: int
    metric: str
    baseline: str | None
    observed_delta: str | None
    confidence: str
    scope: dict[str, Any]
    limitations: list[str]
    data_trust_level: str
    status: str
    semantic_digest: str
    created_at: datetime


class ProposalResponse(Contract):
    id: UUID
    hypothesis: str
    primary_variable: str
    controlled_elements: list[str]
    target_metric: str
    platform: str
    recommended_measurement_window: str
    creative_direction: str
    rationale: str
    expected_learning: str
    data_trust_level: str
    decision: str | None = None
    semantic_digest: str
    created_at: datetime


class ReportResponse(Contract):
    id: UUID
    product_id: UUID
    agent_run_id: UUID
    context_manifest_id: UUID
    context_manifest_digest: str
    data_trust_level: str
    summary: str
    observations: list[dict[str, Any]]
    comparative_findings: list[dict[str, Any]]
    limitations: list[str]
    semantic_digest: str
    created_at: datetime
    candidates: list[CandidateResponse] = Field(default_factory=list)
    proposals: list[ProposalResponse] = Field(default_factory=list)


def _report(
    value: Any,
    candidates: tuple[Any, ...] = (),
    proposals: tuple[Any, ...] = (),
    decisions: dict[UUID, str] | None = None,
) -> ReportResponse:
    decisions = decisions or {}
    return ReportResponse(
        id=value.id,
        product_id=value.product_id,
        agent_run_id=value.agent_run_id,
        context_manifest_id=value.context_manifest_id,
        context_manifest_digest=value.context_manifest_digest,
        data_trust_level=value.data_trust_level.value,
        summary=value.summary,
        observations=[dict(item) for item in value.observations],
        comparative_findings=[dict(item) for item in value.comparative_findings],
        limitations=list(value.limitations),
        semantic_digest=value.semantic_digest,
        created_at=value.created_at,
        candidates=[
            CandidateResponse(
                id=item.id,
                statement=item.statement,
                sample_size=item.sample_size,
                metric=item.metric,
                baseline=str(item.baseline) if item.baseline is not None else None,
                observed_delta=str(item.observed_delta)
                if item.observed_delta is not None
                else None,
                confidence=item.confidence.value,
                scope=dict(item.scope),
                limitations=list(item.limitations),
                data_trust_level=item.data_trust_level.value,
                status=item.status.value,
                semantic_digest=item.semantic_digest,
                created_at=item.created_at,
            )
            for item in candidates
        ],
        proposals=[
            ProposalResponse(
                id=item.id,
                hypothesis=item.hypothesis,
                primary_variable=item.primary_variable,
                controlled_elements=list(item.controlled_elements),
                target_metric=item.target_metric,
                platform=item.platform,
                recommended_measurement_window=item.recommended_measurement_window,
                creative_direction=item.creative_direction,
                rationale=item.rationale,
                expected_learning=item.expected_learning,
                data_trust_level=item.data_trust_level.value,
                decision=decisions.get(item.id),
                semantic_digest=item.semantic_digest,
                created_at=item.created_at,
            )
            for item in proposals
        ],
    )


def create_intelligence_router(
    authenticator: AuthenticationPort,
    identity_uow: UnitOfWorkFactory,
    runtime: AgentRunService,
    service: IntelligenceService,
    environment: str,
    audit: IdentityAuditService,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["intelligence"])

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

    @router.post(
        "/products/{product_id}/intelligence/analyze",
        response_model=AgentRunResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def analyze(product_id: UUID, value: AnalyzeRequest, ctx: Context) -> AgentRunResponse:
        try:
            return _agent_run(
                await runtime.request_intelligence(
                    ctx, product_id=product_id, idempotency_key=value.idempotency_key
                )
            )
        except (AgentRuntimeError, IntelligenceError, ValueError) as error:
            code = getattr(error, "code", "INTELLIGENCE_INVALID_REQUEST")
            raise HTTPException(
                status_code=403 if "DENIED" in code else 409, detail=code.lower()
            ) from error

    @router.get("/products/{product_id}/intelligence/reports", response_model=list[ReportResponse])
    async def list_reports(product_id: UUID, ctx: Context) -> list[ReportResponse]:
        values: list[ReportResponse] = []
        for item in await service.list_reports(ctx, product_id):
            report, candidates, proposals = await service.get_report(ctx, item.id)
            decisions = {
                proposal.id: decision.decision.value
                for proposal in proposals
                if (decision := await service.current_experiment_decision(ctx, proposal.id))
                is not None
            }
            values.append(_report(report, candidates, proposals, decisions))
        return values

    @router.get("/intelligence/reports/{report_id}", response_model=ReportResponse)
    async def get_report(report_id: UUID, ctx: Context) -> ReportResponse:
        try:
            report, candidates, proposals = await service.get_report(ctx, report_id)
            decisions = {
                proposal.id: decision.decision.value
                for proposal in proposals
                if (decision := await service.current_experiment_decision(ctx, proposal.id))
                is not None
            }
            return _report(report, candidates, proposals, decisions)
        except IntelligenceError as error:
            raise HTTPException(status_code=404, detail=error.code.lower()) from error

    @router.post(
        "/intelligence/insights/{candidate_id}/decision", status_code=status.HTTP_201_CREATED
    )
    async def decide_insight(
        candidate_id: UUID, value: DecisionRequest, ctx: Context
    ) -> dict[str, Any]:
        try:
            result = await service.decide_insight(
                ctx, candidate_id, InsightDecisionKind(value.decision)
            )
            return {
                "id": result.id,
                "decision": result.decision.value,
                "candidate_id": result.candidate_id,
                "created_at": result.created_at,
            }
        except (IntelligenceError, ValueError) as error:
            raise HTTPException(
                status_code=403 if "PERMISSION" in getattr(error, "code", "") else 409,
                detail=getattr(error, "code", "invalid_decision").lower(),
            ) from error

    @router.post(
        "/intelligence/experiments/{proposal_id}/decision", status_code=status.HTTP_201_CREATED
    )
    async def decide_experiment(
        proposal_id: UUID, value: DecisionRequest, ctx: Context
    ) -> dict[str, Any]:
        try:
            result = await service.decide_experiment(
                ctx, proposal_id, ExperimentDecisionKind(value.decision)
            )
            return {
                "id": result.id,
                "decision": result.decision.value,
                "proposal_id": result.proposal_id,
                "created_at": result.created_at,
            }
        except (IntelligenceError, ValueError) as error:
            raise HTTPException(
                status_code=403 if "PERMISSION" in getattr(error, "code", "") else 409,
                detail=getattr(error, "code", "invalid_decision").lower(),
            ) from error

    @router.post(
        "/intelligence/experiments/{proposal_id}/generate-concepts",
        response_model=AgentRunResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def generate_concepts(
        proposal_id: UUID, value: GenerateConceptsRequest, ctx: Context
    ) -> AgentRunResponse:
        try:
            proposal = await service.approved_proposal(ctx, proposal_id)
            run = await runtime.request_creative_strategist(
                ctx,
                product_id=proposal.product_id,
                request=CreativeStrategyRequest(value.concept_count, value.channel_intent),
                idempotency_key=value.idempotency_key,
                approved_experiment_proposal_id=proposal.id,
            )
            return _agent_run(run)
        except (AgentRuntimeError, IntelligenceError, ValueError) as error:
            raise HTTPException(
                status_code=409, detail=getattr(error, "code", "experiment_handoff_denied").lower()
            ) from error

    return router
