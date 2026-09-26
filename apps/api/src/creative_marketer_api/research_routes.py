from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from creative_marketer.agent_runtime.application import AgentRunService
from creative_marketer.agent_runtime.domain import AgentRun, AgentRuntimeError, ResearchSnapshot
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
from creative_marketer.research.application import (
    ResearchConflict,
    ResearchNotFound,
    ResearchPermissionDenied,
    ResearchService,
    SocialCapabilityNotSupported,
)
from creative_marketer.research.domain import (
    EvidenceSnapshot,
    ResearchCategory,
    ResearchContextManifest,
    ResearchSource,
    ResearchTarget,
    ResearchTargetKind,
    ResearchValidationError,
    SocialEvidenceProvenance,
    SocialEvidenceSnapshot,
    SocialEvidenceType,
    SocialPlatform,
    SourceFetch,
)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceCreate(Contract):
    url: str = Field(min_length=1, max_length=2048)
    display_name: str = Field(min_length=1, max_length=200)
    category: ResearchCategory
    refresh: bool = True


class SourceResponse(Contract):
    id: UUID
    product_id: UUID
    source_type: str
    canonical_url: str
    display_name: str
    category: str
    status: str
    created_at: datetime
    updated_at: datetime
    can_edit: bool


class FetchResponse(Contract):
    id: UUID
    source_id: UUID
    status: str
    final_url: str | None
    http_status: int | None
    content_type: str | None
    raw_digest: str | None
    raw_byte_size: int | None
    evidence_snapshot_id: UUID | None
    failure_code: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class SourceCreatedResponse(Contract):
    source: SourceResponse
    fetch: FetchResponse | None


class EvidenceBlockResponse(Contract):
    kind: str
    text: str
    ordinal: int


class EvidenceResponse(Contract):
    id: UUID
    product_id: UUID
    source_id: UUID
    source_fetch_id: UUID
    final_url: str
    title: str | None
    blocks: list[EvidenceBlockResponse]
    outbound_links: list[str]
    structured_metadata: dict[str, str]
    raw_digest: str
    semantic_digest: str
    schema_version: int
    extractor_version: str
    instruction_like_content: bool
    captured_at: datetime


class EvidenceReferenceResponse(Contract):
    source_id: UUID
    evidence_snapshot_id: UUID
    semantic_digest: str
    category: str
    captured_at: datetime


class SocialEvidenceReferenceResponse(Contract):
    research_target_id: UUID
    social_evidence_snapshot_id: UUID
    semantic_digest: str
    platform: str
    captured_at: datetime


class ManifestResponse(Contract):
    product_id: UUID
    schema_version: int
    digest: str
    evidence: list[EvidenceReferenceResponse]
    social_evidence: list[SocialEvidenceReferenceResponse] = Field(default_factory=list)


class ResearchTargetCreate(Contract):
    kind: ResearchTargetKind
    display_name: str = Field(min_length=1, max_length=200)
    website_url: str | None = Field(default=None, max_length=2048)
    platform: SocialPlatform | None = None
    platform_handle: str | None = Field(default=None, max_length=200)
    platform_profile_url: str | None = Field(default=None, max_length=2048)
    platform_identifier: str | None = Field(default=None, max_length=200)


class ResearchTargetResponse(Contract):
    id: UUID
    product_id: UUID
    kind: ResearchTargetKind
    display_name: str
    website_url: str | None
    platform: SocialPlatform | None
    platform_handle: str | None
    platform_profile_url: str | None
    platform_identifier: str | None
    status: Literal["active", "archived"]
    created_at: datetime
    updated_at: datetime
    can_edit: bool


class ManualSocialEvidenceCreate(Contract):
    platform: SocialPlatform
    evidence_type: SocialEvidenceType
    source_url: str | None = Field(default=None, max_length=2048)
    destination_url: str | None = Field(default=None, max_length=2048)
    advertiser_name: str | None = Field(default=None, max_length=300)
    platform_content_id: str | None = Field(default=None, max_length=200)
    headline: str | None = Field(default=None, max_length=1000)
    body_text: str | None = Field(default=None, max_length=8000)
    cta: str | None = Field(default=None, max_length=300)
    media_type: str | None = Field(default=None, max_length=100)
    placements: list[str] = Field(default_factory=list, max_length=20)
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    activity_status: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=200)
    media_asset_id: UUID | None = None


class SocialEvidenceResponse(Contract):
    id: UUID
    product_id: UUID
    research_target_id: UUID
    platform: SocialPlatform
    evidence_type: SocialEvidenceType
    provenance: SocialEvidenceProvenance
    source_url: str | None
    destination_url: str | None
    advertiser_name: str | None
    advertiser_platform_id: str | None
    platform_content_id: str | None
    headline: str | None
    body_text: str | None
    cta: str | None
    ad_objective: str | None
    media_type: str | None
    placements: list[str]
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    activity_status: str | None
    region: str | None
    reach_range: str | None
    source_provider: str | None
    media_asset_id: UUID | None
    semantic_digest: str
    schema_version: int
    rights_status: Literal["restricted"]
    allowed_uses: list[str]
    captured_at: datetime


class SocialCapabilityResponse(Contract):
    platform: SocialPlatform
    enabled: bool
    capabilities: list[str]


class SocialProviderQuery(Contract):
    capability: str = Field(min_length=1, max_length=100)


class AgentRunStart(Contract):
    idempotency_key: str = Field(min_length=1, max_length=128)


class AgentRunResponse(Contract):
    id: UUID
    tenant_id: UUID
    product_id: UUID
    status: str
    operational_status: str
    is_stranded: bool
    recovery_of_run_id: UUID | None
    requested_agent_definition_id: UUID
    resolved_agent_definition_id: UUID
    agent_version_id: UUID
    agent_version_number: int
    agent_configuration_digest: str
    prompt_revision: str
    agent_type: str
    input_context_kind: str
    input_context_schema_version: int
    input_context_digest: str
    model_profile_key: str
    output_contract_key: str
    output_contract_version: int
    resolved_provider: str | None
    resolved_model: str | None
    model_route_version: str | None
    pricing_version: str | None
    product_snapshot_id: UUID
    product_snapshot_digest: str
    research_context_digest: str
    context_digest: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost: str
    reserved_cost: str
    unknown_cost: str
    original_unknown_cost: str
    reconciled_actual_cost: str
    remaining_unknown_cost: str
    currency: str
    result_ref: str | None
    failure_code: str | None
    recovery_classification: str | None


class ResearchCitationResponse(Contract):
    evidence_snapshot_id: UUID
    block_index: int
    block_digest: str


class ResearchFindingResponse(Contract):
    key: str
    category: str
    statement: str
    confidence: str
    basis: str
    citations: list[ResearchCitationResponse]
    scope: str
    implication: str | None


class RecommendedSourceResponse(Contract):
    category: str
    reason: str
    suggested_query: str


class ResearchSnapshotResponse(Contract):
    id: UUID
    product_id: UUID
    agent_run_id: UUID
    product_snapshot_id: UUID
    product_snapshot_digest: str
    research_context_digest: str
    findings: list[ResearchFindingResponse]
    research_gaps: list[str]
    recommended_next_sources: list[RecommendedSourceResponse]
    semantic_digest: str
    created_at: datetime
    valid_until: datetime
    freshness: str


def _editable(ctx: ExecutionContext) -> bool:
    return (
        ctx.membership_role.value in {"owner", "admin"} and ctx.membership_status.value == "active"
    )


def _source(value: ResearchSource, editable: bool) -> SourceResponse:
    return SourceResponse(
        id=value.id,
        product_id=value.product_id,
        source_type=value.source_type.value,
        canonical_url=value.canonical_url,
        display_name=value.display_name,
        category=value.category.value,
        status=value.status.value,
        created_at=value.created_at,
        updated_at=value.updated_at,
        can_edit=editable,
    )


def _fetch(value: SourceFetch) -> FetchResponse:
    return FetchResponse(
        id=value.id,
        source_id=value.source_id,
        status=value.status.value,
        final_url=value.final_url,
        http_status=value.http_status,
        content_type=value.content_type,
        raw_digest=value.raw_digest,
        raw_byte_size=value.raw_byte_size,
        evidence_snapshot_id=value.evidence_snapshot_id,
        failure_code=value.failure_code.value if value.failure_code else None,
        started_at=value.started_at,
        completed_at=value.completed_at,
        created_at=value.created_at,
    )


def _evidence(value: EvidenceSnapshot) -> EvidenceResponse:
    return EvidenceResponse(
        id=value.id,
        product_id=value.product_id,
        source_id=value.source_id,
        source_fetch_id=value.source_fetch_id,
        final_url=value.final_url,
        title=value.title,
        blocks=[
            EvidenceBlockResponse(kind=item.kind.value, text=item.text, ordinal=item.ordinal)
            for item in value.blocks
        ],
        outbound_links=list(value.outbound_links),
        structured_metadata=dict(value.structured_metadata),
        raw_digest=value.raw_digest,
        semantic_digest=value.semantic_digest,
        schema_version=value.schema_version,
        extractor_version=value.extractor_version,
        instruction_like_content=value.instruction_like_content,
        captured_at=value.captured_at,
    )


def _manifest(value: ResearchContextManifest) -> ManifestResponse:
    return ManifestResponse(
        product_id=value.product_id,
        schema_version=value.schema_version,
        digest=value.digest,
        evidence=[
            EvidenceReferenceResponse(
                source_id=item.source_id,
                evidence_snapshot_id=item.evidence_snapshot_id,
                semantic_digest=item.semantic_digest,
                category=item.category.value,
                captured_at=item.captured_at,
            )
            for item in value.evidence
        ],
        social_evidence=[
            SocialEvidenceReferenceResponse(
                research_target_id=item.research_target_id,
                social_evidence_snapshot_id=item.social_evidence_snapshot_id,
                semantic_digest=item.semantic_digest,
                platform=item.platform.value,
                captured_at=item.captured_at,
            )
            for item in value.social_evidence
        ],
    )


def _target(value: ResearchTarget, editable: bool) -> ResearchTargetResponse:
    return ResearchTargetResponse(
        id=value.id,
        product_id=value.product_id,
        kind=value.kind,
        display_name=value.display_name,
        website_url=value.website_url,
        platform=value.platform,
        platform_handle=value.platform_handle,
        platform_profile_url=value.platform_profile_url,
        platform_identifier=value.platform_identifier,
        status=value.status.value,
        created_at=value.created_at,
        updated_at=value.updated_at,
        can_edit=editable,
    )


def _social_evidence(value: SocialEvidenceSnapshot) -> SocialEvidenceResponse:
    return SocialEvidenceResponse(
        id=value.id,
        product_id=value.product_id,
        research_target_id=value.research_target_id,
        platform=value.platform,
        evidence_type=value.evidence_type,
        provenance=value.provenance,
        source_url=value.source_url,
        destination_url=value.destination_url,
        advertiser_name=value.advertiser_name,
        advertiser_platform_id=value.advertiser_platform_id,
        platform_content_id=value.platform_content_id,
        headline=value.headline,
        body_text=value.body_text,
        cta=value.cta,
        ad_objective=value.ad_objective,
        media_type=value.media_type,
        placements=list(value.placements),
        first_seen_at=value.first_seen_at,
        last_seen_at=value.last_seen_at,
        activity_status=value.activity_status,
        region=value.region,
        reach_range=value.reach_range,
        source_provider=value.source_provider,
        media_asset_id=value.media_asset_id,
        semantic_digest=value.semantic_digest,
        schema_version=value.schema_version,
        rights_status=value.rights_status,
        allowed_uses=list(value.allowed_uses),
        captured_at=value.captured_at,
    )


def _agent_run(value: AgentRun) -> AgentRunResponse:
    return AgentRunResponse(
        id=value.id,
        tenant_id=value.tenant_id,
        product_id=value.product_id,
        status=value.status.value,
        operational_status=value.operational_status,
        is_stranded=value.is_stranded,
        recovery_of_run_id=value.recovery_of_run_id,
        requested_agent_definition_id=value.requested_agent_definition_id,
        resolved_agent_definition_id=value.resolved_agent_definition_id,
        agent_version_id=value.agent_version_id,
        agent_version_number=value.agent_version_number,
        agent_configuration_digest=value.agent_configuration_digest,
        prompt_revision=value.prompt_revision,
        agent_type=value.agent_type,
        input_context_kind=value.input_context_kind,
        input_context_schema_version=value.input_context_schema_version,
        input_context_digest=value.input_context_digest,
        model_profile_key=value.model_profile_key,
        output_contract_key=value.output_contract_key,
        output_contract_version=value.output_contract_version,
        resolved_provider=value.resolved_provider,
        resolved_model=value.resolved_model,
        model_route_version=value.resolved_model_route_version,
        pricing_version=value.pricing_version,
        product_snapshot_id=value.product_snapshot_id,
        product_snapshot_digest=value.product_snapshot_digest,
        research_context_digest=value.research_context_digest,
        context_digest=value.context_digest,
        created_at=value.created_at,
        started_at=value.started_at,
        completed_at=value.completed_at,
        input_tokens=value.input_tokens,
        output_tokens=value.output_tokens,
        total_tokens=value.total_tokens,
        estimated_cost=str(value.estimated_cost),
        reserved_cost=str(value.reserved_cost),
        unknown_cost=str(value.unknown_cost),
        original_unknown_cost=str(value.unknown_cost),
        reconciled_actual_cost=str(value.reconciled_actual_cost),
        remaining_unknown_cost=str(value.remaining_unknown_cost),
        currency=value.currency,
        result_ref=value.result_ref,
        failure_code=value.failure_code,
        recovery_classification=value.recovery_classification,
    )


def _research_snapshot(value: ResearchSnapshot, freshness: str) -> ResearchSnapshotResponse:
    return ResearchSnapshotResponse(
        id=value.id,
        product_id=value.product_id,
        agent_run_id=value.agent_run_id,
        product_snapshot_id=value.product_snapshot_id,
        product_snapshot_digest=value.product_snapshot_digest,
        research_context_digest=value.research_context_digest,
        findings=[
            ResearchFindingResponse(
                key=item.key,
                category=item.category.value,
                statement=item.statement,
                confidence=item.confidence.value,
                basis="INFERRED" if item.scope.startswith("INFERRED: ") else "OBSERVED",
                citations=[
                    ResearchCitationResponse(
                        evidence_snapshot_id=c.evidence_snapshot_id,
                        block_index=c.block_index,
                        block_digest=c.block_digest,
                    )
                    for c in item.citations
                ],
                scope=item.scope.removeprefix("INFERRED: ").removeprefix("OBSERVED: "),
                implication=item.implication,
            )
            for item in value.findings
        ],
        research_gaps=list(value.research_gaps),
        recommended_next_sources=[
            RecommendedSourceResponse(
                category=item.category,
                reason=item.reason,
                suggested_query=item.suggested_query,
            )
            for item in value.recommended_next_sources
        ],
        semantic_digest=value.semantic_digest,
        created_at=value.created_at,
        valid_until=value.valid_until,
        freshness=freshness,
    )


def create_research_router(
    authenticator: AuthenticationPort,
    identity_uow: UnitOfWorkFactory,
    service: ResearchService,
    environment: str,
    audit: IdentityAuditService,
    agent_service: AgentRunService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["research"])

    async def context(
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

    Context = Annotated[ExecutionContext, Depends(context)]

    def map_error(error: Exception) -> HTTPException:
        if isinstance(error, ResearchNotFound):
            return HTTPException(status_code=404, detail=str(error).replace(" ", "_"))
        if isinstance(error, ResearchPermissionDenied):
            return HTTPException(status_code=403, detail="research_mutation_denied")
        if isinstance(error, ResearchConflict):
            return HTTPException(status_code=409, detail=str(error).replace(" ", "_"))
        if isinstance(error, SocialCapabilityNotSupported):
            return HTTPException(status_code=422, detail=error.code)
        return HTTPException(status_code=422, detail=str(error))

    @router.post(
        "/products/{product_id}/research-targets",
        response_model=ResearchTargetResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_target(
        product_id: UUID, value: ResearchTargetCreate, ctx: Context
    ) -> ResearchTargetResponse:
        try:
            target = await service.create_target(
                ctx,
                product_id=product_id,
                kind=value.kind,
                display_name=value.display_name,
                website_url=value.website_url,
                platform=value.platform,
                platform_handle=value.platform_handle,
                platform_profile_url=value.platform_profile_url,
                platform_identifier=value.platform_identifier,
            )
            return _target(target, True)
        except (
            ResearchNotFound,
            ResearchPermissionDenied,
            ResearchConflict,
            ResearchValidationError,
        ) as error:
            raise map_error(error) from error

    @router.get(
        "/products/{product_id}/research-targets", response_model=list[ResearchTargetResponse]
    )
    async def list_targets(product_id: UUID, ctx: Context) -> list[ResearchTargetResponse]:
        try:
            return [
                _target(value, _editable(ctx))
                for value in await service.list_targets(ctx, product_id)
            ]
        except ResearchNotFound as error:
            raise map_error(error) from error

    @router.post("/research-targets/{target_id}/archive", response_model=ResearchTargetResponse)
    async def archive_target(target_id: UUID, ctx: Context) -> ResearchTargetResponse:
        try:
            return _target(await service.archive_target(ctx, target_id), True)
        except (ResearchNotFound, ResearchPermissionDenied, ResearchConflict) as error:
            raise map_error(error) from error

    @router.post(
        "/research-targets/{target_id}/social-evidence",
        response_model=SocialEvidenceResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def add_manual_social_evidence(
        target_id: UUID, value: ManualSocialEvidenceCreate, ctx: Context
    ) -> SocialEvidenceResponse:
        try:
            evidence = await service.add_manual_social_evidence(
                ctx,
                target_id=target_id,
                platform=value.platform,
                evidence_type=value.evidence_type,
                source_url=value.source_url,
                destination_url=value.destination_url,
                advertiser_name=value.advertiser_name,
                platform_content_id=value.platform_content_id,
                headline=value.headline,
                body_text=value.body_text,
                cta=value.cta,
                media_type=value.media_type,
                placements=tuple(value.placements),
                first_seen_at=value.first_seen_at,
                last_seen_at=value.last_seen_at,
                activity_status=value.activity_status,
                region=value.region,
                media_asset_id=value.media_asset_id,
            )
            return _social_evidence(evidence)
        except (
            ResearchNotFound,
            ResearchPermissionDenied,
            ResearchConflict,
            ResearchValidationError,
        ) as error:
            raise map_error(error) from error

    @router.get(
        "/products/{product_id}/social-evidence", response_model=list[SocialEvidenceResponse]
    )
    async def list_social_evidence(product_id: UUID, ctx: Context) -> list[SocialEvidenceResponse]:
        try:
            return [
                _social_evidence(value)
                for value in await service.list_social_evidence(ctx, product_id)
            ]
        except ResearchNotFound as error:
            raise map_error(error) from error

    @router.get("/social-evidence/{evidence_id}", response_model=SocialEvidenceResponse)
    async def get_social_evidence(evidence_id: UUID, ctx: Context) -> SocialEvidenceResponse:
        try:
            return _social_evidence(await service.get_social_evidence(ctx, evidence_id))
        except ResearchNotFound as error:
            raise map_error(error) from error

    @router.get("/social-research/capabilities", response_model=list[SocialCapabilityResponse])
    async def social_capabilities(ctx: Context) -> list[SocialCapabilityResponse]:
        del ctx
        return [
            SocialCapabilityResponse(
                platform=platform,
                enabled=bool(capabilities),
                capabilities=sorted(capabilities),
            )
            for platform, capabilities in service.social_capabilities().items()
        ]

    @router.post(
        "/research-targets/{target_id}/provider-query",
        response_model=list[SocialEvidenceResponse],
    )
    async def query_social_provider(
        target_id: UUID, value: SocialProviderQuery, ctx: Context
    ) -> list[SocialEvidenceResponse]:
        try:
            return [
                _social_evidence(item)
                for item in await service.query_social_provider(ctx, target_id, value.capability)
            ]
        except (
            ResearchNotFound,
            ResearchPermissionDenied,
            ResearchConflict,
            SocialCapabilityNotSupported,
        ) as error:
            raise map_error(error) from error

    @router.post(
        "/products/{product_id}/research-sources",
        response_model=SourceCreatedResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_source(
        product_id: UUID, value: SourceCreate, ctx: Context
    ) -> SourceCreatedResponse:
        try:
            source, fetch = await service.create_source(
                ctx,
                product_id=product_id,
                url=value.url,
                display_name=value.display_name,
                category=value.category,
                refresh=value.refresh,
            )
            return SourceCreatedResponse(
                source=_source(source, True), fetch=_fetch(fetch) if fetch else None
            )
        except (
            ResearchNotFound,
            ResearchPermissionDenied,
            ResearchConflict,
            ResearchValidationError,
        ) as error:
            raise map_error(error) from error

    @router.get("/products/{product_id}/research-sources", response_model=list[SourceResponse])
    async def list_sources(product_id: UUID, ctx: Context) -> list[SourceResponse]:
        try:
            return [
                _source(value, _editable(ctx))
                for value in await service.list_sources(ctx, product_id)
            ]
        except ResearchNotFound as error:
            raise map_error(error) from error

    @router.get("/research-sources/{source_id}", response_model=SourceResponse)
    async def get_source(source_id: UUID, ctx: Context) -> SourceResponse:
        try:
            return _source(await service.get_source(ctx, source_id), _editable(ctx))
        except ResearchNotFound as error:
            raise map_error(error) from error

    @router.post("/research-sources/{source_id}/refresh", response_model=FetchResponse)
    async def refresh_source(source_id: UUID, ctx: Context) -> FetchResponse:
        try:
            return _fetch(await service.refresh_source(ctx, source_id))
        except (ResearchNotFound, ResearchPermissionDenied, ResearchConflict) as error:
            raise map_error(error) from error

    @router.post("/research-sources/{source_id}/archive", response_model=SourceResponse)
    async def archive_source(source_id: UUID, ctx: Context) -> SourceResponse:
        try:
            return _source(await service.archive_source(ctx, source_id), True)
        except (ResearchNotFound, ResearchPermissionDenied, ResearchConflict) as error:
            raise map_error(error) from error

    @router.get("/research-sources/{source_id}/fetches", response_model=list[FetchResponse])
    async def list_fetches(source_id: UUID, ctx: Context) -> list[FetchResponse]:
        try:
            return [_fetch(value) for value in await service.list_fetches(ctx, source_id)]
        except ResearchNotFound as error:
            raise map_error(error) from error

    @router.get("/research-evidence/{evidence_id}", response_model=EvidenceResponse)
    async def get_evidence(evidence_id: UUID, ctx: Context) -> EvidenceResponse:
        try:
            return _evidence(await service.get_evidence(ctx, evidence_id))
        except ResearchNotFound as error:
            raise map_error(error) from error

    @router.get("/products/{product_id}/research-context-manifest", response_model=ManifestResponse)
    async def get_manifest(product_id: UUID, ctx: Context) -> ManifestResponse:
        try:
            return _manifest(await service.manifest(ctx, product_id))
        except ResearchNotFound as error:
            raise map_error(error) from error

    if agent_service is not None:

        @router.post(
            "/products/{product_id}/research/runs",
            response_model=AgentRunResponse,
            status_code=status.HTTP_202_ACCEPTED,
        )
        async def start_researcher(
            product_id: UUID, value: AgentRunStart, ctx: Context
        ) -> AgentRunResponse:
            try:
                return _agent_run(
                    await agent_service.request_researcher(
                        ctx, product_id=product_id, idempotency_key=value.idempotency_key
                    )
                )
            except (AgentRuntimeError, ValueError) as error:
                code = getattr(error, "code", "AGENT_RUNTIME_ERROR")
                status_code = (
                    403
                    if code == "AGENT_RUN_DENIED"
                    else 422
                    if isinstance(error, ValueError)
                    else 409
                )
                raise HTTPException(status_code=status_code, detail=code.lower()) from error

        @router.get("/products/{product_id}/research/runs", response_model=list[AgentRunResponse])
        async def list_researcher_runs(product_id: UUID, ctx: Context) -> list[AgentRunResponse]:
            return [_agent_run(item) for item in await agent_service.list_runs(ctx, product_id)]

        @router.get("/agent-runs/{run_id}", response_model=AgentRunResponse)
        async def get_agent_run(run_id: UUID, ctx: Context) -> AgentRunResponse:
            try:
                return _agent_run(await agent_service.get_run(ctx, run_id))
            except AgentRuntimeError as error:
                raise HTTPException(status_code=404, detail=error.code.lower()) from error

        @router.get(
            "/products/{product_id}/research/snapshots",
            response_model=list[ResearchSnapshotResponse],
        )
        async def list_research_snapshots(
            product_id: UUID, ctx: Context
        ) -> list[ResearchSnapshotResponse]:
            values = await agent_service.list_snapshots(ctx, product_id)
            return [
                _research_snapshot(item, await agent_service.snapshot_freshness(ctx, item))
                for item in values
            ]

        @router.get("/research/snapshots/{snapshot_id}", response_model=ResearchSnapshotResponse)
        async def get_research_snapshot(
            snapshot_id: UUID, ctx: Context
        ) -> ResearchSnapshotResponse:
            try:
                value = await agent_service.get_snapshot(ctx, snapshot_id)
                return _research_snapshot(value, await agent_service.snapshot_freshness(ctx, value))
            except AgentRuntimeError as error:
                raise HTTPException(status_code=404, detail=error.code.lower()) from error

    return router
