import os
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from creative_marketer.catalog.application import CatalogNotFound, CatalogService
from creative_marketer.catalog.asset_application import AssetService
from creative_marketer.catalog.asset_domain import (
    AllowedUse,
    Asset,
    AssetKind,
    AssetRole,
    AssetStatus,
    RightsStatus,
)
from creative_marketer.catalog.domain import (
    Brand,
    BrandProfile,
    Product,
    ProductBrief,
    ProductProfile,
)
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.infrastructure.database.catalog_uow import SqlAlchemyCatalogUnitOfWorkFactory
from creative_marketer.infrastructure.object_storage.s3 import S3ObjectStore
from creative_marketer_api.config import Settings
from creative_marketer_api.main import create_app
from tests.integration.test_catalog import headers, owner_context, seed_catalog_identity


def storage() -> S3ObjectStore:
    endpoint = os.environ.get("TEST_OBJECT_STORAGE_URL")
    if endpoint is None:
        pytest.skip("TEST_OBJECT_STORAGE_URL is required for object storage tests")
    return S3ObjectStore(
        endpoint_url=endpoint,
        public_endpoint_url=endpoint,
        region="us-east-1",
        bucket="creative-marketer-assets",
        access_key_id="creative-marketer-test",
        secret_access_key="creative-marketer-test-secret",
    )


async def product_setup(
    admin: AsyncEngine, factory: SqlAlchemyCatalogUnitOfWorkFactory
) -> tuple[ExecutionContext, Brand, Product]:
    tenant, user = await seed_catalog_identity(admin)
    context = owner_context(tenant, user)
    brand = Brand(tenant_id=tenant, name="Asset Brand", slug=f"asset-{tenant}", created_by=user)
    product = Product(
        tenant_id=tenant,
        brand_id=brand.id,
        name="Asset Product",
        slug="asset-product",
        category="Test",
        created_by=user,
    )
    catalog = CatalogService(factory)
    await catalog.create_brand(context, brand, BrandProfile(tenant_id=tenant, brand_id=brand.id))
    await catalog.create_product(
        context,
        product,
        ProductProfile(tenant_id=tenant, product_id=product.id),
        ProductBrief(tenant_id=tenant, product_id=product.id),
    )
    return context, brand, product


def asset(
    context: ExecutionContext, brand: Brand, product: Product, filename: str = "pixel.png"
) -> Asset:
    tenant_id = context.tenant_id
    user_id = context.user_id
    asset_id = uuid4()
    return Asset(
        id=asset_id,
        tenant_id=tenant_id,
        brand_id=brand.id,
        product_id=product.id,
        kind=AssetKind.IMAGE,
        role=AssetRole.PRODUCT_DETAIL,
        original_filename=filename,
        declared_mime_type="image/png",
        rights_status=RightsStatus.CONFIRMED,
        allowed_uses=(AllowedUse.INTERNAL_ANALYSIS, AllowedUse.GENERATION_INPUT),
        created_by=user_id,
        upload_object_key=f"tenants/{tenant_id}/assets/{asset_id}/uploads/{uuid4().hex}",
    )


async def direct_upload(
    url: str, fields: dict[str, str], content: bytes, mime: str
) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.post(url, data=fields, files={"file": ("upload", content, mime)})


@pytest.mark.postgres
@pytest.mark.object_storage
@pytest.mark.asyncio
async def test_real_private_storage_ready_lifecycle_and_snapshot_v2(
    admin_engine: AsyncEngine, catalog_factory: SqlAlchemyCatalogUnitOfWorkFactory
) -> None:
    store = storage()
    await store.ensure_private_bucket(["http://localhost:3000"])
    context, brand, product = await product_setup(admin_engine, catalog_factory)
    service = AssetService(catalog_factory, store)
    created = await service.create(context, asset(context, brand, product))
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 8 + (2).to_bytes(4, "big") + (3).to_bytes(4, "big")
    response = await direct_upload(created.upload.url, created.upload.fields, png, "image/png")
    assert response.status_code in {200, 201, 204}
    ready = await service.finalize(context, created.asset.id)
    assert ready.status is AssetStatus.READY and ready.width == 2 and ready.height == 3
    assert await service.finalize(context, ready.id) == ready
    download = await service.download(context, ready.id)
    async with httpx.AsyncClient() as client:
        assert (await client.get(download.url)).content == png
        private_url = (
            f"{os.environ['TEST_OBJECT_STORAGE_URL']}/creative-marketer-assets/{ready.object_key}"
        )
        assert (await client.get(private_url)).status_code == 403

    snapshot = await CatalogService(catalog_factory).create_snapshot(context, product.id)
    assert snapshot.schema_version == 2
    assert snapshot.content["assets"][0]["digest"] == ready.digest  # type: ignore[index]
    async with admin_engine.connect() as connection:
        event_types = set(
            await connection.scalars(
                text("SELECT event_type FROM event_delivery.outbox_events WHERE tenant_id=:tenant"),
                {"tenant": ready.tenant_id},
            )
        )
        actions = set(
            await connection.scalars(
                text("SELECT action FROM audit.audit_records WHERE tenant_id=:tenant"),
                {"tenant": ready.tenant_id},
            )
        )
    assert "catalog.asset.ready.v1" in event_types
    assert "catalog.product.snapshot_created.v2" in event_types
    assert "catalog.asset.ready" in actions

    with pytest.raises(DBAPIError):
        async with admin_engine.begin() as connection:
            await connection.execute(
                text("UPDATE catalog.assets SET digest=:digest WHERE id=:id"),
                {"digest": "sha256:" + "0" * 64, "id": ready.id},
            )
    with pytest.raises(DBAPIError):
        async with admin_engine.begin() as connection:
            await connection.execute(
                text("UPDATE catalog.assets SET status='pending_upload' WHERE id=:id"),
                {"id": ready.id},
            )

    other_tenant, other_user = await seed_catalog_identity(admin_engine)
    with pytest.raises(CatalogNotFound):
        await service.get(owner_context(other_tenant, other_user), ready.id)


@pytest.mark.postgres
@pytest.mark.object_storage
@pytest.mark.asyncio
async def test_real_storage_rejects_spoofing_and_grant_tampering(
    admin_engine: AsyncEngine, catalog_factory: SqlAlchemyCatalogUnitOfWorkFactory
) -> None:
    store = storage()
    await store.ensure_private_bucket(["http://localhost:3000"])
    context, brand, product = await product_setup(admin_engine, catalog_factory)
    service = AssetService(catalog_factory, store)
    created = await service.create(context, asset(context, brand, product, "attack.png"))
    tampered = dict(created.upload.fields)
    signature_key = next(key for key in tampered if "signature" in key.lower())
    tampered[signature_key] = "0" * len(tampered[signature_key])
    invalid = await direct_upload(created.upload.url, tampered, b"bad", "image/png")
    assert invalid.status_code == 403
    bounded = await store.create_upload_grant(
        key=f"policy-tests/{uuid4()}", content_type="image/png", max_bytes=8
    )
    too_large = await direct_upload(bounded.url, bounded.fields, b"123456789", "image/png")
    assert too_large.status_code == 400
    expired_store = S3ObjectStore(
        endpoint_url=os.environ["TEST_OBJECT_STORAGE_URL"],
        public_endpoint_url=os.environ["TEST_OBJECT_STORAGE_URL"],
        region="us-east-1",
        bucket="creative-marketer-assets",
        access_key_id="creative-marketer-test",
        secret_access_key="creative-marketer-test-secret",
        upload_ttl_seconds=-1,
    )
    expired = await expired_store.create_upload_grant(
        key=f"policy-tests/{uuid4()}", content_type="image/png", max_bytes=100
    )
    expired_response = await direct_upload(expired.url, expired.fields, b"valid", "image/png")
    assert expired_response.status_code == 403
    assert (
        await direct_upload(
            created.upload.url, created.upload.fields, b"<svg><script>", "image/png"
        )
    ).status_code in {200, 201, 204}
    rejected = await service.finalize(context, created.asset.id)
    assert rejected.status is AssetStatus.REJECTED and rejected.rejection_code == "mime_mismatch"


@pytest.mark.postgres
@pytest.mark.object_storage
@pytest.mark.asyncio
async def test_protected_asset_api_exposes_grants_but_never_storage_keys(
    admin_engine: AsyncEngine,
    catalog_factory: SqlAlchemyCatalogUnitOfWorkFactory,
    runtime_database_url: str,
) -> None:
    store = storage()
    await store.ensure_private_bucket(["http://localhost:3000"])
    context, brand, product = await product_setup(admin_engine, catalog_factory)
    endpoint = os.environ["TEST_OBJECT_STORAGE_URL"]
    app = create_app(
        Settings(
            app_env="test",
            database_url=runtime_database_url,
            dev_identity_enabled=True,
            audit_fingerprint_key="asset-api-fingerprint-key-32-bytes",
            cors_origins=["http://localhost:3000"],
            object_storage_backend="s3",
            object_storage_endpoint_url=endpoint,
            object_storage_public_endpoint_url=endpoint,
            object_storage_access_key_id="creative-marketer-test",
            object_storage_secret_access_key="creative-marketer-test-secret",
        )
    )
    auth = headers(context.tenant_id, context.user_id)
    body = {
        "brand_id": str(brand.id),
        "product_id": str(product.id),
        "kind": "image",
        "role": "product_hero",
        "original_filename": "api.png",
        "mime_type": "image/png",
        "rights_status": "confirmed",
        "allowed_uses": ["internal_analysis"],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api") as client:
        created = await client.post("/v1/assets", headers=auth, json=body)
        assert created.status_code == 201
        payload = created.json()
        assert "object_key" not in payload["asset"]
        assert "upload_object_key" not in payload["asset"]
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 24
        uploaded = await direct_upload(payload["url"], payload["fields"], png, "image/png")
        assert uploaded.status_code in {200, 201, 204}
        asset_id = payload["asset"]["id"]
        finalized = await client.post(f"/v1/assets/{asset_id}/finalize", headers=auth)
        assert finalized.json()["status"] == "ready"
        product_assets = await client.get(f"/v1/products/{product.id}/assets", headers=auth)
        brand_assets = await client.get(f"/v1/brands/{brand.id}/assets", headers=auth)
        assert len(product_assets.json()) == len(brand_assets.json()) == 1
        assert (await client.get(f"/v1/assets/{asset_id}", headers=auth)).status_code == 200
        download = await client.post(f"/v1/assets/{asset_id}/download", headers=auth)
        assert download.status_code == 200
        assert "X-Amz-Signature" in download.json()["url"]
        archived = await client.post(f"/v1/assets/{asset_id}/archive", headers=auth)
        assert archived.json()["status"] == "archived"
