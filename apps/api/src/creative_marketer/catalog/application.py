from dataclasses import dataclass, replace
from datetime import UTC, datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from creative_marketer.audit.application import AuditWriter
from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditOutcome
from creative_marketer.audit.safety import safe_metadata
from creative_marketer.catalog.domain import (
    Brand,
    BrandProfile,
    BrandStatus,
    BriefCompleteness,
    Product,
    ProductBrief,
    ProductKnowledgeSnapshot,
    ProductProfile,
    ProductStatus,
    evaluate_completeness,
)
from creative_marketer.events.application import OutboxWriter
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import event_sha256_v1, tenant_event
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.identity.domain import MembershipRole, MembershipStatus


class CatalogNotFound(Exception):
    pass


class CatalogConflict(Exception):
    pass


class CatalogPermissionDenied(Exception):
    pass


class BrandRepository(Protocol):
    async def add(self, value: Brand) -> None: ...
    async def get(self, value_id: UUID) -> Brand | None: ...
    async def list(self) -> tuple[Brand, ...]: ...
    async def update(self, value: Brand) -> None: ...


class BrandProfileRepository(Protocol):
    async def add(self, value: BrandProfile) -> None: ...
    async def get(self, brand_id: UUID) -> BrandProfile | None: ...
    async def update(self, value: BrandProfile) -> None: ...


class ProductRepository(Protocol):
    async def add(self, value: Product) -> None: ...
    async def get(self, value_id: UUID) -> Product | None: ...
    async def list_for_brand(self, brand_id: UUID) -> tuple[Product, ...]: ...
    async def list_all(self) -> tuple[Product, ...]: ...
    async def update(self, value: Product) -> None: ...


class ProductProfileRepository(Protocol):
    async def add(self, value: ProductProfile) -> None: ...
    async def get(self, product_id: UUID) -> ProductProfile | None: ...
    async def update(self, value: ProductProfile) -> None: ...


class ProductBriefRepository(Protocol):
    async def add(self, value: ProductBrief) -> None: ...
    async def get(self, product_id: UUID) -> ProductBrief | None: ...
    async def update(self, value: ProductBrief, expected_revision: int) -> None: ...


class SnapshotRepository(Protocol):
    async def add(self, value: ProductKnowledgeSnapshot) -> None: ...
    async def latest(self, product_id: UUID) -> ProductKnowledgeSnapshot | None: ...


class CatalogUnitOfWork(Protocol):
    brands: BrandRepository
    brand_profiles: BrandProfileRepository
    products: ProductRepository
    product_profiles: ProductProfileRepository
    product_briefs: ProductBriefRepository
    snapshots: SnapshotRepository
    audit: AuditWriter
    outbox: OutboxWriter

    async def __aenter__(self) -> "CatalogUnitOfWork": ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class CatalogUnitOfWorkFactory(Protocol):
    def __call__(self, context: ExecutionContext) -> CatalogUnitOfWork: ...


def require_catalog_mutation(context: ExecutionContext) -> None:
    if context.membership_status is not MembershipStatus.ACTIVE or context.membership_role not in {
        MembershipRole.OWNER,
        MembershipRole.ADMIN,
    }:
        raise CatalogPermissionDenied("catalog mutations require an active owner or admin")


def _digest(value: object) -> str:
    return event_sha256_v1(value)


async def _event(
    uow: CatalogUnitOfWork,
    context: ExecutionContext,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    payload: dict[str, object],
) -> None:
    contracts = EventContractRegistry()
    await uow.outbox.append(
        tenant_event(
            context,
            event_type=event_type,
            schema_version=1,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            payload_schema_digest=contracts.schema_digest(event_type),
            occurred_at=datetime.now(UTC),
        )
    )


@dataclass(frozen=True, slots=True)
class ProductWorkspace:
    brand: Brand
    brand_profile: BrandProfile
    product: Product
    profile: ProductProfile
    brief: ProductBrief
    completeness: BriefCompleteness
    latest_snapshot: ProductKnowledgeSnapshot | None


@dataclass(slots=True)
class CatalogService:
    uow_factory: CatalogUnitOfWorkFactory

    async def list_brands(self, context: ExecutionContext) -> tuple[Brand, ...]:
        async with self.uow_factory(context) as uow:
            return await uow.brands.list()

    async def create_brand(
        self, context: ExecutionContext, brand: Brand, profile: BrandProfile
    ) -> Brand:
        require_catalog_mutation(context)
        if (
            brand.tenant_id != context.tenant_id
            or profile.tenant_id != context.tenant_id
            or profile.brand_id != brand.id
            or brand.created_by != context.user_id
        ):
            raise CatalogPermissionDenied("catalog identity must come from execution context")
        async with self.uow_factory(context) as uow:
            await uow.brands.add(brand)
            await uow.brand_profiles.add(profile)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="catalog.brand.created",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="brand",
                    resource_id=str(brand.id),
                    after_digest=_digest(
                        {"name": brand.name, "slug": brand.slug, "status": brand.status.value}
                    ),
                    metadata=safe_metadata({"changed_fields": ["identity", "profile"]}),
                )
            )
            await _event(
                uow,
                context,
                "catalog.brand.created.v1",
                "brand",
                brand.id,
                {"brand_id": str(brand.id), "status": brand.status.value},
            )
            await uow.commit()
        return brand

    async def get_brand(
        self, context: ExecutionContext, brand_id: UUID
    ) -> tuple[Brand, BrandProfile]:
        async with self.uow_factory(context) as uow:
            brand, profile = await uow.brands.get(brand_id), await uow.brand_profiles.get(brand_id)
            if brand is None or profile is None:
                raise CatalogNotFound("brand not found")
            return brand, profile

    async def update_brand(
        self, context: ExecutionContext, brand: Brand, profile: BrandProfile
    ) -> Brand:
        require_catalog_mutation(context)
        async with self.uow_factory(context) as uow:
            old = await uow.brands.get(brand.id)
            if old is None:
                raise CatalogNotFound("brand not found")
            if (
                brand.tenant_id != context.tenant_id
                or profile.tenant_id != context.tenant_id
                or profile.brand_id != brand.id
            ):
                raise CatalogPermissionDenied("catalog identity must come from execution context")
            before = _digest({"name": old.name, "slug": old.slug, "status": old.status.value})
            await uow.brands.update(brand)
            await uow.brand_profiles.update(profile)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="catalog.brand.updated"
                    if brand.status is not BrandStatus.ARCHIVED
                    else "catalog.brand.archived",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="brand",
                    resource_id=str(brand.id),
                    before_digest=before,
                    after_digest=_digest(
                        {"name": brand.name, "slug": brand.slug, "status": brand.status.value}
                    ),
                    metadata=safe_metadata({"changed_fields": ["identity", "profile", "status"]}),
                )
            )
            await uow.commit()
        return brand

    async def list_products(
        self, context: ExecutionContext, brand_id: UUID | None = None
    ) -> tuple[Product, ...]:
        async with self.uow_factory(context) as uow:
            if brand_id is not None and await uow.brands.get(brand_id) is None:
                raise CatalogNotFound("brand not found")
            return (
                await uow.products.list_for_brand(brand_id)
                if brand_id
                else await uow.products.list_all()
            )

    async def create_product(
        self,
        context: ExecutionContext,
        product: Product,
        profile: ProductProfile,
        brief: ProductBrief,
    ) -> Product:
        require_catalog_mutation(context)
        if (
            any(
                value != context.tenant_id
                for value in (product.tenant_id, profile.tenant_id, brief.tenant_id)
            )
            or product.created_by != context.user_id
            or profile.product_id != product.id
            or brief.product_id != product.id
        ):
            raise CatalogPermissionDenied("catalog identity must come from execution context")
        async with self.uow_factory(context) as uow:
            if await uow.brands.get(product.brand_id) is None:
                raise CatalogNotFound("brand not found")
            await uow.products.add(product)
            await uow.product_profiles.add(profile)
            await uow.product_briefs.add(brief)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="catalog.product.created",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="product",
                    resource_id=str(product.id),
                    after_digest=_digest(
                        {
                            "name": product.name,
                            "slug": product.slug,
                            "category": product.category,
                            "status": product.status.value,
                        }
                    ),
                    metadata=safe_metadata(
                        {
                            "brand_id": str(product.brand_id),
                            "changed_fields": ["basics", "profile", "brief"],
                        }
                    ),
                )
            )
            await _event(
                uow,
                context,
                "catalog.product.created.v1",
                "product",
                product.id,
                {
                    "product_id": str(product.id),
                    "brand_id": str(product.brand_id),
                    "status": product.status.value,
                },
            )
            completeness = evaluate_completeness(profile, brief)
            if completeness.score == 100:
                await _event(
                    uow,
                    context,
                    "catalog.product.brief_completed.v1",
                    "product",
                    product.id,
                    {
                        "product_id": str(product.id),
                        "brief_revision": brief.revision,
                        "completeness_score": 100,
                    },
                )
            await uow.commit()
        return product

    async def get_workspace(self, context: ExecutionContext, product_id: UUID) -> ProductWorkspace:
        async with self.uow_factory(context) as uow:
            product = await uow.products.get(product_id)
            if product is None:
                raise CatalogNotFound("product not found")
            brand = await uow.brands.get(product.brand_id)
            brand_profile = await uow.brand_profiles.get(product.brand_id)
            profile = await uow.product_profiles.get(product_id)
            brief = await uow.product_briefs.get(product_id)
            if brand is None or brand_profile is None or profile is None or brief is None:
                raise CatalogNotFound("product workspace incomplete")
            return ProductWorkspace(
                brand,
                brand_profile,
                product,
                profile,
                brief,
                evaluate_completeness(profile, brief),
                await uow.snapshots.latest(product_id),
            )

    async def update_product(
        self, context: ExecutionContext, product: Product, profile: ProductProfile
    ) -> Product:
        require_catalog_mutation(context)
        async with self.uow_factory(context) as uow:
            old = await uow.products.get(product.id)
            old_profile = await uow.product_profiles.get(product.id)
            brief = await uow.product_briefs.get(product.id)
            if old is None:
                raise CatalogNotFound("product not found")
            if (
                product.tenant_id != context.tenant_id
                or profile.tenant_id != context.tenant_id
                or profile.product_id != product.id
                or product.brand_id != old.brand_id
            ):
                raise CatalogPermissionDenied("catalog identity or brand cannot be changed")
            before = _digest(
                {"name": old.name, "category": old.category, "status": old.status.value}
            )
            await uow.products.update(product)
            await uow.product_profiles.update(profile)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="catalog.product.archived"
                    if product.status is ProductStatus.ARCHIVED
                    else "catalog.product.updated",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="product",
                    resource_id=str(product.id),
                    before_digest=before,
                    after_digest=_digest(
                        {
                            "name": product.name,
                            "category": product.category,
                            "status": product.status.value,
                        }
                    ),
                    metadata=safe_metadata({"changed_fields": ["basics", "profile", "status"]}),
                )
            )
            await _event(
                uow,
                context,
                "catalog.product.updated.v1",
                "product",
                product.id,
                {
                    "product_id": str(product.id),
                    "brand_id": str(product.brand_id),
                    "status": product.status.value,
                },
            )
            if (
                old_profile is not None
                and brief is not None
                and evaluate_completeness(old_profile, brief).score < 100
                and evaluate_completeness(profile, brief).score == 100
            ):
                await _event(
                    uow,
                    context,
                    "catalog.product.brief_completed.v1",
                    "product",
                    product.id,
                    {
                        "product_id": str(product.id),
                        "brief_revision": brief.revision,
                        "completeness_score": 100,
                    },
                )
            await uow.commit()
        return product

    async def save_brief(
        self, context: ExecutionContext, brief: ProductBrief
    ) -> tuple[ProductBrief, BriefCompleteness]:
        require_catalog_mutation(context)
        async with self.uow_factory(context) as uow:
            current = await uow.product_briefs.get(brief.product_id)
            profile = await uow.product_profiles.get(brief.product_id)
            if current is None or profile is None:
                raise CatalogNotFound("product not found")
            if brief.tenant_id != context.tenant_id:
                raise CatalogPermissionDenied("catalog identity must come from execution context")
            saved = replace(brief, revision=current.revision + 1, updated_at=datetime.now(UTC))
            before_score = evaluate_completeness(profile, current).score
            completeness = evaluate_completeness(profile, saved)
            await uow.product_briefs.update(saved, current.revision)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="catalog.product.brief.updated",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="product",
                    resource_id=str(saved.product_id),
                    before_digest=_digest(current.semantic()),
                    after_digest=_digest(saved.semantic()),
                    metadata=safe_metadata(
                        {
                            "changed_fields": ["brief"],
                            "revision": saved.revision,
                            "completeness": completeness.score,
                        }
                    ),
                )
            )
            if before_score < 100 and completeness.score == 100:
                await _event(
                    uow,
                    context,
                    "catalog.product.brief_completed.v1",
                    "product",
                    saved.product_id,
                    {
                        "product_id": str(saved.product_id),
                        "brief_revision": saved.revision,
                        "completeness_score": 100,
                    },
                )
            await uow.commit()
            return saved, completeness

    async def create_snapshot(
        self, context: ExecutionContext, product_id: UUID
    ) -> ProductKnowledgeSnapshot:
        require_catalog_mutation(context)
        async with self.uow_factory(context) as uow:
            product = await uow.products.get(product_id)
            if product is None:
                raise CatalogNotFound("product not found")
            brand, brand_profile = (
                await uow.brands.get(product.brand_id),
                await uow.brand_profiles.get(product.brand_id),
            )
            profile, brief = (
                await uow.product_profiles.get(product_id),
                await uow.product_briefs.get(product_id),
            )
            if brand is None or brand_profile is None or profile is None or brief is None:
                raise CatalogNotFound("product workspace incomplete")
            snapshot = ProductKnowledgeSnapshot.create(
                brand=brand,
                brand_profile=brand_profile,
                product=product,
                profile=profile,
                brief=brief,
                created_by=context.user_id,
            )
            await uow.snapshots.add(snapshot)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="catalog.product.snapshot.created",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="product_snapshot",
                    resource_id=str(snapshot.id),
                    after_digest=snapshot.digest,
                    metadata=safe_metadata(
                        {
                            "product_id": str(product_id),
                            "schema_version": snapshot.schema_version,
                            "source_revision": snapshot.source_revision,
                        }
                    ),
                )
            )
            await _event(
                uow,
                context,
                "catalog.product.snapshot_created.v1",
                "product",
                product_id,
                {
                    "product_id": str(product_id),
                    "snapshot_id": str(snapshot.id),
                    "snapshot_digest": snapshot.digest,
                    "schema_version": snapshot.schema_version,
                    "source_revision": snapshot.source_revision,
                },
            )
            await uow.commit()
            return snapshot
