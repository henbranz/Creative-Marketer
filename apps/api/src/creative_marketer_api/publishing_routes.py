from datetime import datetime
from typing import Annotated, Any, Protocol
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

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
from creative_marketer.publishing.application import PublicationDraftRecord, PublishingService
from creative_marketer.publishing.domain import (
    Publication,
    PublicationDecisionState,
    PublicationError,
    PublicationMode,
    SocialAccount,
    SocialPlatform,
)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicationApprovalBinder(Protocol):
    async def __call__(self, context: ExecutionContext, draft_id: UUID) -> None: ...


class SocialAccountWrite(Contract):
    platform: SocialPlatform
    display_name: str = Field(min_length=1, max_length=200)
    external_account_id: str = Field(min_length=1, max_length=256)
    username: str | None = Field(default=None, max_length=200)


class SocialAccountResponse(Contract):
    id: UUID
    platform: str
    display_name: str
    external_account_id: str
    username: str | None
    status: str
    provider: str
    capabilities: dict[str, Any]


class PublicationDraftWrite(Contract):
    final_creative_id: UUID
    social_account_id: UUID
    caption: str = Field(min_length=1, max_length=5000)
    title: str | None = Field(default=None, max_length=500)
    hashtags: list[str] = Field(default_factory=list, max_length=30)
    destination_url: HttpUrl | None = None
    mode: PublicationMode
    scheduled_at: datetime | None = None
    platform_settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def schedule_matches_mode(self) -> "PublicationDraftWrite":
        if (self.mode is PublicationMode.SCHEDULE) != (self.scheduled_at is not None):
            raise ValueError("scheduled_at is required only for SCHEDULE mode")
        return self


class PublicationDraftResponse(Contract):
    id: UUID
    product_id: UUID
    final_creative_id: UUID
    output_asset_id: UUID
    platform: str
    social_account_id: UUID
    external_destination_id: str
    caption: str
    title: str | None
    hashtags: list[str]
    destination_url: str | None
    mode: str
    scheduled_at: datetime | None
    semantic_digest: str
    decision_state: str | None
    approval_action_digest: str | None
    status: str
    failure_code: str | None
    created_at: datetime


class PublicationResponse(Contract):
    id: UUID
    product_id: UUID
    publication_draft_id: UUID
    final_creative_id: UUID
    output_asset_id: UUID
    platform: str
    social_account_id: UUID
    external_post_id: str
    canonical_permalink: str | None
    provider: str
    status: str
    submitted_at: datetime
    published_at: datetime | None


def _account(value: SocialAccount) -> SocialAccountResponse:
    return SocialAccountResponse(
        id=value.id,
        platform=value.platform.value,
        display_name=value.display_name,
        external_account_id=value.external_account_id,
        username=value.username,
        status=value.status.value,
        provider=value.provider,
        capabilities=dict(value.capabilities),
    )


def _draft(value: PublicationDraftRecord) -> PublicationDraftResponse:
    item = value.draft
    return PublicationDraftResponse(
        id=item.id,
        product_id=item.product_id,
        final_creative_id=item.final_creative_id,
        output_asset_id=item.output_asset_id,
        platform=item.platform.value,
        social_account_id=item.social_account_id,
        external_destination_id=item.external_destination_id,
        caption=item.caption,
        title=item.title,
        hashtags=list(item.hashtags),
        destination_url=item.destination_url,
        mode=item.mode.value,
        scheduled_at=item.scheduled_at,
        semantic_digest=item.semantic_digest,
        decision_state=value.decision.state.value if value.decision else None,
        approval_action_digest=value.decision.binding.action_digest if value.decision else None,
        status=value.job.status.value if value.job else "PENDING_APPROVAL",
        failure_code=value.job.failure_code if value.job else None,
        created_at=item.created_at,
    )


def _publication(value: Publication) -> PublicationResponse:
    return PublicationResponse(
        id=value.id,
        product_id=value.product_id,
        publication_draft_id=value.publication_draft_id,
        final_creative_id=value.final_creative_id,
        output_asset_id=value.output_asset_id,
        platform=value.platform.value,
        social_account_id=value.social_account_id,
        external_post_id=value.external_post_id,
        canonical_permalink=value.canonical_permalink,
        provider=value.provider,
        status=value.status.value,
        submitted_at=value.submitted_at,
        published_at=value.published_at,
    )


def create_publishing_router(
    authenticator: AuthenticationPort,
    identity_uow: UnitOfWorkFactory,
    service: PublishingService,
    environment: str,
    audit: IdentityAuditService,
    approval_binder: PublicationApprovalBinder | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["publishing"])

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

    def failure(error: PublicationError) -> HTTPException:
        code = error.code
        return HTTPException(
            status_code=404 if "NOT_FOUND" in code else 403 if "DENIED" in code else 409,
            detail=code.lower(),
        )

    @router.get("/social/accounts", response_model=list[SocialAccountResponse])
    async def list_accounts(ctx: Context) -> list[SocialAccountResponse]:
        return [_account(value) for value in await service.list_accounts(ctx)]

    @router.post(
        "/social/accounts",
        response_model=SocialAccountResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_account(value: SocialAccountWrite, ctx: Context) -> SocialAccountResponse:
        if environment not in {"development", "test"}:
            raise HTTPException(status_code=404, detail="fake_social_account_creation_unavailable")
        try:
            return _account(
                await service.create_account(
                    ctx,
                    platform=value.platform,
                    display_name=value.display_name,
                    external_account_id=value.external_account_id,
                    username=value.username,
                )
            )
        except PublicationError as error:
            raise failure(error) from error

    @router.post(
        "/products/{product_id}/publication-drafts",
        response_model=PublicationDraftResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_draft(
        product_id: UUID, value: PublicationDraftWrite, ctx: Context
    ) -> PublicationDraftResponse:
        try:
            return _draft(
                await service.create_draft(
                    ctx,
                    product_id=product_id,
                    final_creative_id=value.final_creative_id,
                    social_account_id=value.social_account_id,
                    caption=value.caption,
                    title=value.title,
                    hashtags=tuple(value.hashtags),
                    destination_url=str(value.destination_url) if value.destination_url else None,
                    mode=value.mode,
                    scheduled_at=value.scheduled_at,
                    platform_settings=value.platform_settings,
                )
            )
        except PublicationError as error:
            raise failure(error) from error

    @router.get(
        "/products/{product_id}/publication-drafts", response_model=list[PublicationDraftResponse]
    )
    async def list_drafts(product_id: UUID, ctx: Context) -> list[PublicationDraftResponse]:
        return [_draft(value) for value in await service.list_drafts(ctx, product_id)]

    @router.get("/publication-drafts/{draft_id}", response_model=PublicationDraftResponse)
    async def get_draft(draft_id: UUID, ctx: Context) -> PublicationDraftResponse:
        try:
            return _draft(await service.get_draft(ctx, draft_id))
        except PublicationError as error:
            raise failure(error) from error

    async def decide(
        draft_id: UUID, state: PublicationDecisionState, ctx: ExecutionContext
    ) -> PublicationDraftResponse:
        try:
            record = await service.decide(ctx, draft_id, state)
            if state is PublicationDecisionState.APPROVED:
                if approval_binder is None:
                    raise RuntimeError("publication governance binding is unavailable")
                await approval_binder(ctx, draft_id)
            return _draft(record)
        except PublicationError as error:
            raise failure(error) from error

    @router.post("/publication-drafts/{draft_id}/approve", response_model=PublicationDraftResponse)
    async def approve(draft_id: UUID, ctx: Context) -> PublicationDraftResponse:
        return await decide(draft_id, PublicationDecisionState.APPROVED, ctx)

    @router.post("/publication-drafts/{draft_id}/reject", response_model=PublicationDraftResponse)
    async def reject(draft_id: UUID, ctx: Context) -> PublicationDraftResponse:
        return await decide(draft_id, PublicationDecisionState.REJECTED, ctx)

    @router.post("/publication-drafts/{draft_id}/execute", response_model=PublicationDraftResponse)
    async def execute(draft_id: UUID, ctx: Context) -> PublicationDraftResponse:
        if environment not in {"development", "test"}:
            raise HTTPException(status_code=404, detail="fake_social_execution_unavailable")
        try:
            return _draft(await service.execute(ctx, draft_id))
        except PublicationError as error:
            raise failure(error) from error

    @router.post("/publication-drafts/{draft_id}/cancel", response_model=PublicationDraftResponse)
    async def cancel(draft_id: UUID, ctx: Context) -> PublicationDraftResponse:
        try:
            return _draft(await service.cancel(ctx, draft_id))
        except PublicationError as error:
            raise failure(error) from error

    @router.post(
        "/publication-drafts/{draft_id}/reconcile", response_model=PublicationDraftResponse
    )
    async def reconcile(draft_id: UUID, ctx: Context) -> PublicationDraftResponse:
        try:
            return _draft(await service.reconcile(ctx, draft_id))
        except PublicationError as error:
            raise failure(error) from error

    @router.get("/products/{product_id}/publications", response_model=list[PublicationResponse])
    async def list_publications(product_id: UUID, ctx: Context) -> list[PublicationResponse]:
        return [_publication(value) for value in await service.list_publications(ctx, product_id)]

    @router.get("/publications/{publication_id}", response_model=PublicationResponse)
    async def get_publication(publication_id: UUID, ctx: Context) -> PublicationResponse:
        try:
            return _publication(await service.get_publication(ctx, publication_id))
        except PublicationError as error:
            raise failure(error) from error

    return router
