from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

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
)
from creative_marketer.research.domain import (
    EvidenceSnapshot,
    ResearchCategory,
    ResearchContextManifest,
    ResearchSource,
    ResearchValidationError,
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


class ManifestResponse(Contract):
    product_id: UUID
    schema_version: int
    digest: str
    evidence: list[EvidenceReferenceResponse]


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
    )


def create_research_router(
    authenticator: AuthenticationPort,
    identity_uow: UnitOfWorkFactory,
    service: ResearchService,
    environment: str,
    audit: IdentityAuditService,
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
        return HTTPException(status_code=422, detail=str(error))

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

    return router
