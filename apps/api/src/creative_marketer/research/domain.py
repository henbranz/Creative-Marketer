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
class ResearchContextManifest:
    tenant_id: UUID
    product_id: UUID
    evidence: tuple[ResearchEvidenceReference, ...]
    digest: str
    schema_version: int = 1

    @classmethod
    def build(
        cls, tenant_id: UUID, product_id: UUID, values: tuple[ResearchEvidenceReference, ...]
    ) -> ResearchContextManifest:
        ordered = tuple(
            sorted(values, key=lambda item: (item.category.value, str(item.source_id)))
        )[:50]
        content = {
            "schema_version": 1,
            "product_id": str(product_id),
            "evidence": [item.semantic() for item in ordered],
        }
        return cls(tenant_id, product_id, ordered, event_sha256_v1(content))
