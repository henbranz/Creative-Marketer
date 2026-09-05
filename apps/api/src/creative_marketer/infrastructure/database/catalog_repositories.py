import json
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.catalog.application import CatalogConflict
from creative_marketer.catalog.asset_domain import (
    AllowedUse,
    Asset,
    AssetKind,
    AssetOrigin,
    AssetRole,
    AssetStatus,
    RightsStatus,
)
from creative_marketer.catalog.domain import (
    Audience,
    Brand,
    BrandProfile,
    BrandStatus,
    KnowledgeProvenance,
    Product,
    ProductBrief,
    ProductKnowledgeSnapshot,
    ProductProfile,
    ProductStatus,
)
from creative_marketer.events.domain import canonical_event_json_v1
from creative_marketer.infrastructure.database.catalog_schema import (
    assets,
    brand_profiles,
    brands,
    product_briefs,
    product_knowledge_snapshots,
    product_profiles,
    products,
)


def _data(row: object) -> Any:
    return row._mapping  # type: ignore[attr-defined]


def _audience(value: dict[str, object]) -> Audience:
    return Audience(
        name=cast(str, value["name"]),
        description=cast(str, value.get("description", "")),
        pain_points=tuple(cast(list[str], value.get("pain_points", []))),
        desires=tuple(cast(list[str], value.get("desires", []))),
        motivations=tuple(cast(list[str], value.get("motivations", []))),
        objections=tuple(cast(list[str], value.get("objections", []))),
    )


def _brand(row: object) -> Brand:
    d = _data(row)
    return Brand(
        id=d["id"],
        tenant_id=d["tenant_id"],
        name=d["name"],
        slug=d["slug"],
        website_url=d["website_url"],
        status=BrandStatus(d["status"]),
        created_by=d["created_by"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


def _brand_profile(row: object) -> BrandProfile:
    d = _data(row)
    return BrandProfile(
        tenant_id=d["tenant_id"],
        brand_id=d["brand_id"],
        industry=d["industry"],
        description=d["description"],
        brand_positioning=d["brand_positioning"],
        brand_voice=d["brand_voice"],
        tone_attributes=tuple(d["tone_attributes"]),
        visual_style_keywords=tuple(d["visual_style_keywords"]),
        target_markets=tuple(d["target_markets"]),
        primary_language=d["primary_language"],
        allowed_claims=tuple(d["allowed_claims"]),
        prohibited_claims=tuple(d["prohibited_claims"]),
        competitors=tuple(d["competitors"]),
        provenance=KnowledgeProvenance(d["provenance"]),
        updated_at=d["updated_at"],
    )


def _product(row: object) -> Product:
    d = _data(row)
    return Product(
        id=d["id"],
        tenant_id=d["tenant_id"],
        brand_id=d["brand_id"],
        name=d["name"],
        slug=d["slug"],
        sku=d["sku"],
        category=d["category"],
        short_description=d["short_description"],
        status=ProductStatus(d["status"]),
        created_by=d["created_by"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


def _profile_values(value: ProductProfile) -> dict[str, object]:
    semantic = value.semantic()
    return {
        "tenant_id": value.tenant_id,
        "product_id": value.product_id,
        "description": value.description,
        "features": list(value.features),
        "benefits": list(value.benefits),
        "materials": list(value.materials),
        "variants": list(value.variants),
        "price": value.price,
        "currency": value.currency,
        "estimated_margin": value.estimated_margin,
        "target_audiences": semantic["target_audiences"],
        "problems_solved": list(value.problems_solved),
        "use_cases": list(value.use_cases),
        "differentiators": list(value.differentiators),
        "purchase_objections": list(value.purchase_objections),
        "allowed_claims": list(value.allowed_claims),
        "prohibited_claims": list(value.prohibited_claims),
        "shipping_summary": value.shipping_summary,
        "seasonality_notes": value.seasonality_notes,
        "landing_page_url": value.landing_page_url,
        "competitor_product_refs": list(value.competitor_product_refs),
        "provenance": value.provenance.value,
        "updated_at": value.updated_at,
    }


def _profile(row: object) -> ProductProfile:
    d = _data(row)
    return ProductProfile(
        tenant_id=d["tenant_id"],
        product_id=d["product_id"],
        description=d["description"],
        features=tuple(d["features"]),
        benefits=tuple(d["benefits"]),
        materials=tuple(d["materials"]),
        variants=tuple(d["variants"]),
        price=d["price"],
        currency=d["currency"],
        estimated_margin=d["estimated_margin"],
        target_audiences=tuple(_audience(v) for v in d["target_audiences"]),
        problems_solved=tuple(d["problems_solved"]),
        use_cases=tuple(d["use_cases"]),
        differentiators=tuple(d["differentiators"]),
        purchase_objections=tuple(d["purchase_objections"]),
        allowed_claims=tuple(d["allowed_claims"]),
        prohibited_claims=tuple(d["prohibited_claims"]),
        shipping_summary=d["shipping_summary"],
        seasonality_notes=d["seasonality_notes"],
        landing_page_url=d["landing_page_url"],
        competitor_product_refs=tuple(d["competitor_product_refs"]),
        provenance=KnowledgeProvenance(d["provenance"]),
        updated_at=d["updated_at"],
    )


def _brief(row: object) -> ProductBrief:
    d, value = _data(row), _data(row)["content"]
    primary = value.get("primary_audience")
    return ProductBrief(
        tenant_id=d["tenant_id"],
        product_id=d["product_id"],
        product_why=value["product_why"],
        emotional_benefits=tuple(value["emotional_benefits"]),
        primary_audience=None if primary is None else _audience(primary),
        secondary_audiences=tuple(_audience(v) for v in value["secondary_audiences"]),
        positioning_statement=value["positioning_statement"],
        competitive_alternatives=tuple(value["competitive_alternatives"]),
        why_choose_us=tuple(value["why_choose_us"]),
        current_channels=tuple(value["current_channels"]),
        priority_channels=tuple(value["priority_channels"]),
        conversion_goal=value["conversion_goal"],
        offers=tuple(value["offers"]),
        cta_preferences=tuple(value["cta_preferences"]),
        desired_creative_style=value["desired_creative_style"],
        tones_to_explore=tuple(value["tones_to_explore"]),
        tones_to_avoid=tuple(value["tones_to_avoid"]),
        creative_references=tuple(value["creative_references"]),
        mandatory_messaging=tuple(value["mandatory_messaging"]),
        prohibited_messaging=tuple(value["prohibited_messaging"]),
        required_disclaimers=tuple(value["required_disclaimers"]),
        legal_safety_constraints=tuple(value["legal_safety_constraints"]),
        geographical_restrictions=tuple(value["geographical_restrictions"]),
        provenance=KnowledgeProvenance(d["provenance"]),
        revision=d["revision"],
        updated_at=d["updated_at"],
    )


class SqlAlchemyBrandRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, value: Brand) -> None:
        try:
            await self.session.execute(
                insert(brands).values(
                    id=value.id,
                    tenant_id=value.tenant_id,
                    name=value.name,
                    slug=value.slug,
                    website_url=value.website_url,
                    status=value.status.value,
                    created_by=value.created_by,
                    created_at=value.created_at,
                    updated_at=value.updated_at,
                )
            )
        except IntegrityError as error:
            raise CatalogConflict("brand already exists") from error

    async def get(self, value_id: UUID) -> Brand | None:
        row = (await self.session.execute(select(brands).where(brands.c.id == value_id))).first()
        return None if row is None else _brand(row)

    async def list(self) -> tuple[Brand, ...]:
        rows = (
            await self.session.execute(select(brands).order_by(brands.c.name, brands.c.id))
        ).all()
        return tuple(_brand(row) for row in rows)

    async def update(self, value: Brand) -> None:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(brands)
                .where(brands.c.id == value.id)
                .values(
                    name=value.name,
                    slug=value.slug,
                    website_url=value.website_url,
                    status=value.status.value,
                    updated_at=value.updated_at,
                )
            ),
        )
        if result.rowcount != 1:
            raise CatalogNotFoundError


class CatalogNotFoundError(Exception):
    pass


class SqlAlchemyBrandProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, v: BrandProfile) -> None:
        await self.session.execute(
            insert(brand_profiles).values(
                tenant_id=v.tenant_id,
                brand_id=v.brand_id,
                industry=v.industry,
                description=v.description,
                brand_positioning=v.brand_positioning,
                brand_voice=v.brand_voice,
                tone_attributes=list(v.tone_attributes),
                visual_style_keywords=list(v.visual_style_keywords),
                target_markets=list(v.target_markets),
                primary_language=v.primary_language,
                allowed_claims=list(v.allowed_claims),
                prohibited_claims=list(v.prohibited_claims),
                competitors=list(v.competitors),
                provenance=v.provenance.value,
                created_at=v.updated_at,
                updated_at=v.updated_at,
            )
        )

    async def get(self, brand_id: UUID) -> BrandProfile | None:
        row = (
            await self.session.execute(
                select(brand_profiles).where(brand_profiles.c.brand_id == brand_id)
            )
        ).first()
        return None if row is None else _brand_profile(row)

    async def update(self, v: BrandProfile) -> None:
        await self.session.execute(
            update(brand_profiles)
            .where(brand_profiles.c.brand_id == v.brand_id)
            .values(
                industry=v.industry,
                description=v.description,
                brand_positioning=v.brand_positioning,
                brand_voice=v.brand_voice,
                tone_attributes=list(v.tone_attributes),
                visual_style_keywords=list(v.visual_style_keywords),
                target_markets=list(v.target_markets),
                primary_language=v.primary_language,
                allowed_claims=list(v.allowed_claims),
                prohibited_claims=list(v.prohibited_claims),
                competitors=list(v.competitors),
                provenance=v.provenance.value,
                updated_at=v.updated_at,
            )
        )


class SqlAlchemyProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, v: Product) -> None:
        try:
            await self.session.execute(
                insert(products).values(
                    id=v.id,
                    tenant_id=v.tenant_id,
                    brand_id=v.brand_id,
                    name=v.name,
                    slug=v.slug,
                    sku=v.sku,
                    category=v.category,
                    short_description=v.short_description,
                    status=v.status.value,
                    created_by=v.created_by,
                    created_at=v.created_at,
                    updated_at=v.updated_at,
                )
            )
        except IntegrityError as error:
            raise CatalogConflict("product already exists") from error

    async def get(self, value_id: UUID) -> Product | None:
        row = (
            await self.session.execute(select(products).where(products.c.id == value_id))
        ).first()
        return None if row is None else _product(row)

    async def list_for_brand(self, brand_id: UUID) -> tuple[Product, ...]:
        rows = (
            await self.session.execute(
                select(products)
                .where(products.c.brand_id == brand_id)
                .order_by(products.c.name, products.c.id)
            )
        ).all()
        return tuple(_product(r) for r in rows)

    async def list_all(self) -> tuple[Product, ...]:
        rows = (
            await self.session.execute(select(products).order_by(products.c.name, products.c.id))
        ).all()
        return tuple(_product(r) for r in rows)

    async def update(self, v: Product) -> None:
        await self.session.execute(
            update(products)
            .where(products.c.id == v.id)
            .values(
                name=v.name,
                slug=v.slug,
                sku=v.sku,
                category=v.category,
                short_description=v.short_description,
                status=v.status.value,
                updated_at=v.updated_at,
            )
        )


class SqlAlchemyProductProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, v: ProductProfile) -> None:
        await self.session.execute(
            insert(product_profiles).values(**_profile_values(v), created_at=v.updated_at)
        )

    async def get(self, product_id: UUID) -> ProductProfile | None:
        row = (
            await self.session.execute(
                select(product_profiles).where(product_profiles.c.product_id == product_id)
            )
        ).first()
        return None if row is None else _profile(row)

    async def update(self, v: ProductProfile) -> None:
        await self.session.execute(
            update(product_profiles)
            .where(product_profiles.c.product_id == v.product_id)
            .values(**_profile_values(v))
        )


class SqlAlchemyProductBriefRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, v: ProductBrief) -> None:
        await self.session.execute(
            insert(product_briefs).values(
                tenant_id=v.tenant_id,
                product_id=v.product_id,
                content=v.semantic(),
                provenance=v.provenance.value,
                revision=v.revision,
                created_at=v.updated_at,
                updated_at=v.updated_at,
            )
        )

    async def get(self, product_id: UUID) -> ProductBrief | None:
        row = (
            await self.session.execute(
                select(product_briefs).where(product_briefs.c.product_id == product_id)
            )
        ).first()
        return None if row is None else _brief(row)

    async def update(self, v: ProductBrief, expected_revision: int) -> None:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(product_briefs)
                .where(
                    product_briefs.c.product_id == v.product_id,
                    product_briefs.c.revision == expected_revision,
                )
                .values(
                    content=v.semantic(),
                    provenance=v.provenance.value,
                    revision=v.revision,
                    updated_at=v.updated_at,
                )
            ),
        )
        if result.rowcount != 1:
            raise CatalogConflict("brief was changed by another request")


class SqlAlchemySnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, v: ProductKnowledgeSnapshot) -> None:
        await self.session.execute(
            insert(product_knowledge_snapshots).values(
                id=v.id,
                tenant_id=v.tenant_id,
                product_id=v.product_id,
                schema_version=v.schema_version,
                source_revision=v.source_revision,
                content=json.loads(canonical_event_json_v1(v.content)),
                digest=v.digest,
                created_by=v.created_by,
                created_at=v.created_at,
            )
        )

    async def latest(self, product_id: UUID) -> ProductKnowledgeSnapshot | None:
        row = (
            await self.session.execute(
                select(product_knowledge_snapshots)
                .where(product_knowledge_snapshots.c.product_id == product_id)
                .order_by(
                    product_knowledge_snapshots.c.created_at.desc(),
                    product_knowledge_snapshots.c.id.desc(),
                )
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        d = _data(row)
        return ProductKnowledgeSnapshot(
            id=d["id"],
            tenant_id=d["tenant_id"],
            product_id=d["product_id"],
            schema_version=d["schema_version"],
            source_revision=d["source_revision"],
            content=d["content"],
            digest=d["digest"],
            created_by=d["created_by"],
            created_at=cast(datetime, d["created_at"]),
        )


def _asset(row: object) -> Asset:
    d = _data(row)
    return Asset(
        id=d["id"],
        tenant_id=d["tenant_id"],
        brand_id=d["brand_id"],
        product_id=d["product_id"],
        kind=AssetKind(d["kind"]),
        role=AssetRole(d["role"]),
        origin=AssetOrigin(d["origin"]),
        status=AssetStatus(d["status"]),
        original_filename=d["original_filename"],
        declared_mime_type=d["declared_mime_type"],
        detected_mime_type=d["detected_mime_type"],
        rights_status=RightsStatus(d["rights_status"]),
        allowed_uses=tuple(AllowedUse(value) for value in d["allowed_uses"]),
        upload_object_key=d["upload_object_key"],
        object_key=d["object_key"],
        byte_size=d["byte_size"],
        digest=d["digest"],
        width=d["width"],
        height=d["height"],
        duration_ms=d["duration_ms"],
        rejection_code=d["rejection_code"],
        parent_asset_id=d["parent_asset_id"],
        source_url=d["source_url"],
        created_by=d["created_by"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


def _asset_values(value: Asset) -> dict[str, object]:
    return {
        "id": value.id,
        "tenant_id": value.tenant_id,
        "brand_id": value.brand_id,
        "product_id": value.product_id,
        "kind": value.kind.value,
        "role": value.role.value,
        "origin": value.origin.value,
        "status": value.status.value,
        "original_filename": value.original_filename,
        "declared_mime_type": value.declared_mime_type,
        "detected_mime_type": value.detected_mime_type,
        "rights_status": value.rights_status.value,
        "allowed_uses": [use.value for use in value.allowed_uses],
        "upload_object_key": value.upload_object_key,
        "object_key": value.object_key,
        "byte_size": value.byte_size,
        "digest": value.digest,
        "width": value.width,
        "height": value.height,
        "duration_ms": value.duration_ms,
        "rejection_code": value.rejection_code,
        "parent_asset_id": value.parent_asset_id,
        "source_url": value.source_url,
        "created_by": value.created_by,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
    }


class SqlAlchemyAssetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, value: Asset) -> None:
        try:
            await self.session.execute(insert(assets).values(**_asset_values(value)))
        except IntegrityError as error:
            raise CatalogConflict("asset already exists or association is invalid") from error

    async def get(self, value_id: UUID) -> Asset | None:
        row = (await self.session.execute(select(assets).where(assets.c.id == value_id))).first()
        return None if row is None else _asset(row)

    async def list(
        self,
        *,
        product_id: UUID | None = None,
        kind: AssetKind | None = None,
        status: AssetStatus | None = None,
    ) -> tuple[Asset, ...]:
        query = select(assets)
        if product_id is not None:
            query = query.where(assets.c.product_id == product_id)
        if kind is not None:
            query = query.where(assets.c.kind == kind.value)
        if status is not None:
            query = query.where(assets.c.status == status.value)
        rows = (await self.session.execute(query.order_by(assets.c.created_at.desc()))).all()
        return tuple(_asset(row) for row in rows)

    async def list_ready_for_product(self, product_id: UUID) -> tuple[Asset, ...]:
        return await self.list(product_id=product_id, status=AssetStatus.READY)

    async def update(self, value: Asset, expected_status: AssetStatus) -> None:
        values = _asset_values(value)
        values.pop("id")
        values.pop("tenant_id")
        values.pop("created_at")
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(assets)
                .where(assets.c.id == value.id, assets.c.status == expected_status.value)
                .values(**values)
            ),
        )
        if result.rowcount != 1:
            raise CatalogConflict("asset state changed concurrently")
