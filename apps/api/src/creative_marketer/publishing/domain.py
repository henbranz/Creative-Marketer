from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from math import gcd
from types import MappingProxyType
from uuid import UUID, uuid4

from creative_marketer.agent_runtime.domain import canonical_digest

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class PublicationError(Exception):
    code = "PUBLISHING_ERROR"


class PublicationNotFound(PublicationError):
    code = "PUBLISHING_NOT_FOUND"


class PublicationPermissionDenied(PublicationError):
    code = "PUBLISHING_PERMISSION_DENIED"


class FinalCreativeNotApproved(PublicationError):
    code = "FINAL_CREATIVE_NOT_APPROVED"


class PublicationDraftOutdated(PublicationError):
    code = "PUBLICATION_DRAFT_OUTDATED"


class PublicationApprovalRequired(PublicationError):
    code = "PUBLICATION_APPROVAL_REQUIRED"


class SocialAccountInactive(PublicationError):
    code = "SOCIAL_ACCOUNT_INACTIVE"


class PlatformCapabilityUnsupported(PublicationError):
    code = "PLATFORM_CAPABILITY_UNSUPPORTED"


class PublicationMediaIncompatible(PublicationError):
    code = "PUBLICATION_MEDIA_INCOMPATIBLE"


class PublicationOutcomeUnknown(PublicationError):
    code = "PUBLICATION_OUTCOME_UNKNOWN"


class PublicationAlreadyExists(PublicationError):
    code = "PUBLICATION_ALREADY_EXISTS"


class PublicationCancelled(PublicationError):
    code = "PUBLICATION_CANCELLED"


class SocialPlatform(StrEnum):
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"


class SocialAccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISCONNECTED = "DISCONNECTED"
    REQUIRES_REAUTH = "REQUIRES_REAUTH"
    ARCHIVED = "ARCHIVED"


class PublicationMode(StrEnum):
    POST_NOW = "POST_NOW"
    SCHEDULE = "SCHEDULE"


class PublicationDecisionState(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PublicationStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    SCHEDULED = "SCHEDULED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    CANCELLED = "CANCELLED"


class ProviderPublicationState(StrEnum):
    SUBMITTED = "SUBMITTED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class PlatformCapabilities:
    platform: SocialPlatform
    media_kinds: frozenset[str]
    max_caption_characters: int
    supports_schedule: bool
    maximum_duration_seconds: int | None = None
    aspect_ratios: frozenset[str] = frozenset({"9:16"})

    def validate(
        self,
        *,
        media_kind: str,
        duration_ms: int,
        width: int,
        height: int,
        caption: str,
        mode: PublicationMode,
    ) -> None:
        if media_kind.casefold() not in self.media_kinds:
            raise PublicationMediaIncompatible("media kind is not supported")
        if len(caption) > self.max_caption_characters:
            raise PlatformCapabilityUnsupported("caption exceeds platform limit")
        if mode is PublicationMode.SCHEDULE and not self.supports_schedule:
            raise PlatformCapabilityUnsupported("scheduling is not supported")
        if self.maximum_duration_seconds is not None and duration_ms > (
            self.maximum_duration_seconds * 1000
        ):
            raise PublicationMediaIncompatible("creative duration exceeds platform limit")
        divisor = max(gcd(width, height), 1)
        ratio = f"{width // divisor}:{height // divisor}"
        if ratio not in self.aspect_ratios:
            raise PublicationMediaIncompatible("creative aspect ratio is not supported")


FAKE_PLATFORM_CAPABILITIES: Mapping[SocialPlatform, PlatformCapabilities] = MappingProxyType(
    {
        SocialPlatform.FACEBOOK: PlatformCapabilities(
            SocialPlatform.FACEBOOK, frozenset({"video", "image"}), 5_000, True, 600
        ),
        SocialPlatform.INSTAGRAM: PlatformCapabilities(
            SocialPlatform.INSTAGRAM, frozenset({"video", "image"}), 2_200, True, 180
        ),
        SocialPlatform.TIKTOK: PlatformCapabilities(
            SocialPlatform.TIKTOK, frozenset({"video"}), 2_200, True, 600
        ),
    }
)


@dataclass(frozen=True, slots=True)
class SocialAccount:
    tenant_id: UUID
    platform: SocialPlatform
    display_name: str
    external_account_id: str
    provider: str = "fake"
    username: str | None = None
    account_type: str | None = None
    status: SocialAccountStatus = SocialAccountStatus.ACTIVE
    capabilities: Mapping[str, object] = field(default_factory=dict)
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.display_name.strip() or len(self.display_name) > 200:
            raise ValueError("SocialAccount display name is invalid")
        if not self.external_account_id.strip() or len(self.external_account_id) > 256:
            raise ValueError("SocialAccount external identifier is invalid")
        if self.provider != "fake":
            raise ValueError("real social providers are not implemented")
        object.__setattr__(self, "capabilities", MappingProxyType(dict(self.capabilities)))

    def with_status(self, status: SocialAccountStatus) -> SocialAccount:
        return replace(self, status=status, updated_at=datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class FinalCreativeAuthority:
    tenant_id: UUID
    product_id: UUID
    final_creative_id: UUID
    final_creative_digest: str
    output_asset_id: UUID
    output_asset_digest: str
    media_kind: str
    duration_ms: int
    width: int
    height: int
    decision_id: UUID | None
    decision_state: str | None

    @property
    def approved(self) -> bool:
        return self.decision_id is not None and self.decision_state == "APPROVED_FOR_PUBLISHING"


def publication_draft_digest(value: Mapping[str, object]) -> str:
    return canonical_digest(value)


@dataclass(frozen=True, slots=True)
class PublicationDraft:
    tenant_id: UUID
    product_id: UUID
    final_creative_id: UUID
    final_creative_digest: str
    output_asset_id: UUID
    output_asset_digest: str
    platform: SocialPlatform
    social_account_id: UUID
    external_destination_id: str
    caption: str
    mode: PublicationMode
    created_by_user_id: UUID
    semantic_digest: str
    title: str | None = None
    hashtags: tuple[str, ...] = ()
    destination_url: str | None = None
    scheduled_at: datetime | None = None
    platform_settings: Mapping[str, object] = field(default_factory=dict)
    id: UUID = field(default_factory=uuid4)
    schema_version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.schema_version != 1 or not self.caption.strip() or len(self.caption) > 5_000:
            raise ValueError("PublicationDraft content is invalid")
        if not self.external_destination_id.strip():
            raise ValueError("PublicationDraft destination is invalid")
        for digest in (
            self.final_creative_digest,
            self.output_asset_digest,
            self.semantic_digest,
        ):
            if not _DIGEST.fullmatch(digest):
                raise ValueError("PublicationDraft digest is invalid")
        if self.mode is PublicationMode.SCHEDULE:
            if self.scheduled_at is None or self.scheduled_at.tzinfo is None:
                raise ValueError("scheduled PublicationDraft requires an aware timestamp")
        elif self.scheduled_at is not None:
            raise ValueError("publish-now PublicationDraft cannot have scheduled_at")
        if len(self.hashtags) > 30 or any(
            not value.strip() or len(value) > 100 for value in self.hashtags
        ):
            raise ValueError("PublicationDraft hashtags are invalid")
        object.__setattr__(
            self, "platform_settings", MappingProxyType(dict(self.platform_settings))
        )
        if self.semantic_digest != self.calculate_digest():
            raise ValueError("PublicationDraft semantic digest mismatch")

    def material(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "tenant_id": str(self.tenant_id),
            "product_id": str(self.product_id),
            "final_creative_id": str(self.final_creative_id),
            "final_creative_digest": self.final_creative_digest,
            "output_asset_id": str(self.output_asset_id),
            "output_asset_digest": self.output_asset_digest,
            "platform": self.platform.value,
            "social_account_id": str(self.social_account_id),
            "external_destination_id": self.external_destination_id,
            "caption": self.caption,
            "title": self.title,
            "hashtags": list(self.hashtags),
            "destination_url": self.destination_url,
            "mode": self.mode.value,
            "scheduled_at": self.scheduled_at.astimezone(UTC).isoformat()
            if self.scheduled_at
            else None,
            "platform_settings": dict(self.platform_settings),
        }

    def calculate_digest(self) -> str:
        return publication_draft_digest(self.material())


def build_publication_draft(
    *,
    tenant_id: UUID,
    product_id: UUID,
    final_creative_id: UUID,
    final_creative_digest: str,
    output_asset_id: UUID,
    output_asset_digest: str,
    platform: SocialPlatform,
    social_account_id: UUID,
    external_destination_id: str,
    caption: str,
    mode: PublicationMode,
    created_by_user_id: UUID,
    title: str | None = None,
    hashtags: tuple[str, ...] = (),
    destination_url: str | None = None,
    scheduled_at: datetime | None = None,
    platform_settings: Mapping[str, object] | None = None,
) -> PublicationDraft:
    settings = dict(platform_settings or {})
    material = {
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "product_id": str(product_id),
        "final_creative_id": str(final_creative_id),
        "final_creative_digest": final_creative_digest,
        "output_asset_id": str(output_asset_id),
        "output_asset_digest": output_asset_digest,
        "platform": platform.value,
        "social_account_id": str(social_account_id),
        "external_destination_id": external_destination_id,
        "caption": caption,
        "title": title,
        "hashtags": list(hashtags),
        "destination_url": destination_url,
        "mode": mode.value,
        "scheduled_at": scheduled_at.astimezone(UTC).isoformat()
        if isinstance(scheduled_at, datetime)
        else None,
        "platform_settings": settings,
    }
    return PublicationDraft(
        tenant_id,
        product_id,
        final_creative_id,
        final_creative_digest,
        output_asset_id,
        output_asset_digest,
        platform,
        social_account_id,
        external_destination_id,
        caption,
        mode,
        created_by_user_id,
        publication_draft_digest(material),
        title,
        hashtags,
        destination_url,
        scheduled_at,
        settings,
    )


@dataclass(frozen=True, slots=True)
class PublicationApprovalBinding:
    tenant_id: UUID
    publication_draft_id: UUID
    publication_draft_digest: str
    final_creative_digest: str
    output_asset_digest: str
    platform: SocialPlatform
    social_account_id: UUID
    external_destination_id: str
    caption_digest: str
    platform_settings_digest: str
    scheduled_at: datetime | None
    action_digest: str

    @classmethod
    def from_draft(cls, draft: PublicationDraft) -> PublicationApprovalBinding:
        material = {
            "publication_draft_id": str(draft.id),
            "publication_draft_digest": draft.semantic_digest,
            "final_creative_digest": draft.final_creative_digest,
            "output_asset_digest": draft.output_asset_digest,
            "platform": draft.platform.value,
            "social_account_id": str(draft.social_account_id),
            "external_destination_id": draft.external_destination_id,
            "caption_digest": canonical_digest(
                {"caption": draft.caption, "hashtags": list(draft.hashtags)}
            ),
            "platform_settings_digest": canonical_digest(dict(draft.platform_settings)),
            "scheduled_at": draft.scheduled_at.astimezone(UTC).isoformat()
            if draft.scheduled_at
            else None,
        }
        return cls(
            draft.tenant_id,
            draft.id,
            draft.semantic_digest,
            draft.final_creative_digest,
            draft.output_asset_digest,
            draft.platform,
            draft.social_account_id,
            draft.external_destination_id,
            str(material["caption_digest"]),
            str(material["platform_settings_digest"]),
            draft.scheduled_at,
            canonical_digest(material),
        )


@dataclass(frozen=True, slots=True)
class PublicationDecision:
    tenant_id: UUID
    binding: PublicationApprovalBinding
    state: PublicationDecisionState
    decided_by_user_id: UUID
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class PublicationJob:
    tenant_id: UUID
    publication_draft_id: UUID
    operation_id: str
    status: PublicationStatus
    created_by_user_id: UUID
    id: UUID = field(default_factory=uuid4)
    failure_code: str | None = None
    external_operation_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def transition(
        self,
        status: PublicationStatus,
        *,
        failure_code: str | None = None,
        external_operation_id: str | None = None,
    ) -> PublicationJob:
        allowed = {
            PublicationStatus.APPROVED: {
                PublicationStatus.SCHEDULED,
                PublicationStatus.SUBMITTING,
                PublicationStatus.CANCELLED,
            },
            PublicationStatus.SCHEDULED: {
                PublicationStatus.SUBMITTING,
                PublicationStatus.CANCELLED,
            },
            PublicationStatus.SUBMITTING: {
                PublicationStatus.SUBMITTED,
                PublicationStatus.PUBLISHED,
                PublicationStatus.FAILED,
                PublicationStatus.OUTCOME_UNKNOWN,
            },
            PublicationStatus.SUBMITTED: {
                PublicationStatus.PUBLISHED,
                PublicationStatus.FAILED,
                PublicationStatus.OUTCOME_UNKNOWN,
                PublicationStatus.CANCELLED,
            },
            PublicationStatus.OUTCOME_UNKNOWN: {
                PublicationStatus.PUBLISHED,
                PublicationStatus.FAILED,
            },
        }
        if status not in allowed.get(self.status, set()):
            raise ValueError("invalid PublicationJob transition")
        return replace(
            self,
            status=status,
            failure_code=failure_code,
            external_operation_id=external_operation_id or self.external_operation_id,
            updated_at=datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class Publication:
    tenant_id: UUID
    product_id: UUID
    publication_draft_id: UUID
    final_creative_id: UUID
    output_asset_id: UUID
    platform: SocialPlatform
    social_account_id: UUID
    external_post_id: str
    provider: str
    connector_version: str
    request_digest: str
    status: PublicationStatus
    semantic_digest: str
    submitted_at: datetime
    external_operation_id: str | None = None
    canonical_permalink: str | None = None
    published_at: datetime | None = None
    response_metadata_digest: str | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.status is not PublicationStatus.PUBLISHED:
            raise ValueError("Publication is only a completed published fact")
        for digest in (self.request_digest, self.semantic_digest):
            if not _DIGEST.fullmatch(digest):
                raise ValueError("Publication digest is invalid")


def build_publication(
    draft: PublicationDraft,
    *,
    external_post_id: str,
    external_operation_id: str | None,
    canonical_permalink: str | None,
    provider: str,
    connector_version: str,
    submitted_at: datetime,
    published_at: datetime,
    response_metadata_digest: str | None = None,
) -> Publication:
    material = {
        "publication_draft_id": str(draft.id),
        "publication_draft_digest": draft.semantic_digest,
        "external_post_id": external_post_id,
        "external_operation_id": external_operation_id,
        "canonical_permalink": canonical_permalink,
        "provider": provider,
        "connector_version": connector_version,
        "published_at": published_at.astimezone(UTC).isoformat(),
    }
    return Publication(
        draft.tenant_id,
        draft.product_id,
        draft.id,
        draft.final_creative_id,
        draft.output_asset_id,
        draft.platform,
        draft.social_account_id,
        external_post_id,
        provider,
        connector_version,
        draft.semantic_digest,
        PublicationStatus.PUBLISHED,
        canonical_digest(material),
        submitted_at,
        external_operation_id,
        canonical_permalink,
        published_at,
        response_metadata_digest,
    )
