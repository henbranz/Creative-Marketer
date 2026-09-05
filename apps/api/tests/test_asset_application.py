from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest

from creative_marketer.catalog.application import (
    CatalogConflict,
    CatalogNotFound,
    CatalogPermissionDenied,
)
from creative_marketer.catalog.asset_application import (
    AssetService,
    DownloadGrant,
    ObjectMetadata,
    ObjectStoreUnavailable,
    UploadGrant,
)
from creative_marketer.catalog.asset_domain import (
    AllowedUse,
    Asset,
    AssetKind,
    AssetRole,
    AssetStatus,
    RightsStatus,
)
from creative_marketer.catalog.domain import Brand, Product
from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus


class Items:
    def __init__(self, values: list[Any] | None = None) -> None:
        self.values = {value.id: value for value in values or []}

    async def add(self, value: Any) -> None:
        self.values[value.id] = value

    async def get(self, value_id: UUID) -> Any | None:
        return self.values.get(value_id)

    async def update(self, value: Asset, expected_status: AssetStatus) -> None:
        current = self.values.get(value.id)
        if current is None or current.status is not expected_status:
            raise CatalogConflict("asset state changed concurrently")
        self.values[value.id] = value

    async def list(
        self,
        *,
        product_id: UUID | None = None,
        kind: AssetKind | None = None,
        status: AssetStatus | None = None,
    ) -> tuple[Asset, ...]:
        return tuple(
            value
            for value in self.values.values()
            if (product_id is None or value.product_id == product_id)
            and (kind is None or value.kind is kind)
            and (status is None or value.status is status)
        )

    async def list_ready_for_product(self, product_id: UUID) -> tuple[Asset, ...]:
        return await self.list(product_id=product_id, status=AssetStatus.READY)


class Sink:
    def __init__(self) -> None:
        self.values: list[Any] = []

    async def append(self, value: Any) -> None:
        self.values.append(value)


class Uow:
    def __init__(self, brands: Items, products: Items, assets: Items) -> None:
        self.brands, self.products, self.assets = brands, products, assets
        self.audit, self.outbox = Sink(), Sink()
        self.commits = 0

    async def __aenter__(self) -> "Uow":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1


class Factory:
    def __init__(self, uow: Uow) -> None:
        self.uow = uow

    def __call__(self, _context: ExecutionContext) -> Any:
        return self.uow


@dataclass
class Store:
    content: bytes = b"\x89PNG\r\n\x1a\n" + b"0" * 24
    content_type: str | None = "image/png"
    available: bool = True
    promoted: list[tuple[str, str]] = field(default_factory=list)

    async def create_upload_grant(
        self, *, key: str, content_type: str, max_bytes: int
    ) -> UploadGrant:
        if not self.available:
            raise ObjectStoreUnavailable
        assert key and content_type == "image/png" and max_bytes == 25 * 1024 * 1024
        return UploadGrant(
            "https://store/upload",
            {"key": key},
            datetime.now(UTC) + timedelta(minutes=15),
        )

    async def create_download_grant(self, *, key: str) -> DownloadGrant:
        if not self.available:
            raise ObjectStoreUnavailable
        return DownloadGrant(f"https://store/{key}", datetime.now(UTC) + timedelta(minutes=10))

    async def head(self, *, key: str) -> ObjectMetadata:
        if not self.available:
            raise ObjectStoreUnavailable
        return ObjectMetadata(len(self.content), self.content_type)

    async def stream(self, *, key: str) -> AsyncIterator[bytes]:
        if not self.available:
            raise ObjectStoreUnavailable
        yield self.content[:9]
        yield self.content[9:]

    async def promote(self, *, source_key: str, destination_key: str) -> None:
        if not self.available:
            raise ObjectStoreUnavailable
        self.promoted.append((source_key, destination_key))


def setup(
    role: MembershipRole = MembershipRole.OWNER,
) -> tuple[ExecutionContext, Brand, Product, Uow]:
    tenant, user = uuid4(), uuid4()
    context = ExecutionContext(
        tenant,
        Actor(ActorKind.USER, user),
        user,
        role,
        MembershipStatus.ACTIVE,
        "test",
        AuthenticationAssurance(datetime.now(UTC), "test", "high"),
    )
    brand = Brand(tenant_id=tenant, name="Brand", slug="brand", created_by=user)
    product = Product(
        tenant_id=tenant,
        brand_id=brand.id,
        name="Product",
        slug="product",
        category="Test",
        created_by=user,
    )
    return context, brand, product, Uow(Items([brand]), Items([product]), Items())


def new_asset(context: ExecutionContext, brand: Brand, product: Product, **changes: Any) -> Asset:
    values: dict[str, Any] = {
        "tenant_id": context.tenant_id,
        "brand_id": brand.id,
        "product_id": product.id,
        "kind": AssetKind.IMAGE,
        "role": AssetRole.PRODUCT_HERO,
        "original_filename": "hero.png",
        "declared_mime_type": "image/png",
        "rights_status": RightsStatus.CONFIRMED,
        "allowed_uses": (AllowedUse.INTERNAL_ANALYSIS,),
        "created_by": context.user_id,
        "upload_object_key": "staging/key",
    }
    values.update(changes)
    return Asset(**values)


@pytest.mark.asyncio
async def test_asset_service_happy_path_is_audited_evented_idempotent_and_archivable() -> None:
    context, brand, product, uow = setup()
    store = Store()
    service = AssetService(Factory(uow), store)  # type: ignore[arg-type]
    created = await service.create(context, new_asset(context, brand, product))
    assert created.upload.url == "https://store/upload"
    ready = await service.finalize(context, created.asset.id)
    assert ready.status is AssetStatus.READY and ready.digest and ready.object_key
    assert await service.finalize(context, ready.id) == ready
    assert (await service.download(context, ready.id)).url.endswith(ready.object_key)
    assert await service.list(context, product_id=product.id) == (ready,)
    assert await service.get(context, ready.id) == ready
    archived = await service.archive(context, ready.id)
    assert archived.status is AssetStatus.ARCHIVED
    assert [event.event_type for event in uow.outbox.values] == [
        "catalog.asset.ready.v1",
        "catalog.asset.archived.v1",
    ]


@pytest.mark.asyncio
async def test_validation_rejects_spoofed_content_and_repeated_finalize_is_stable() -> None:
    context, brand, product, uow = setup()
    store = Store(content=b"<svg><script>alert(1)</script>", content_type="image/png")
    service = AssetService(Factory(uow), store)  # type: ignore[arg-type]
    created = await service.create(context, new_asset(context, brand, product))
    rejected = await service.finalize(context, created.asset.id)
    assert rejected.status is AssetStatus.REJECTED
    assert rejected.rejection_code == "mime_mismatch"
    assert await service.finalize(context, rejected.id) == rejected


@pytest.mark.asyncio
async def test_storage_outage_resets_pending_and_does_not_break_metadata_reads() -> None:
    context, brand, product, uow = setup()
    store = Store()
    service = AssetService(Factory(uow), store)  # type: ignore[arg-type]
    created = await service.create(context, new_asset(context, brand, product))
    store.available = False
    with pytest.raises(ObjectStoreUnavailable):
        await service.finalize(context, created.asset.id)
    assert (await service.get(context, created.asset.id)).status is AssetStatus.PENDING_UPLOAD
    store.available = True
    ready = await service.finalize(context, created.asset.id)
    store.available = False
    with pytest.raises(ObjectStoreUnavailable):
        await service.download(context, ready.id)


@pytest.mark.asyncio
async def test_association_permissions_and_state_conflicts_fail_closed() -> None:
    context, brand, product, uow = setup(MembershipRole.MEMBER)
    service = AssetService(Factory(uow), Store())  # type: ignore[arg-type]
    with pytest.raises(CatalogPermissionDenied):
        await service.create(context, new_asset(context, brand, product))
    owner = setup()[0]
    with pytest.raises(CatalogPermissionDenied):
        await service.create(owner, new_asset(context, brand, product))

    context, brand, product, uow = setup()
    service = AssetService(Factory(uow), Store())  # type: ignore[arg-type]
    with pytest.raises(CatalogNotFound, match="brand"):
        await service.create(context, new_asset(context, brand, product, brand_id=uuid4()))
    with pytest.raises(CatalogNotFound, match="product"):
        await service.create(context, new_asset(context, brand, product, product_id=uuid4()))
    with pytest.raises(CatalogNotFound, match="parent"):
        await service.create(context, new_asset(context, brand, product, parent_asset_id=uuid4()))
    with pytest.raises(CatalogNotFound):
        await service.get(context, uuid4())
    pending = new_asset(context, brand, product)
    await uow.assets.add(pending.validating())
    with pytest.raises(CatalogConflict):
        await service.finalize(context, pending.id)
    with pytest.raises(CatalogConflict):
        await service.download(context, pending.id)
    stale = replace(pending.validating(), updated_at=datetime.now(UTC) - timedelta(minutes=16))
    await uow.assets.add(stale)
    assert (await service.finalize(context, stale.id)).status is AssetStatus.READY
