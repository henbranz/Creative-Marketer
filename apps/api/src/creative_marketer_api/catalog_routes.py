from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from creative_marketer.audit.identity import IdentityAuditService
from creative_marketer.catalog.application import (
    CatalogConflict,
    CatalogNotFound,
    CatalogPermissionDenied,
    CatalogService,
    ProductWorkspace,
)
from creative_marketer.catalog.asset_application import AssetService, ObjectStoreUnavailable
from creative_marketer.catalog.asset_domain import (
    AllowedUse,
    Asset,
    AssetKind,
    AssetRole,
    AssetStatus,
    RightsStatus,
)
from creative_marketer.catalog.domain import (
    Audience,
    Brand,
    BrandProfile,
    BrandStatus,
    CatalogValidationError,
    Product,
    ProductBrief,
    ProductKnowledgeSnapshot,
    ProductProfile,
    ProductStatus,
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


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AudienceContract(Contract):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    pain_points: list[str] = Field(default_factory=list, max_length=20)
    desires: list[str] = Field(default_factory=list, max_length=20)
    motivations: list[str] = Field(default_factory=list, max_length=20)
    objections: list[str] = Field(default_factory=list, max_length=20)


class BrandProfileContract(Contract):
    industry: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=4000)
    brand_positioning: str = Field(default="", max_length=2000)
    brand_voice: str = Field(default="", max_length=2000)
    tone_attributes: list[str] = Field(default_factory=list, max_length=30)
    visual_style_keywords: list[str] = Field(default_factory=list, max_length=30)
    target_markets: list[str] = Field(default_factory=list, max_length=30)
    primary_language: str = Field(default="en", min_length=1, max_length=16)
    allowed_claims: list[str] = Field(default_factory=list, max_length=30)
    prohibited_claims: list[str] = Field(default_factory=list, max_length=30)
    competitors: list[str] = Field(default_factory=list, max_length=30)


class BrandProfileResponse(BrandProfileContract):
    provenance: Literal["user_provided", "imported", "ai_inferred", "validated"]


class BrandWrite(Contract):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=100)
    website_url: str | None = Field(default=None, max_length=2048)
    status: Literal["active", "archived"] = "active"
    profile: BrandProfileContract = Field(default_factory=BrandProfileContract)


class BrandResponse(BrandWrite):
    id: UUID
    tenant_id: UUID
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    can_edit: bool = False
    profile: BrandProfileResponse


class ProductProfileContract(Contract):
    description: str = Field(default="", max_length=8000)
    features: list[str] = Field(default_factory=list, max_length=30)
    benefits: list[str] = Field(default_factory=list, max_length=30)
    materials: list[str] = Field(default_factory=list, max_length=30)
    variants: list[str] = Field(default_factory=list, max_length=30)
    price: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    estimated_margin: Decimal | None = Field(default=None, ge=0, le=1, decimal_places=4)
    target_audiences: list[AudienceContract] = Field(default_factory=list, max_length=30)
    problems_solved: list[str] = Field(default_factory=list, max_length=30)
    use_cases: list[str] = Field(default_factory=list, max_length=30)
    differentiators: list[str] = Field(default_factory=list, max_length=30)
    purchase_objections: list[str] = Field(default_factory=list, max_length=30)
    allowed_claims: list[str] = Field(default_factory=list, max_length=30)
    prohibited_claims: list[str] = Field(default_factory=list, max_length=30)
    shipping_summary: str | None = Field(default=None, max_length=2000)
    seasonality_notes: str | None = Field(default=None, max_length=2000)
    landing_page_url: str | None = Field(default=None, max_length=2048)
    competitor_product_refs: list[str] = Field(default_factory=list, max_length=30)


class ProductProfileResponse(ProductProfileContract):
    provenance: Literal["user_provided", "imported", "ai_inferred", "validated"]


class BriefContract(Contract):
    product_why: str = Field(default="", max_length=3000)
    emotional_benefits: list[str] = Field(default_factory=list, max_length=30)
    primary_audience: AudienceContract | None = None
    secondary_audiences: list[AudienceContract] = Field(default_factory=list, max_length=30)
    positioning_statement: str = Field(default="", max_length=3000)
    competitive_alternatives: list[str] = Field(default_factory=list, max_length=30)
    why_choose_us: list[str] = Field(default_factory=list, max_length=30)
    current_channels: list[str] = Field(default_factory=list, max_length=30)
    priority_channels: list[str] = Field(default_factory=list, max_length=30)
    conversion_goal: str = Field(default="", max_length=500)
    offers: list[str] = Field(default_factory=list, max_length=30)
    cta_preferences: list[str] = Field(default_factory=list, max_length=30)
    desired_creative_style: str = Field(default="", max_length=2000)
    tones_to_explore: list[str] = Field(default_factory=list, max_length=30)
    tones_to_avoid: list[str] = Field(default_factory=list, max_length=30)
    creative_references: list[str] = Field(default_factory=list, max_length=30)
    mandatory_messaging: list[str] = Field(default_factory=list, max_length=30)
    prohibited_messaging: list[str] = Field(default_factory=list, max_length=30)
    required_disclaimers: list[str] = Field(default_factory=list, max_length=30)
    legal_safety_constraints: list[str] = Field(default_factory=list, max_length=30)
    geographical_restrictions: list[str] = Field(default_factory=list, max_length=30)


class ProductWrite(Contract):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=100)
    sku: str | None = Field(default=None, max_length=100)
    category: str = Field(min_length=1, max_length=200)
    short_description: str = Field(default="", max_length=1000)
    status: Literal["draft", "active", "archived"] = "draft"
    profile: ProductProfileContract = Field(default_factory=ProductProfileContract)


class ProductResponse(ProductWrite):
    id: UUID
    tenant_id: UUID
    brand_id: UUID
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    can_edit: bool = False
    profile: ProductProfileResponse


class ProductCreate(ProductWrite):
    brief: BriefContract = Field(default_factory=BriefContract)


class BriefResponse(BriefContract):
    product_id: UUID
    revision: int
    updated_at: datetime
    can_edit: bool
    provenance: Literal["user_provided", "imported", "ai_inferred", "validated"]


class CompletenessResponse(Contract):
    score: int = Field(ge=0, le=100)
    missing_sections: list[str]
    missing_fields: list[str]


class SnapshotResponse(Contract):
    id: UUID
    product_id: UUID
    schema_version: int
    source_revision: int
    digest: str
    created_at: datetime


class WorkspaceResponse(Contract):
    brand: BrandResponse
    product: ProductResponse
    brief: BriefResponse
    completeness: CompletenessResponse
    latest_snapshot: SnapshotResponse | None


class AssetCreate(Contract):
    brand_id: UUID
    product_id: UUID | None = None
    kind: Literal["image", "video", "document"]
    role: Literal[
        "product_hero",
        "product_detail",
        "lifestyle",
        "logo",
        "brand_guideline",
        "packaging",
        "other",
    ]
    original_filename: str = Field(min_length=1, max_length=255)
    mime_type: Literal[
        "image/jpeg", "image/png", "image/webp", "video/mp4", "video/webm", "application/pdf"
    ]
    rights_status: Literal["confirmed", "unknown", "restricted"]
    allowed_uses: list[
        Literal["internal_analysis", "generation_input", "organic_publishing", "paid_advertising"]
    ] = Field(min_length=1, max_length=4)
    parent_asset_id: UUID | None = None
    source_url: str | None = Field(default=None, max_length=2048)


class AssetResponse(Contract):
    id: UUID
    tenant_id: UUID
    brand_id: UUID
    product_id: UUID | None
    kind: str
    role: str
    origin: str
    status: str
    original_filename: str
    declared_mime_type: str
    detected_mime_type: str | None
    rights_status: str
    allowed_uses: list[str]
    byte_size: int | None
    digest: str | None
    width: int | None
    height: int | None
    duration_ms: int | None
    rejection_code: str | None
    parent_asset_id: UUID | None
    source_url: str | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    can_edit: bool


class UploadGrantResponse(Contract):
    asset: AssetResponse
    url: str
    fields: dict[str, str]
    expires_at: datetime


class DownloadGrantResponse(Contract):
    url: str
    expires_at: datetime


def _audience(value: AudienceContract) -> Audience:
    return Audience(
        name=value.name,
        description=value.description,
        pain_points=tuple(value.pain_points),
        desires=tuple(value.desires),
        motivations=tuple(value.motivations),
        objections=tuple(value.objections),
    )


def _profile(tenant_id: UUID, product_id: UUID, value: ProductProfileContract) -> ProductProfile:
    return ProductProfile(
        tenant_id=tenant_id,
        product_id=product_id,
        description=value.description,
        features=tuple(value.features),
        benefits=tuple(value.benefits),
        materials=tuple(value.materials),
        variants=tuple(value.variants),
        price=value.price,
        currency=value.currency,
        estimated_margin=value.estimated_margin,
        target_audiences=tuple(_audience(item) for item in value.target_audiences),
        problems_solved=tuple(value.problems_solved),
        use_cases=tuple(value.use_cases),
        differentiators=tuple(value.differentiators),
        purchase_objections=tuple(value.purchase_objections),
        allowed_claims=tuple(value.allowed_claims),
        prohibited_claims=tuple(value.prohibited_claims),
        shipping_summary=value.shipping_summary,
        seasonality_notes=value.seasonality_notes,
        landing_page_url=value.landing_page_url,
        competitor_product_refs=tuple(value.competitor_product_refs),
    )


def _brief(tenant_id: UUID, product_id: UUID, value: BriefContract) -> ProductBrief:
    return ProductBrief(
        tenant_id=tenant_id,
        product_id=product_id,
        product_why=value.product_why,
        emotional_benefits=tuple(value.emotional_benefits),
        primary_audience=None
        if value.primary_audience is None
        else _audience(value.primary_audience),
        secondary_audiences=tuple(_audience(item) for item in value.secondary_audiences),
        positioning_statement=value.positioning_statement,
        competitive_alternatives=tuple(value.competitive_alternatives),
        why_choose_us=tuple(value.why_choose_us),
        current_channels=tuple(value.current_channels),
        priority_channels=tuple(value.priority_channels),
        conversion_goal=value.conversion_goal,
        offers=tuple(value.offers),
        cta_preferences=tuple(value.cta_preferences),
        desired_creative_style=value.desired_creative_style,
        tones_to_explore=tuple(value.tones_to_explore),
        tones_to_avoid=tuple(value.tones_to_avoid),
        creative_references=tuple(value.creative_references),
        mandatory_messaging=tuple(value.mandatory_messaging),
        prohibited_messaging=tuple(value.prohibited_messaging),
        required_disclaimers=tuple(value.required_disclaimers),
        legal_safety_constraints=tuple(value.legal_safety_constraints),
        geographical_restrictions=tuple(value.geographical_restrictions),
    )


def _editable(context: ExecutionContext) -> bool:
    return context.membership_role.value in {"owner", "admin"}


def _brand_response(brand: Brand, profile: BrandProfile, can_edit: bool) -> BrandResponse:
    return BrandResponse(
        id=brand.id,
        tenant_id=brand.tenant_id,
        name=brand.name,
        slug=brand.slug,
        website_url=brand.website_url,
        status=brand.status.value,
        created_by=brand.created_by,
        created_at=brand.created_at,
        updated_at=brand.updated_at,
        can_edit=can_edit,
        profile=BrandProfileResponse(
            industry=profile.industry,
            description=profile.description,
            brand_positioning=profile.brand_positioning,
            brand_voice=profile.brand_voice,
            tone_attributes=list(profile.tone_attributes),
            visual_style_keywords=list(profile.visual_style_keywords),
            target_markets=list(profile.target_markets),
            primary_language=profile.primary_language,
            allowed_claims=list(profile.allowed_claims),
            prohibited_claims=list(profile.prohibited_claims),
            competitors=list(profile.competitors),
            provenance=profile.provenance.value,
        ),
    )


def _product_response(product: Product, profile: ProductProfile, can_edit: bool) -> ProductResponse:
    value = profile.semantic()
    return ProductResponse(
        id=product.id,
        tenant_id=product.tenant_id,
        brand_id=product.brand_id,
        name=product.name,
        slug=product.slug,
        sku=product.sku,
        category=product.category,
        short_description=product.short_description,
        status=product.status.value,
        created_by=product.created_by,
        created_at=product.created_at,
        updated_at=product.updated_at,
        can_edit=can_edit,
        profile=ProductProfileResponse(
            **{key: value[key] for key in ProductProfileResponse.model_fields}
        ),
    )


def _brief_response(brief: ProductBrief, can_edit: bool) -> BriefResponse:
    return BriefResponse(
        **brief.semantic(),
        product_id=brief.product_id,
        revision=brief.revision,
        updated_at=brief.updated_at,
        can_edit=can_edit,
    )


def _snapshot_response(value: ProductKnowledgeSnapshot | None) -> SnapshotResponse | None:
    return (
        None
        if value is None
        else SnapshotResponse(
            id=value.id,
            product_id=value.product_id,
            schema_version=value.schema_version,
            source_revision=value.source_revision,
            digest=value.digest,
            created_at=value.created_at,
        )
    )


def _workspace_response(value: ProductWorkspace, can_edit: bool) -> WorkspaceResponse:
    return WorkspaceResponse(
        brand=_brand_response(value.brand, value.brand_profile, can_edit),
        product=_product_response(value.product, value.profile, can_edit),
        brief=_brief_response(value.brief, can_edit),
        completeness=CompletenessResponse(
            score=value.completeness.score,
            missing_sections=list(value.completeness.missing_sections),
            missing_fields=list(value.completeness.missing_fields),
        ),
        latest_snapshot=_snapshot_response(value.latest_snapshot),
    )


def _asset_response(value: Asset, can_edit: bool) -> AssetResponse:
    return AssetResponse(
        id=value.id,
        tenant_id=value.tenant_id,
        brand_id=value.brand_id,
        product_id=value.product_id,
        kind=value.kind.value,
        role=value.role.value,
        origin=value.origin.value,
        status=value.status.value,
        original_filename=value.original_filename,
        declared_mime_type=value.declared_mime_type,
        detected_mime_type=value.detected_mime_type,
        rights_status=value.rights_status.value,
        allowed_uses=[use.value for use in value.allowed_uses],
        byte_size=value.byte_size,
        digest=value.digest,
        width=value.width,
        height=value.height,
        duration_ms=value.duration_ms,
        rejection_code=value.rejection_code,
        parent_asset_id=value.parent_asset_id,
        source_url=value.source_url,
        created_by=value.created_by,
        created_at=value.created_at,
        updated_at=value.updated_at,
        can_edit=can_edit,
    )


def create_catalog_router(
    authenticator: AuthenticationPort,
    identity_uow: UnitOfWorkFactory,
    service: CatalogService,
    asset_service: AssetService,
    environment: str,
    audit: IdentityAuditService,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["catalog"])

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

    @router.get("/brands", response_model=list[BrandResponse])
    async def list_brands(ctx: Context) -> list[BrandResponse]:
        return [
            _brand_response(brand, (await service.get_brand(ctx, brand.id))[1], _editable(ctx))
            for brand in await service.list_brands(ctx)
        ]

    @router.post("/brands", response_model=BrandResponse, status_code=status.HTTP_201_CREATED)
    async def create_brand(value: BrandWrite, ctx: Context) -> BrandResponse:
        try:
            brand = Brand(
                tenant_id=ctx.tenant_id,
                name=value.name,
                slug=value.slug,
                website_url=value.website_url,
                status=BrandStatus(value.status),
                created_by=ctx.user_id,
            )
            profile = BrandProfile(
                tenant_id=ctx.tenant_id, brand_id=brand.id, **value.profile.model_dump()
            )
            await service.create_brand(ctx, brand, profile)
            return _brand_response(brand, profile, True)
        except (CatalogValidationError, CatalogConflict) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except CatalogPermissionDenied as error:
            raise HTTPException(status_code=403, detail="catalog_mutation_denied") from error

    @router.get("/brands/{brand_id}", response_model=BrandResponse)
    async def get_brand(brand_id: UUID, ctx: Context) -> BrandResponse:
        try:
            brand, profile = await service.get_brand(ctx, brand_id)
        except CatalogNotFound as error:
            raise HTTPException(status_code=404, detail="brand_not_found") from error
        return _brand_response(brand, profile, _editable(ctx))

    @router.patch("/brands/{brand_id}", response_model=BrandResponse)
    async def update_brand(brand_id: UUID, value: BrandWrite, ctx: Context) -> BrandResponse:
        try:
            current, _ = await service.get_brand(ctx, brand_id)
            brand = Brand(
                id=current.id,
                tenant_id=ctx.tenant_id,
                name=value.name,
                slug=value.slug,
                website_url=value.website_url,
                status=BrandStatus(value.status),
                created_by=current.created_by,
                created_at=current.created_at,
            )
            profile = BrandProfile(
                tenant_id=ctx.tenant_id, brand_id=brand.id, **value.profile.model_dump()
            )
            await service.update_brand(ctx, brand, profile)
            return _brand_response(brand, profile, True)
        except CatalogNotFound as error:
            raise HTTPException(status_code=404, detail="brand_not_found") from error
        except CatalogPermissionDenied as error:
            raise HTTPException(status_code=403, detail="catalog_mutation_denied") from error

    @router.get("/brands/{brand_id}/products", response_model=list[ProductResponse])
    async def list_brand_products(brand_id: UUID, ctx: Context) -> list[ProductResponse]:
        try:
            values = await service.list_products(ctx, brand_id)
        except CatalogNotFound as error:
            raise HTTPException(status_code=404, detail="brand_not_found") from error
        return [
            _product_response(
                value, (await service.get_workspace(ctx, value.id)).profile, _editable(ctx)
            )
            for value in values
        ]

    @router.post(
        "/brands/{brand_id}/products",
        response_model=WorkspaceResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_product(
        brand_id: UUID, value: ProductCreate, ctx: Context
    ) -> WorkspaceResponse:
        try:
            product = Product(
                tenant_id=ctx.tenant_id,
                brand_id=brand_id,
                name=value.name,
                slug=value.slug,
                sku=value.sku,
                category=value.category,
                short_description=value.short_description,
                status=ProductStatus(value.status),
                created_by=ctx.user_id,
            )
            await service.create_product(
                ctx,
                product,
                _profile(ctx.tenant_id, product.id, value.profile),
                _brief(ctx.tenant_id, product.id, value.brief),
            )
            return _workspace_response(await service.get_workspace(ctx, product.id), True)
        except CatalogNotFound as error:
            raise HTTPException(status_code=404, detail="brand_not_found") from error
        except CatalogPermissionDenied as error:
            raise HTTPException(status_code=403, detail="catalog_mutation_denied") from error
        except (CatalogValidationError, CatalogConflict) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get("/products/{product_id}", response_model=WorkspaceResponse)
    async def get_product(product_id: UUID, ctx: Context) -> WorkspaceResponse:
        try:
            return _workspace_response(await service.get_workspace(ctx, product_id), _editable(ctx))
        except CatalogNotFound as error:
            raise HTTPException(status_code=404, detail="product_not_found") from error

    @router.patch("/products/{product_id}", response_model=WorkspaceResponse)
    async def update_product(
        product_id: UUID, value: ProductWrite, ctx: Context
    ) -> WorkspaceResponse:
        try:
            workspace = await service.get_workspace(ctx, product_id)
            current = workspace.product
            product = Product(
                id=current.id,
                tenant_id=ctx.tenant_id,
                brand_id=current.brand_id,
                name=value.name,
                slug=value.slug,
                sku=value.sku,
                category=value.category,
                short_description=value.short_description,
                status=ProductStatus(value.status),
                created_by=current.created_by,
                created_at=current.created_at,
            )
            await service.update_product(
                ctx, product, _profile(ctx.tenant_id, product.id, value.profile)
            )
            return _workspace_response(await service.get_workspace(ctx, product_id), True)
        except CatalogNotFound as error:
            raise HTTPException(status_code=404, detail="product_not_found") from error
        except CatalogPermissionDenied as error:
            raise HTTPException(status_code=403, detail="catalog_mutation_denied") from error

    @router.get("/products/{product_id}/brief", response_model=BriefResponse)
    async def get_brief(product_id: UUID, ctx: Context) -> BriefResponse:
        try:
            workspace = await service.get_workspace(ctx, product_id)
        except CatalogNotFound as error:
            raise HTTPException(status_code=404, detail="product_not_found") from error
        return _brief_response(workspace.brief, _editable(ctx))

    @router.put("/products/{product_id}/brief", response_model=BriefResponse)
    async def put_brief(product_id: UUID, value: BriefContract, ctx: Context) -> BriefResponse:
        try:
            saved, _ = await service.save_brief(ctx, _brief(ctx.tenant_id, product_id, value))
        except CatalogNotFound as error:
            raise HTTPException(status_code=404, detail="product_not_found") from error
        except CatalogPermissionDenied as error:
            raise HTTPException(status_code=403, detail="catalog_mutation_denied") from error
        return _brief_response(saved, True)

    @router.get("/products/{product_id}/brief/completeness", response_model=CompletenessResponse)
    async def get_completeness(product_id: UUID, ctx: Context) -> CompletenessResponse:
        try:
            value = (await service.get_workspace(ctx, product_id)).completeness
        except CatalogNotFound as error:
            raise HTTPException(status_code=404, detail="product_not_found") from error
        return CompletenessResponse(
            score=value.score,
            missing_sections=list(value.missing_sections),
            missing_fields=list(value.missing_fields),
        )

    @router.post(
        "/products/{product_id}/snapshots",
        response_model=SnapshotResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_snapshot(product_id: UUID, ctx: Context) -> SnapshotResponse:
        try:
            return _snapshot_response(await service.create_snapshot(ctx, product_id))  # type: ignore[return-value]
        except CatalogNotFound as error:
            raise HTTPException(status_code=404, detail="product_not_found") from error
        except CatalogPermissionDenied as error:
            raise HTTPException(status_code=403, detail="catalog_mutation_denied") from error

    @router.get("/products/{product_id}/snapshots/latest", response_model=SnapshotResponse)
    async def latest_snapshot(product_id: UUID, ctx: Context) -> SnapshotResponse:
        try:
            value = (await service.get_workspace(ctx, product_id)).latest_snapshot
        except CatalogNotFound as error:
            raise HTTPException(status_code=404, detail="product_not_found") from error
        if value is None:
            raise HTTPException(status_code=404, detail="snapshot_not_found")
        return _snapshot_response(value)  # type: ignore[return-value]

    @router.get("/products/{product_id}/assets", response_model=list[AssetResponse])
    async def list_assets(
        product_id: UUID,
        ctx: Context,
        kind: AssetKind | None = None,
        asset_status: Annotated[AssetStatus | None, Query(alias="status")] = None,
    ) -> list[AssetResponse]:
        return [
            _asset_response(value, _editable(ctx))
            for value in await asset_service.list(
                ctx, product_id=product_id, kind=kind, status=asset_status
            )
        ]

    @router.get("/brands/{brand_id}/assets", response_model=list[AssetResponse])
    async def list_brand_assets(brand_id: UUID, ctx: Context) -> list[AssetResponse]:
        return [
            _asset_response(value, _editable(ctx))
            for value in await asset_service.list(ctx)
            if value.brand_id == brand_id
        ]

    @router.post("/assets", response_model=UploadGrantResponse, status_code=status.HTTP_201_CREATED)
    async def create_asset(value: AssetCreate, ctx: Context) -> UploadGrantResponse:
        try:
            asset_id = uuid4()
            created = await asset_service.create(
                ctx,
                Asset(
                    id=asset_id,
                    tenant_id=ctx.tenant_id,
                    brand_id=value.brand_id,
                    product_id=value.product_id,
                    kind=AssetKind(value.kind),
                    role=AssetRole(value.role),
                    original_filename=value.original_filename,
                    declared_mime_type=value.mime_type,
                    rights_status=RightsStatus(value.rights_status),
                    allowed_uses=tuple(AllowedUse(use) for use in value.allowed_uses),
                    created_by=ctx.user_id,
                    upload_object_key=(
                        f"tenants/{ctx.tenant_id}/assets/{asset_id}/uploads/{uuid4().hex}"
                    ),
                    parent_asset_id=value.parent_asset_id,
                    source_url=value.source_url,
                ),
            )
            return UploadGrantResponse(
                asset=_asset_response(created.asset, True),
                url=created.upload.url,
                fields=created.upload.fields,
                expires_at=created.upload.expires_at,
            )
        except CatalogNotFound as error:
            raise HTTPException(status_code=404, detail=str(error).replace(" ", "_")) from error
        except CatalogPermissionDenied as error:
            raise HTTPException(status_code=403, detail="catalog_mutation_denied") from error
        except CatalogValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except CatalogConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ObjectStoreUnavailable as error:
            raise HTTPException(status_code=503, detail="object_storage_unavailable") from error

    @router.get("/assets/{asset_id}", response_model=AssetResponse)
    async def get_asset(asset_id: UUID, ctx: Context) -> AssetResponse:
        try:
            return _asset_response(await asset_service.get(ctx, asset_id), _editable(ctx))
        except CatalogNotFound as error:
            raise HTTPException(status_code=404, detail="asset_not_found") from error

    @router.post("/assets/{asset_id}/finalize", response_model=AssetResponse)
    async def finalize_asset(asset_id: UUID, ctx: Context) -> AssetResponse:
        try:
            return _asset_response(await asset_service.finalize(ctx, asset_id), True)
        except CatalogNotFound as error:
            raise HTTPException(status_code=404, detail="asset_not_found") from error
        except CatalogPermissionDenied as error:
            raise HTTPException(status_code=403, detail="catalog_mutation_denied") from error
        except CatalogConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ObjectStoreUnavailable as error:
            raise HTTPException(status_code=503, detail="object_storage_unavailable") from error

    @router.post("/assets/{asset_id}/download", response_model=DownloadGrantResponse)
    async def download_asset(asset_id: UUID, ctx: Context) -> DownloadGrantResponse:
        try:
            grant = await asset_service.download(ctx, asset_id)
            return DownloadGrantResponse(url=grant.url, expires_at=grant.expires_at)
        except CatalogNotFound as error:
            raise HTTPException(status_code=404, detail="asset_not_found") from error
        except CatalogConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ObjectStoreUnavailable as error:
            raise HTTPException(status_code=503, detail="object_storage_unavailable") from error

    @router.post("/assets/{asset_id}/archive", response_model=AssetResponse)
    async def archive_asset(asset_id: UUID, ctx: Context) -> AssetResponse:
        try:
            return _asset_response(await asset_service.archive(ctx, asset_id), True)
        except CatalogNotFound as error:
            raise HTTPException(status_code=404, detail="asset_not_found") from error
        except CatalogPermissionDenied as error:
            raise HTTPException(status_code=403, detail="catalog_mutation_denied") from error
        except (CatalogValidationError, CatalogConflict) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return router
