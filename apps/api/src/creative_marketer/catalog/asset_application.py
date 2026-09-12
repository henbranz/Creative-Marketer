from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import BinaryIO, Protocol
from uuid import UUID, uuid4

from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditOutcome
from creative_marketer.audit.safety import safe_metadata
from creative_marketer.catalog.application import (
    CatalogConflict,
    CatalogNotFound,
    CatalogPermissionDenied,
    CatalogUnitOfWork,
    CatalogUnitOfWorkFactory,
    _event,
    require_catalog_mutation,
)
from creative_marketer.catalog.asset_domain import (
    MAX_BYTES,
    AllowedUse,
    Asset,
    AssetKind,
    AssetOrigin,
    AssetRole,
    AssetStatus,
    RightsStatus,
    detect_mime,
    image_dimensions,
)
from creative_marketer.catalog.domain import CatalogValidationError
from creative_marketer.identity.application.authentication import ExecutionContext


class ObjectStoreUnavailable(Exception):
    pass


@dataclass(frozen=True, slots=True)
class UploadGrant:
    url: str
    fields: dict[str, str]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class DownloadGrant:
    url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    byte_size: int
    content_type: str | None


class ObjectStore(Protocol):
    async def create_upload_grant(
        self, *, key: str, content_type: str, max_bytes: int
    ) -> UploadGrant: ...
    async def create_download_grant(self, *, key: str) -> DownloadGrant: ...
    async def head(self, *, key: str) -> ObjectMetadata: ...
    def stream(self, *, key: str) -> AsyncIterator[bytes]: ...
    async def promote(self, *, source_key: str, destination_key: str) -> None: ...
    async def put_private(self, *, key: str, content_type: str, body: bytes | BinaryIO) -> None: ...


class UnavailableObjectStore:
    async def create_upload_grant(
        self, *, key: str, content_type: str, max_bytes: int
    ) -> UploadGrant:
        raise ObjectStoreUnavailable("object storage is not configured")

    async def create_download_grant(self, *, key: str) -> DownloadGrant:
        raise ObjectStoreUnavailable("object storage is not configured")

    async def head(self, *, key: str) -> ObjectMetadata:
        raise ObjectStoreUnavailable("object storage is not configured")

    async def stream(self, *, key: str) -> AsyncIterator[bytes]:
        raise ObjectStoreUnavailable("object storage is not configured")
        yield b""  # pragma: no cover

    async def promote(self, *, source_key: str, destination_key: str) -> None:
        raise ObjectStoreUnavailable("object storage is not configured")

    async def put_private(self, *, key: str, content_type: str, body: bytes | BinaryIO) -> None:
        raise ObjectStoreUnavailable("object storage is not configured")


@dataclass(frozen=True, slots=True)
class CreatedAsset:
    asset: Asset
    upload: UploadGrant


async def _record(
    uow: CatalogUnitOfWork,
    context: ExecutionContext,
    asset: Asset,
    action: str,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
) -> None:
    await uow.audit.append(
        tenant_audit(
            context,
            action=action,
            outcome=outcome,
            resource_type="asset",
            resource_id=str(asset.id),
            after_digest=asset.digest,
            metadata=safe_metadata(
                {
                    "brand_id": str(asset.brand_id),
                    "product_id": None if asset.product_id is None else str(asset.product_id),
                    "kind": asset.kind.value,
                    "status": asset.status.value,
                }
            ),
        )
    )


@dataclass(slots=True)
class AssetService:
    uow_factory: CatalogUnitOfWorkFactory
    object_store: ObjectStore

    async def ingest_generated(
        self,
        context: ExecutionContext,
        *,
        brand_id: UUID,
        product_id: UUID,
        kind: AssetKind,
        role: AssetRole,
        content: bytes | BinaryIO,
        media_type: str,
        rights_status: RightsStatus,
        allowed_uses: tuple[AllowedUse, ...],
        original_filename: str,
        width: int | None = None,
        height: int | None = None,
        duration_ms: int | None = None,
    ) -> Asset:
        """Validate and privately ingest provider output as an ordinary immutable Asset."""
        require_catalog_mutation(context)
        if context.user_id is None:
            raise CatalogPermissionDenied("generated Asset ingestion requires an initiating user")
        if isinstance(content, bytes):
            prefix = content[:64]
            byte_size = len(content)
            digest_value = sha256(content).hexdigest()
        else:
            content.seek(0)
            prefix = content.read(64)
            content.seek(0)
            hasher = sha256()
            byte_size = 0
            while chunk := content.read(1024 * 1024):
                byte_size += len(chunk)
                if byte_size > MAX_BYTES[kind]:
                    raise CatalogValidationError("generated media failed binary validation")
                hasher.update(chunk)
            digest_value = hasher.hexdigest()
            content.seek(0)
        if not prefix or byte_size > MAX_BYTES[kind] or detect_mime(prefix) != media_type:
            raise CatalogValidationError("generated media failed binary validation")
        asset_id = uuid4()
        digest = "sha256:" + digest_value
        key_prefix = f"tenants/{context.tenant_id}/assets/{asset_id}"
        upload_key = f"{key_prefix}/uploads/generated-{digest[7:]}"
        object_key = f"{key_prefix}/objects/{digest[7:]}"
        await self.object_store.put_private(key=object_key, content_type=media_type, body=content)
        detected_width, detected_height = image_dimensions(media_type, prefix)
        now = datetime.now(UTC)
        asset = Asset(
            tenant_id=context.tenant_id,
            brand_id=brand_id,
            product_id=product_id,
            kind=kind,
            role=role,
            origin=AssetOrigin.GENERATED,
            status=AssetStatus.READY,
            original_filename=original_filename,
            declared_mime_type=media_type,
            detected_mime_type=media_type,
            rights_status=rights_status,
            allowed_uses=allowed_uses,
            upload_object_key=upload_key,
            object_key=object_key,
            byte_size=byte_size,
            digest=digest,
            created_by=context.user_id,
            id=asset_id,
            width=width if width is not None else detected_width,
            height=height if height is not None else detected_height,
            duration_ms=duration_ms,
            created_at=now,
            updated_at=now,
        )
        async with self.uow_factory(context) as uow:
            product = await uow.products.get(product_id)
            if product is None or product.brand_id != brand_id:
                raise CatalogNotFound("product not found")
            await uow.assets.add(asset)
            await _record(uow, context, asset, "catalog.asset.generated_ready")
            await _event(
                uow,
                context,
                "catalog.asset.ready.v2",
                "asset",
                asset.id,
                {
                    "asset_id": str(asset.id),
                    "brand_id": str(asset.brand_id),
                    "product_id": str(asset.product_id),
                    "kind": asset.kind.value,
                    "role": asset.role.value,
                    "mime_type": asset.detected_mime_type,
                    "byte_size": asset.byte_size,
                    "digest": asset.digest,
                },
                schema_version=2,
            )
            await uow.commit()
        return asset

    async def create(self, context: ExecutionContext, asset: Asset) -> CreatedAsset:
        require_catalog_mutation(context)
        if asset.tenant_id != context.tenant_id or asset.created_by != context.user_id:
            raise CatalogPermissionDenied("asset identity must come from execution context")
        async with self.uow_factory(context) as uow:
            if await uow.brands.get(asset.brand_id) is None:
                raise CatalogNotFound("brand not found")
            if asset.product_id is not None:
                product = await uow.products.get(asset.product_id)
                if product is None or product.brand_id != asset.brand_id:
                    raise CatalogNotFound("product not found")
            if asset.parent_asset_id is not None:
                parent = await uow.assets.get(asset.parent_asset_id)
                if parent is None:
                    raise CatalogNotFound("parent asset not found")
            upload = await self.object_store.create_upload_grant(
                key=asset.upload_object_key,
                content_type=asset.declared_mime_type,
                max_bytes=MAX_BYTES[asset.kind],
            )
            await uow.assets.add(asset)
            await _record(uow, context, asset, "catalog.asset.upload_created")
            await uow.commit()
            return CreatedAsset(asset, upload)

    async def list(
        self,
        context: ExecutionContext,
        *,
        product_id: UUID | None = None,
        kind: AssetKind | None = None,
        status: AssetStatus | None = None,
    ) -> tuple[Asset, ...]:
        async with self.uow_factory(context) as uow:
            return await uow.assets.list(product_id=product_id, kind=kind, status=status)

    async def get(self, context: ExecutionContext, asset_id: UUID) -> Asset:
        async with self.uow_factory(context) as uow:
            value = await uow.assets.get(asset_id)
            if value is None:
                raise CatalogNotFound("asset not found")
            return value

    async def finalize(self, context: ExecutionContext, asset_id: UUID) -> Asset:
        require_catalog_mutation(context)
        async with self.uow_factory(context) as uow:
            asset = await uow.assets.get(asset_id)
            if asset is None:
                raise CatalogNotFound("asset not found")
            if asset.status is AssetStatus.READY or asset.status is AssetStatus.REJECTED:
                return asset
            if asset.status is AssetStatus.VALIDATING:
                if asset.updated_at > datetime.now(UTC) - timedelta(minutes=15):
                    raise CatalogConflict("asset validation is already in progress")
                validating = replace(asset, updated_at=datetime.now(UTC))
                await uow.assets.update(validating, expected_status=AssetStatus.VALIDATING)
            elif asset.status is AssetStatus.PENDING_UPLOAD:
                validating = asset.validating()
                await uow.assets.update(validating, expected_status=AssetStatus.PENDING_UPLOAD)
            else:
                raise CatalogConflict("asset cannot be finalized from its current state")
            await uow.commit()

        try:
            metadata = await self.object_store.head(key=asset.upload_object_key)
            if metadata.byte_size > MAX_BYTES[asset.kind]:
                raise CatalogValidationError("file_too_large")
            if metadata.content_type not in {None, asset.declared_mime_type}:
                raise CatalogValidationError("content_type_mismatch")
            prefix = bytearray()
            byte_size = 0
            digest = sha256()
            async for chunk in self.object_store.stream(key=asset.upload_object_key):
                byte_size += len(chunk)
                if byte_size > MAX_BYTES[asset.kind]:
                    raise CatalogValidationError("file_too_large")
                if len(prefix) < 64:
                    prefix.extend(chunk[: 64 - len(prefix)])
                digest.update(chunk)
            detected = detect_mime(bytes(prefix))
            if byte_size != metadata.byte_size:
                raise ObjectStoreUnavailable("object changed during validation")
            if detected is None or detected != asset.declared_mime_type:
                raise CatalogValidationError("mime_mismatch")
            final_key = f"tenants/{asset.tenant_id}/assets/{asset.id}/objects/{uuid4().hex}"
            await self.object_store.promote(
                source_key=asset.upload_object_key, destination_key=final_key
            )
            width, height = image_dimensions(detected, bytes(prefix))
            result = validating.ready(
                object_key=final_key,
                detected_mime_type=detected,
                byte_size=byte_size,
                digest=f"sha256:{digest.hexdigest()}",
                width=width,
                height=height,
            )
        except CatalogValidationError as error:
            result = validating.rejected(str(error))
        except ObjectStoreUnavailable:
            async with self.uow_factory(context) as retry_uow:
                await retry_uow.assets.update(
                    validating.retry_pending(), expected_status=AssetStatus.VALIDATING
                )
                await retry_uow.commit()
            raise

        async with self.uow_factory(context) as uow:
            await uow.assets.update(result, expected_status=AssetStatus.VALIDATING)
            await _record(
                uow,
                context,
                result,
                "catalog.asset.ready"
                if result.status is AssetStatus.READY
                else "catalog.asset.rejected",
                AuditOutcome.SUCCESS if result.status is AssetStatus.READY else AuditOutcome.DENIED,
            )
            if result.status is AssetStatus.READY:
                await _event(
                    uow,
                    context,
                    "catalog.asset.ready.v1",
                    "asset",
                    result.id,
                    {
                        "asset_id": str(result.id),
                        "brand_id": str(result.brand_id),
                        "product_id": None if result.product_id is None else str(result.product_id),
                        "kind": result.kind.value,
                        "role": result.role.value,
                        "mime_type": result.detected_mime_type,
                        "byte_size": result.byte_size,
                        "digest": result.digest,
                    },
                )
            await uow.commit()
        return result

    async def download(self, context: ExecutionContext, asset_id: UUID) -> DownloadGrant:
        asset = await self.get(context, asset_id)
        if asset.status is not AssetStatus.READY or asset.object_key is None:
            raise CatalogConflict("asset is not downloadable")
        return await self.object_store.create_download_grant(key=asset.object_key)

    async def archive(self, context: ExecutionContext, asset_id: UUID) -> Asset:
        require_catalog_mutation(context)
        async with self.uow_factory(context) as uow:
            asset = await uow.assets.get(asset_id)
            if asset is None:
                raise CatalogNotFound("asset not found")
            archived = asset.archived()
            await uow.assets.update(archived, expected_status=asset.status)
            await _record(uow, context, archived, "catalog.asset.archived")
            await _event(
                uow,
                context,
                "catalog.asset.archived.v1",
                "asset",
                archived.id,
                {
                    "asset_id": str(archived.id),
                    "brand_id": str(archived.brand_id),
                    "product_id": None if archived.product_id is None else str(archived.product_id),
                },
            )
            await uow.commit()
            return archived
