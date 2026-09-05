from dataclasses import replace
from hashlib import sha256
from uuid import uuid4

import pytest

from creative_marketer.catalog.asset_domain import (
    AllowedUse,
    Asset,
    AssetKind,
    AssetRole,
    AssetStatus,
    RightsStatus,
    detect_mime,
    image_dimensions,
    sha256_digest,
)
from creative_marketer.catalog.domain import CatalogValidationError


def asset(**changes: object) -> Asset:
    values: dict[str, object] = {
        "tenant_id": uuid4(),
        "brand_id": uuid4(),
        "product_id": uuid4(),
        "kind": AssetKind.IMAGE,
        "role": AssetRole.PRODUCT_DETAIL,
        "original_filename": " product.png ",
        "declared_mime_type": "image/png",
        "rights_status": RightsStatus.CONFIRMED,
        "allowed_uses": (AllowedUse.INTERNAL_ANALYSIS, AllowedUse.GENERATION_INPUT),
        "created_by": uuid4(),
        "upload_object_key": "staging/unique",
    }
    values.update(changes)
    return Asset(**values)  # type: ignore[arg-type]


def test_asset_normalizes_and_enforces_rights_and_type() -> None:
    value = asset()
    assert value.original_filename == "product.png"
    with pytest.raises(CatalogValidationError, match="MIME"):
        asset(kind=AssetKind.VIDEO)
    with pytest.raises(CatalogValidationError, match="internal-analysis"):
        asset(rights_status=RightsStatus.UNKNOWN)
    assert (
        asset(
            rights_status=RightsStatus.UNKNOWN,
            allowed_uses=(AllowedUse.INTERNAL_ANALYSIS,),
        ).rights_status
        is RightsStatus.UNKNOWN
    )
    with pytest.raises(CatalogValidationError, match="non-empty"):
        asset(allowed_uses=())
    with pytest.raises(CatalogValidationError, match="unique"):
        asset(allowed_uses=(AllowedUse.INTERNAL_ANALYSIS, AllowedUse.INTERNAL_ANALYSIS))


def test_asset_urls_lineage_and_ready_identity_are_bounded() -> None:
    value = asset(source_url="https://example.test/source")
    assert value.source_url == "https://example.test/source"
    with pytest.raises(CatalogValidationError, match="source URL"):
        asset(source_url="file:///private/file")
    with pytest.raises(CatalogValidationError, match="own parent"):
        value_id = uuid4()
        asset(id=value_id, parent_asset_id=value_id)
    with pytest.raises(CatalogValidationError, match="verified binary"):
        asset(status=AssetStatus.READY)
    with pytest.raises(CatalogValidationError, match="sha256"):
        asset(digest="md5:bad")
    with pytest.raises(CatalogValidationError, match="sha256"):
        asset(digest="sha256:" + "z" * 64)
    with pytest.raises(CatalogValidationError, match="byte size"):
        asset(byte_size=0)


def test_asset_lifecycle_is_explicit_and_ready_binary_is_manifested() -> None:
    pending = asset()
    validating = pending.validating()
    assert validating.retry_pending().status is AssetStatus.PENDING_UPLOAD
    digest = "sha256:" + sha256(b"bytes").hexdigest()
    ready = validating.ready(
        object_key="immutable/key",
        detected_mime_type="image/png",
        byte_size=5,
        digest=digest,
        width=4,
        height=3,
    )
    assert ready.manifest() == {
        "asset_id": str(ready.id),
        "kind": "image",
        "role": "product_detail",
        "mime_type": "image/png",
        "byte_size": 5,
        "digest": digest,
        "rights_status": "confirmed",
        "allowed_uses": ["internal_analysis", "generation_input"],
        "parent_asset_id": None,
    }
    assert ready.archived().status is AssetStatus.ARCHIVED
    assert validating.rejected("mime_mismatch").rejection_code == "mime_mismatch"
    with pytest.raises(CatalogValidationError):
        pending.ready(object_key="key", detected_mime_type="image/png", byte_size=1, digest=digest)
    with pytest.raises(CatalogValidationError):
        pending.rejected("bad")
    with pytest.raises(CatalogValidationError):
        pending.retry_pending()
    with pytest.raises(CatalogValidationError):
        pending.archived()
    with pytest.raises(CatalogValidationError):
        pending.manifest()


@pytest.mark.parametrize(
    ("prefix", "mime"),
    [
        (b"\xff\xd8\xffrest", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\nrest", "image/png"),
        (b"RIFF0000WEBPrest", "image/webp"),
        (b"0000ftypisom", "video/mp4"),
        (b"\x1aE\xdf\xa3doctype-webm", "video/webm"),
        (b"\x1aE\xdf\xa3doctype-matroska", None),
        (b"0000ftypheic", None),
        (b"%PDF-1.7", "application/pdf"),
        (b"<svg><script>", None),
    ],
)
def test_magic_byte_detection(prefix: bytes, mime: str | None) -> None:
    assert detect_mime(prefix) == mime


def test_dimensions_and_streaming_digest() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 8 + (120).to_bytes(4, "big") + (80).to_bytes(4, "big")
    assert image_dimensions("image/png", png) == (120, 80)
    webp = b"RIFF0000WEBPVP8X" + b"0" * 8 + (9).to_bytes(3, "little") + (19).to_bytes(3, "little")
    assert image_dimensions("image/webp", webp) == (10, 20)
    assert image_dimensions("image/jpeg", b"short") == (None, None)
    assert sha256_digest((b"a", b"b")) == "sha256:" + sha256(b"ab").hexdigest()


def test_ready_identity_cannot_be_forged_with_wrong_mime_or_size() -> None:
    digest = "sha256:" + "a" * 64
    with pytest.raises(CatalogValidationError, match="MIME"):
        asset(
            status=AssetStatus.READY,
            object_key="final",
            detected_mime_type="image/jpeg",
            byte_size=4,
            digest=digest,
        )
    with pytest.raises(CatalogValidationError, match="byte size"):
        replace(asset(), byte_size=25 * 1024 * 1024 + 1)
