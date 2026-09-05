from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from urllib.parse import urlparse
from uuid import UUID, uuid4

from creative_marketer.catalog.domain import CatalogValidationError


class AssetKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"


class AssetStatus(StrEnum):
    PENDING_UPLOAD = "pending_upload"
    VALIDATING = "validating"
    READY = "ready"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class AssetOrigin(StrEnum):
    USER_UPLOAD = "user_upload"


class AssetRole(StrEnum):
    PRODUCT_HERO = "product_hero"
    PRODUCT_DETAIL = "product_detail"
    LIFESTYLE = "lifestyle"
    LOGO = "logo"
    BRAND_GUIDELINE = "brand_guideline"
    PACKAGING = "packaging"
    OTHER = "other"


class RightsStatus(StrEnum):
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"
    RESTRICTED = "restricted"


class AllowedUse(StrEnum):
    INTERNAL_ANALYSIS = "internal_analysis"
    GENERATION_INPUT = "generation_input"
    ORGANIC_PUBLISHING = "organic_publishing"
    PAID_ADVERTISING = "paid_advertising"


MAX_BYTES = {
    AssetKind.IMAGE: 25 * 1024 * 1024,
    AssetKind.DOCUMENT: 25 * 1024 * 1024,
    AssetKind.VIDEO: 250 * 1024 * 1024,
}

MIME_KIND = {
    "image/jpeg": AssetKind.IMAGE,
    "image/png": AssetKind.IMAGE,
    "image/webp": AssetKind.IMAGE,
    "video/mp4": AssetKind.VIDEO,
    "video/webm": AssetKind.VIDEO,
    "application/pdf": AssetKind.DOCUMENT,
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def _required(value: str, label: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise CatalogValidationError(f"{label} must be between 1 and {maximum} characters")
    return normalized


def _source_url(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip()
    if len(normalized) > 2048:
        raise CatalogValidationError("source URL is too long")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
        raise CatalogValidationError("source URL must be absolute HTTP(S) without user info")
    return normalized


@dataclass(frozen=True, slots=True)
class Asset:
    tenant_id: UUID
    brand_id: UUID
    kind: AssetKind
    role: AssetRole
    original_filename: str
    declared_mime_type: str
    rights_status: RightsStatus
    allowed_uses: tuple[AllowedUse, ...]
    created_by: UUID
    upload_object_key: str
    id: UUID = field(default_factory=uuid4)
    product_id: UUID | None = None
    origin: AssetOrigin = AssetOrigin.USER_UPLOAD
    status: AssetStatus = AssetStatus.PENDING_UPLOAD
    object_key: str | None = None
    detected_mime_type: str | None = None
    byte_size: int | None = None
    digest: str | None = None
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    rejection_code: str | None = None
    parent_asset_id: UUID | None = None
    source_url: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "original_filename", _required(self.original_filename, "filename", 255)
        )
        object.__setattr__(
            self, "upload_object_key", _required(self.upload_object_key, "upload key", 1024)
        )
        object.__setattr__(self, "source_url", _source_url(self.source_url))
        if MIME_KIND.get(self.declared_mime_type) is not self.kind:
            raise CatalogValidationError("declared MIME type does not match asset kind")
        if not self.allowed_uses or len(set(self.allowed_uses)) != len(self.allowed_uses):
            raise CatalogValidationError("allowed uses must be non-empty and unique")
        if self.rights_status is not RightsStatus.CONFIRMED and self.allowed_uses != (
            AllowedUse.INTERNAL_ANALYSIS,
        ):
            raise CatalogValidationError(
                "unconfirmed or restricted assets are internal-analysis only"
            )
        if self.parent_asset_id == self.id:
            raise CatalogValidationError("an asset cannot be its own parent")
        if self.status is AssetStatus.READY:
            if not all((self.object_key, self.detected_mime_type, self.byte_size, self.digest)):
                raise CatalogValidationError("ready assets require verified binary identity")
            if self.detected_mime_type != self.declared_mime_type:
                raise CatalogValidationError("ready asset MIME must match its declaration")
        if self.byte_size is not None and not 0 < self.byte_size <= MAX_BYTES[self.kind]:
            raise CatalogValidationError("asset byte size is outside the allowed limit")
        if self.digest is not None and (
            len(self.digest) != 71
            or not self.digest.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in self.digest[7:])
        ):
            raise CatalogValidationError("asset digest must use sha256")

    def validating(self) -> "Asset":
        if self.status is not AssetStatus.PENDING_UPLOAD:
            raise CatalogValidationError("only pending assets can begin validation")
        return replace(self, status=AssetStatus.VALIDATING, updated_at=utc_now())

    def ready(
        self,
        *,
        object_key: str,
        detected_mime_type: str,
        byte_size: int,
        digest: str,
        width: int | None = None,
        height: int | None = None,
        duration_ms: int | None = None,
    ) -> "Asset":
        if self.status is not AssetStatus.VALIDATING:
            raise CatalogValidationError("only validating assets can become ready")
        return replace(
            self,
            status=AssetStatus.READY,
            object_key=object_key,
            detected_mime_type=detected_mime_type,
            byte_size=byte_size,
            digest=digest,
            width=width,
            height=height,
            duration_ms=duration_ms,
            updated_at=utc_now(),
        )

    def rejected(self, code: str) -> "Asset":
        if self.status is not AssetStatus.VALIDATING:
            raise CatalogValidationError("only validating assets can be rejected")
        return replace(
            self,
            status=AssetStatus.REJECTED,
            rejection_code=_required(code, "rejection code", 64),
            updated_at=utc_now(),
        )

    def retry_pending(self) -> "Asset":
        if self.status is not AssetStatus.VALIDATING:
            raise CatalogValidationError("only validating assets can return to pending")
        return replace(self, status=AssetStatus.PENDING_UPLOAD, updated_at=utc_now())

    def archived(self) -> "Asset":
        if self.status not in {AssetStatus.READY, AssetStatus.REJECTED}:
            raise CatalogValidationError("only ready or rejected assets can be archived")
        return replace(self, status=AssetStatus.ARCHIVED, updated_at=utc_now())

    def manifest(self) -> dict[str, object]:
        if self.status is not AssetStatus.READY:
            raise CatalogValidationError("only ready assets belong in a snapshot")
        return {
            "asset_id": str(self.id),
            "kind": self.kind.value,
            "role": self.role.value,
            "mime_type": self.detected_mime_type,
            "byte_size": self.byte_size,
            "digest": self.digest,
            "rights_status": self.rights_status.value,
            "allowed_uses": [value.value for value in self.allowed_uses],
            "parent_asset_id": None if self.parent_asset_id is None else str(self.parent_asset_id),
        }


def detect_mime(prefix: bytes) -> str | None:
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return "image/webp"
    if (
        len(prefix) >= 12
        and prefix[4:8] == b"ftyp"
        and prefix[8:12] in {b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"dash"}
    ):
        return "video/mp4"
    if prefix.startswith(b"\x1aE\xdf\xa3") and b"webm" in prefix[:64].lower():
        return "video/webm"
    if prefix.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def image_dimensions(mime_type: str, prefix: bytes) -> tuple[int | None, int | None]:
    if mime_type == "image/png" and len(prefix) >= 24:
        return int.from_bytes(prefix[16:20], "big"), int.from_bytes(prefix[20:24], "big")
    if mime_type == "image/webp" and len(prefix) >= 30 and prefix[12:16] == b"VP8X":
        return (
            1 + int.from_bytes(prefix[24:27], "little"),
            1 + int.from_bytes(prefix[27:30], "little"),
        )
    return None, None


def sha256_digest(chunks: Iterable[bytes]) -> str:
    digest = sha256()
    for chunk in chunks:
        digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
