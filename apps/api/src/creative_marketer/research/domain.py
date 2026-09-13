from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from uuid import UUID, uuid4

from creative_marketer.events.domain import event_sha256_v1

MAX_URL_LENGTH = 2048
MAX_DISPLAY_NAME_LENGTH = 200
MAX_EVIDENCE_BLOCKS = 500
MAX_BLOCK_CHARACTERS = 8192
MAX_OUTBOUND_LINKS = 200
MAX_EXTRACTED_BYTES = 500 * 1024
SECRET_QUERY_KEYS = re.compile(
    r"(?i)(?:^|[_-])(token|secret|password|passwd|key|api[_-]?key|access[_-]?key|auth|signature|sig)(?:$|[_-])"
)


class ResearchValidationError(ValueError):
    pass


class SourceType(StrEnum):
    WEB_PAGE = "web_page"


class ResearchCategory(StrEnum):
    COMPETITOR = "competitor"
    PRODUCT_PAGE = "product_page"
    LANDING_PAGE = "landing_page"
    PRICING = "pricing"
    REVIEW = "review"
    MARKET_REFERENCE = "market_reference"
    CREATIVE_REFERENCE = "creative_reference"
    OTHER = "other"


class ResearchSourceStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ResearchTargetKind(StrEnum):
    COMPETITOR_BRAND = "competitor_brand"
    ADVERTISER = "advertiser"
    SOCIAL_PROFILE = "social_profile"
    PRODUCT = "product"


class SocialPlatform(StrEnum):
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    OTHER = "other"


class SocialEvidenceProvenance(StrEnum):
    USER_PROVIDED = "user_provided"
    PROVIDER_FETCHED = "provider_fetched"


class SocialEvidenceType(StrEnum):
    AD = "ad"
    POST = "post"
    REEL = "reel"
    VIDEO = "video"
    SCREENSHOT = "screenshot"
    EXPORTED_IMAGE = "exported_image"
    EXPORTED_VIDEO = "exported_video"
    OTHER = "other"


class FetchStatus(StrEnum):
    PENDING = "pending"
    FETCHING = "fetching"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"


class FetchFailureCode(StrEnum):
    URL_INVALID = "url_invalid"
    SECRET_QUERY_PARAMETER = "secret_query_parameter"
    UNSAFE_ADDRESS = "blocked_network_target"
    DNS_FAILED = "dns_failed"
    DNS_REBINDING = "dns_rebinding"
    ROBOTS_DENIED = "robots_denied"
    ROBOTS_UNAVAILABLE = "robots_unavailable"
    REDIRECT_LIMIT = "redirect_limit"
    REDIRECT_INVALID = "redirect_invalid"
    CONNECT_TIMEOUT = "connect_timeout"
    OVERALL_TIMEOUT = "overall_timeout"
    HTTP_ERROR = "http_error"
    RESPONSE_TOO_LARGE = "response_too_large"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    BINARY_CONTENT = "binary_content"
    EXTRACTION_FAILED = "extraction_failed"
    STORAGE_FAILED = "storage_failed"
    CONCURRENT_FETCH = "concurrent_fetch"


class EvidenceBlockKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE_TEXT = "table_text"
    QUOTE = "quote"


def canonicalize_url(value: str) -> str:
    if not value or len(value) > MAX_URL_LENGTH:
        raise ResearchValidationError("url must be present and at most 2048 characters")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ResearchValidationError("url is invalid") from error
    scheme = parsed.scheme.lower()
    try:
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as error:
        raise ResearchValidationError("url hostname is invalid") from error
    if scheme not in {"http", "https"} or not host:
        raise ResearchValidationError("only absolute http and https urls are supported")
    if parsed.username is not None or parsed.password is not None:
        raise ResearchValidationError("url credentials are forbidden")
    if port not in {None, 80, 443}:
        raise ResearchValidationError("only ports 80 and 443 are supported")
    if any(
        SECRET_QUERY_KEYS.search(key) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
    ):
        raise ResearchValidationError("secret-like query parameters are forbidden")
    netloc = host
    if ":" in host:
        netloc = f"[{host}]"
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{netloc}:{port}"
    canonical = urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))
    if len(canonical) > MAX_URL_LENGTH:
        raise ResearchValidationError("canonical url exceeds 2048 characters")
    return canonical


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def research_sha256_v1(value: object) -> str:
    """Canonical digest for tenant evidence; content is not an event payload."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_bytes(canonical.encode())


@dataclass(frozen=True, slots=True)
class ResearchSource:
    tenant_id: UUID
    product_id: UUID
    canonical_url: str
    display_name: str
    category: ResearchCategory
    created_by: UUID
    id: UUID = field(default_factory=uuid4)
    source_type: SourceType = SourceType.WEB_PAGE
    status: ResearchSourceStatus = ResearchSourceStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.canonical_url != canonicalize_url(self.canonical_url):
            raise ResearchValidationError("source url must be canonical")
        if not self.display_name.strip() or len(self.display_name) > MAX_DISPLAY_NAME_LENGTH:
            raise ResearchValidationError("display name must be present and bounded")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ResearchValidationError("source timestamps must be timezone-aware")

    def archive(self, now: datetime | None = None) -> ResearchSource:
        if self.status is ResearchSourceStatus.ARCHIVED:
            return self
        return replace(
            self, status=ResearchSourceStatus.ARCHIVED, updated_at=now or datetime.now(UTC)
        )


def _bounded_optional(value: str | None, maximum: int, label: str) -> str | None:
    if value is None or not value.strip():
        return None
    if len(value) > maximum:
        raise ResearchValidationError(f"{label} exceeds {maximum} characters")
    return value.strip()


def _optional_public_url(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return canonicalize_url(value.strip())


@dataclass(frozen=True, slots=True)
class ResearchTarget:
    tenant_id: UUID
    product_id: UUID
    kind: ResearchTargetKind
    display_name: str
    created_by: UUID
    id: UUID = field(default_factory=uuid4)
    website_url: str | None = None
    platform: SocialPlatform | None = None
    platform_handle: str | None = None
    platform_profile_url: str | None = None
    platform_identifier: str | None = None
    status: ResearchSourceStatus = ResearchSourceStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        name = self.display_name.strip()
        if not name or len(name) > 200:
            raise ResearchValidationError("research target display name is required and bounded")
        object.__setattr__(self, "display_name", name)
        object.__setattr__(self, "website_url", _optional_public_url(self.website_url))
        object.__setattr__(
            self, "platform_profile_url", _optional_public_url(self.platform_profile_url)
        )
        object.__setattr__(
            self, "platform_handle", _bounded_optional(self.platform_handle, 200, "platform handle")
        )
        object.__setattr__(
            self,
            "platform_identifier",
            _bounded_optional(self.platform_identifier, 200, "platform identifier"),
        )
        if self.platform is None and any(
            (self.platform_handle, self.platform_profile_url, self.platform_identifier)
        ):
            raise ResearchValidationError("platform metadata requires a platform")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ResearchValidationError("target timestamps must be timezone-aware")

    def archive(self, now: datetime | None = None) -> ResearchTarget:
        if self.status is ResearchSourceStatus.ARCHIVED:
            return self
        return replace(
            self, status=ResearchSourceStatus.ARCHIVED, updated_at=now or datetime.now(UTC)
        )


@dataclass(frozen=True, slots=True)
class SourceFetch:
    tenant_id: UUID
    source_id: UUID
    requested_url: str
    requested_by: UUID
    raw_object_key: str
    id: UUID = field(default_factory=uuid4)
    status: FetchStatus = FetchStatus.PENDING
    final_url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    raw_digest: str | None = None
    raw_byte_size: int | None = None
    evidence_snapshot_id: UUID | None = None
    failure_code: FetchFailureCode | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        canonicalize_url(self.requested_url)
        prefix = f"tenants/{self.tenant_id}/research/{self.source_id}/{self.id}/raw/"
        if not self.raw_object_key.startswith(prefix):
            raise ResearchValidationError("raw object key violates tenant research prefix")

    def begin(self, now: datetime) -> SourceFetch:
        if self.status is not FetchStatus.PENDING:
            raise ResearchValidationError("only pending fetches can begin")
        return replace(self, status=FetchStatus.FETCHING, started_at=now)

    def succeed(
        self,
        *,
        final_url: str,
        http_status: int,
        content_type: str,
        raw_digest: str,
        raw_byte_size: int,
        evidence_snapshot_id: UUID,
        now: datetime,
    ) -> SourceFetch:
        if self.status is not FetchStatus.FETCHING:
            raise ResearchValidationError("only fetching attempts can succeed")
        return replace(
            self,
            status=FetchStatus.SUCCEEDED,
            final_url=canonicalize_url(final_url),
            http_status=http_status,
            content_type=content_type,
            raw_digest=raw_digest,
            raw_byte_size=raw_byte_size,
            evidence_snapshot_id=evidence_snapshot_id,
            completed_at=now,
        )

    def stop(self, *, rejected: bool, code: FetchFailureCode, now: datetime) -> SourceFetch:
        if self.status not in {FetchStatus.PENDING, FetchStatus.FETCHING}:
            raise ResearchValidationError("fetch is already terminal")
        return replace(
            self,
            status=FetchStatus.REJECTED if rejected else FetchStatus.FAILED,
            failure_code=code,
            completed_at=now,
        )


@dataclass(frozen=True, slots=True)
class EvidenceBlock:
    kind: EvidenceBlockKind
    text: str
    ordinal: int

    def __post_init__(self) -> None:
        if (
            self.ordinal < 0
            or not self.text.strip()
            or len(self.text.encode()) > MAX_BLOCK_CHARACTERS
        ):
            raise ResearchValidationError("evidence block is empty or exceeds its bound")

    def semantic(self) -> dict[str, object]:
        return {"kind": self.kind.value, "ordinal": self.ordinal, "text": self.text}


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    tenant_id: UUID
    product_id: UUID
    source_id: UUID
    source_fetch_id: UUID
    final_url: str
    title: str | None
    blocks: tuple[EvidenceBlock, ...]
    outbound_links: tuple[str, ...]
    structured_metadata: dict[str, str]
    raw_digest: str
    semantic_digest: str
    captured_at: datetime
    id: UUID = field(default_factory=uuid4)
    schema_version: int = 1
    extractor_version: str = "html-v1"
    instruction_like_content: bool = False

    def __post_init__(self) -> None:
        if not self.blocks or len(self.blocks) > MAX_EVIDENCE_BLOCKS:
            raise ResearchValidationError("evidence must contain 1 to 500 blocks")
        if len(self.outbound_links) > MAX_OUTBOUND_LINKS:
            raise ResearchValidationError("too many outbound links")
        if len(str(self.semantic()).encode()) > MAX_EXTRACTED_BYTES:
            raise ResearchValidationError("extracted evidence exceeds 500 KiB")
        canonicalize_url(self.final_url)
        if self.semantic_digest != research_sha256_v1(self.digest_input()):
            raise ResearchValidationError("evidence semantic digest does not match content")
        object.__setattr__(
            self, "structured_metadata", MappingProxyType(dict(self.structured_metadata))
        )

    def digest_input(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "extractor_version": self.extractor_version,
            "final_url": self.final_url,
            "title": self.title,
            "blocks": [block.semantic() for block in self.blocks],
            "outbound_links": list(self.outbound_links),
            "structured_metadata": dict(self.structured_metadata),
            "instruction_like_content": self.instruction_like_content,
        }

    def semantic(self) -> dict[str, object]:
        return self.digest_input()


@dataclass(frozen=True, slots=True)
class SocialEvidenceSnapshot:
    tenant_id: UUID
    product_id: UUID
    research_target_id: UUID
    platform: SocialPlatform
    evidence_type: SocialEvidenceType
    provenance: SocialEvidenceProvenance
    captured_by: UUID
    captured_at: datetime
    semantic_digest: str
    id: UUID = field(default_factory=uuid4)
    source_url: str | None = None
    destination_url: str | None = None
    advertiser_name: str | None = None
    advertiser_platform_id: str | None = None
    platform_content_id: str | None = None
    headline: str | None = None
    body_text: str | None = None
    cta: str | None = None
    ad_objective: str | None = None
    media_type: str | None = None
    placements: tuple[str, ...] = ()
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    activity_status: str | None = None
    region: str | None = None
    reach_range: str | None = None
    source_provider: str | None = None
    media_asset_id: UUID | None = None
    raw_provider_metadata_digest: str | None = None
    schema_version: int = 1
    rights_status: str = "restricted"
    allowed_uses: tuple[str, ...] = ("internal_analysis",)

    def __post_init__(self) -> None:
        for name, maximum in (
            ("advertiser_name", 300),
            ("advertiser_platform_id", 200),
            ("platform_content_id", 200),
            ("headline", 1000),
            ("body_text", 8000),
            ("cta", 300),
            ("ad_objective", 200),
            ("media_type", 100),
            ("activity_status", 100),
            ("region", 200),
            ("reach_range", 200),
            ("source_provider", 100),
        ):
            object.__setattr__(self, name, _bounded_optional(getattr(self, name), maximum, name))
        object.__setattr__(self, "source_url", _optional_public_url(self.source_url))
        object.__setattr__(self, "destination_url", _optional_public_url(self.destination_url))
        normalized_placements = tuple(item.strip() for item in self.placements if item.strip())
        if len(normalized_placements) > 20 or any(
            len(item) > 100 for item in normalized_placements
        ):
            raise ResearchValidationError("placements are not bounded")
        object.__setattr__(self, "placements", normalized_placements)
        if self.captured_at.tzinfo is None or any(
            value is not None and value.tzinfo is None
            for value in (self.first_seen_at, self.last_seen_at)
        ):
            raise ResearchValidationError("social evidence timestamps must be timezone-aware")
        if self.provenance is SocialEvidenceProvenance.USER_PROVIDED and self.source_provider:
            raise ResearchValidationError("manual evidence cannot claim a provider")
        if (
            self.provenance is SocialEvidenceProvenance.PROVIDER_FETCHED
            and not self.source_provider
        ):
            raise ResearchValidationError("provider evidence must identify its provider")
        if self.rights_status != "restricted" or self.allowed_uses != ("internal_analysis",):
            raise ResearchValidationError("competitor evidence is restricted to internal analysis")
        if not any((self.source_url, self.headline, self.body_text, self.media_asset_id)):
            raise ResearchValidationError("social evidence requires a source, text, or media")
        computed_digest = research_sha256_v1(self.digest_input())
        if not self.semantic_digest:
            object.__setattr__(self, "semantic_digest", computed_digest)
        elif self.semantic_digest != computed_digest:
            raise ResearchValidationError("social evidence semantic digest does not match content")

    def digest_input(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "product_id": str(self.product_id),
            "research_target_id": str(self.research_target_id),
            "platform": self.platform.value,
            "evidence_type": self.evidence_type.value,
            "provenance": self.provenance.value,
            "source_url": self.source_url,
            "destination_url": self.destination_url,
            "advertiser_name": self.advertiser_name,
            "advertiser_platform_id": self.advertiser_platform_id,
            "platform_content_id": self.platform_content_id,
            "headline": self.headline,
            "body_text": self.body_text,
            "cta": self.cta,
            "ad_objective": self.ad_objective,
            "media_type": self.media_type,
            "placements": list(self.placements),
            "first_seen_at": self.first_seen_at.astimezone(UTC).isoformat()
            if self.first_seen_at
            else None,
            "last_seen_at": self.last_seen_at.astimezone(UTC).isoformat()
            if self.last_seen_at
            else None,
            "activity_status": self.activity_status,
            "region": self.region,
            "reach_range": self.reach_range,
            "source_provider": self.source_provider,
            "media_asset_id": str(self.media_asset_id) if self.media_asset_id else None,
            "raw_provider_metadata_digest": self.raw_provider_metadata_digest,
            "rights_status": self.rights_status,
            "allowed_uses": list(self.allowed_uses),
        }

    def analysis_blocks(self) -> tuple[EvidenceBlock, ...]:
        values = (
            ("Advertiser", self.advertiser_name),
            ("Headline", self.headline),
            ("Body or caption", self.body_text),
            ("Call to action", self.cta),
            ("Objective", self.ad_objective),
            ("Media type", self.media_type),
            ("Placements", ", ".join(self.placements) or None),
            ("Status", self.activity_status),
            ("Region", self.region),
            ("Officially supplied reach", self.reach_range),
            ("Source URL", self.source_url),
            ("Destination URL", self.destination_url),
        )
        text = "\n".join(f"{label}: {value}" for label, value in values if value)
        if not text:
            text = f"User-provided {self.platform.value} {self.evidence_type.value} media evidence."
        return (EvidenceBlock(EvidenceBlockKind.PARAGRAPH, text, 0),)


@dataclass(frozen=True, slots=True)
class ResearchEvidenceReference:
    source_id: UUID
    evidence_snapshot_id: UUID
    semantic_digest: str
    category: ResearchCategory
    captured_at: datetime

    def semantic(self) -> dict[str, object]:
        return {
            "source_id": str(self.source_id),
            "evidence_snapshot_id": str(self.evidence_snapshot_id),
            "semantic_digest": self.semantic_digest,
            "category": self.category.value,
            "captured_at": self.captured_at.astimezone(UTC).isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SocialEvidenceReference:
    research_target_id: UUID
    social_evidence_snapshot_id: UUID
    semantic_digest: str
    platform: SocialPlatform
    captured_at: datetime

    def semantic(self) -> dict[str, object]:
        return {
            "research_target_id": str(self.research_target_id),
            "social_evidence_snapshot_id": str(self.social_evidence_snapshot_id),
            "semantic_digest": self.semantic_digest,
            "platform": self.platform.value,
            "captured_at": self.captured_at.astimezone(UTC).isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ResearchContextManifest:
    tenant_id: UUID
    product_id: UUID
    evidence: tuple[ResearchEvidenceReference, ...]
    digest: str
    social_evidence: tuple[SocialEvidenceReference, ...] = ()
    schema_version: int = 2

    @classmethod
    def build(
        cls,
        tenant_id: UUID,
        product_id: UUID,
        values: tuple[ResearchEvidenceReference, ...],
        social_values: tuple[SocialEvidenceReference, ...] = (),
    ) -> ResearchContextManifest:
        social_ordered = tuple(
            sorted(
                social_values,
                key=lambda item: (
                    item.platform.value,
                    -item.captured_at.timestamp(),
                    str(item.social_evidence_snapshot_id),
                ),
            )
        )[:20]
        ordered = tuple(
            sorted(values, key=lambda item: (item.category.value, str(item.source_id)))
        )[: 50 - len(social_ordered)]
        content = {
            "schema_version": 2,
            "product_id": str(product_id),
            "evidence": [item.semantic() for item in ordered],
            "social_evidence": [item.semantic() for item in social_ordered],
        }
        return cls(tenant_id, product_id, ordered, event_sha256_v1(content), social_ordered)
