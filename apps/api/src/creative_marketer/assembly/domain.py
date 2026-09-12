from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from creative_marketer.agent_runtime.domain import canonical_digest

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class AssemblyError(Exception):
    code = "ASSEMBLY_ERROR"


class AssemblyNotFound(AssemblyError):
    code = "ASSEMBLY_NOT_FOUND"


class AssemblyPermissionDenied(AssemblyError):
    code = "ASSEMBLY_PERMISSION_DENIED"


class AssemblyNotReady(AssemblyError):
    code = "ASSEMBLY_NOT_READY"


class AssemblySourceRightsChanged(AssemblyError):
    code = "ASSEMBLY_SOURCE_RIGHTS_CHANGED"


class AssemblySourceDigestMismatch(AssemblyError):
    code = "ASSEMBLY_SOURCE_DIGEST_MISMATCH"


class AssemblyInvalidTimeline(AssemblyError):
    code = "ASSEMBLY_INVALID_TIMELINE"


class AssemblyRenderFailed(AssemblyError):
    code = "ASSEMBLY_RENDER_FAILED"


class AssemblyRenderTimeout(AssemblyError):
    code = "ASSEMBLY_RENDER_TIMEOUT"


class AssemblyOutputInvalid(AssemblyError):
    code = "ASSEMBLY_OUTPUT_INVALID"


class AssemblyReadiness(StrEnum):
    READY = "READY"
    MISSING_GENERATED_MEDIA = "MISSING_GENERATED_MEDIA"
    MISSING_MANUAL_MEDIA = "MISSING_MANUAL_MEDIA"
    SOURCE_ARCHIVED = "SOURCE_ARCHIVED"
    RIGHTS_CHANGED = "RIGHTS_CHANGED"
    PRODUCTION_OUTDATED = "PRODUCTION_OUTDATED"


class SourceKind(StrEnum):
    EXISTING_ASSET = "EXISTING_ASSET"
    GENERATED_IMAGE = "GENERATED_IMAGE"
    GENERATED_VIDEO = "GENERATED_VIDEO"
    MANUAL_VIDEO = "MANUAL_VIDEO"


class FitMode(StrEnum):
    CROP_FILL = "CROP_FILL"
    FIT_WITH_BACKGROUND = "FIT_WITH_BACKGROUND"


class Transition(StrEnum):
    CUT = "CUT"
    CROSSFADE = "CROSSFADE"


class AudioBehavior(StrEnum):
    KEEP = "KEEP"
    MUTE = "MUTE"


class OverlayStyle(StrEnum):
    HOOK_PRIMARY = "HOOK_PRIMARY"
    BODY_SUBTITLE = "BODY_SUBTITLE"
    CTA_PRIMARY = "CTA_PRIMARY"
    DISCLAIMER = "DISCLAIMER"


class CaptionKind(StrEnum):
    SCRIPT_CAPTIONS = "SCRIPT_CAPTIONS"


class AssemblyJobStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RENDERING = "RENDERING"
    IMPORTING = "IMPORTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class FinalCreativeDecisionState(StrEnum):
    APPROVED_FOR_PUBLISHING = "APPROVED_FOR_PUBLISHING"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class RenderProfile:
    key: str = "social_vertical_v1"
    version: int = 1
    width: int = 1080
    height: int = 1920
    fps: int = 24
    video_codec: str = "libx264"
    pixel_format: str = "yuv420p"
    audio_codec: str = "aac"
    sample_rate_hz: int = 48_000
    loudness_lufs: int = -14
    true_peak_db: float = -1.5
    safe_zone_version: int = 1
    typography_profile: str = "noto_sans_v1"

    def __post_init__(self) -> None:
        if (self.key, self.version, self.width, self.height, self.fps) != (
            "social_vertical_v1",
            1,
            1080,
            1920,
            24,
        ):
            raise ValueError("unsupported render profile")

    def primitive(self) -> dict[str, object]:
        return {
            "key": self.key,
            "version": self.version,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "video_codec": self.video_codec,
            "pixel_format": self.pixel_format,
            "audio_codec": self.audio_codec,
            "sample_rate_hz": self.sample_rate_hz,
            "loudness_lufs": self.loudness_lufs,
            "true_peak_db": self.true_peak_db,
            "safe_zone_version": self.safe_zone_version,
            "typography_profile": self.typography_profile,
        }


@dataclass(frozen=True, slots=True)
class AssemblySource:
    asset_id: UUID
    digest: str
    media_kind: str
    mime_type: str
    byte_size: int
    object_key: str
    has_audio: bool = False

    def __post_init__(self) -> None:
        if not _DIGEST.fullmatch(self.digest) or self.byte_size <= 0 or not self.object_key:
            raise ValueError("assembly source identity is invalid")


@dataclass(frozen=True, slots=True)
class AssemblyItem:
    item_key: str
    ordinal: int
    source_kind: SourceKind
    source_asset_id: UUID
    source_asset_digest: str
    source_media_kind: str
    production_shot_ids: tuple[UUID, ...]
    production_shot_keys: tuple[str, ...]
    generation_segment_id: UUID | None
    timeline_start_ms: int
    timeline_duration_ms: int
    source_in_ms: int = 0
    source_out_ms: int | None = None
    fit_mode: FitMode = FitMode.CROP_FILL
    audio_behavior: AudioBehavior = AudioBehavior.KEEP
    transition_in: Transition = Transition.CUT
    transition_out: Transition = Transition.CUT
    transition_duration_ms: int = 0

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.item_key):
            raise AssemblyInvalidTimeline("assembly item key is invalid")
        if self.ordinal < 1 or self.timeline_start_ms < 0 or self.timeline_duration_ms <= 0:
            raise AssemblyInvalidTimeline("assembly item timing is invalid")
        if not self.production_shot_ids or len(set(self.production_shot_ids)) != len(
            self.production_shot_ids
        ):
            raise AssemblyInvalidTimeline("assembly item requires unique shot provenance")
        if not _DIGEST.fullmatch(self.source_asset_digest):
            raise AssemblyInvalidTimeline("assembly source digest is invalid")
        if (
            self.transition_in is Transition.CROSSFADE
            or self.transition_out is Transition.CROSSFADE
        ):
            if not 150 <= self.transition_duration_ms <= 500:
                raise AssemblyInvalidTimeline("crossfade duration is outside 150-500 ms")
        elif self.transition_duration_ms != 0:
            raise AssemblyInvalidTimeline("cut transitions cannot have a duration")

    def primitive(self) -> dict[str, object]:
        return {
            "item_key": self.item_key,
            "ordinal": self.ordinal,
            "source_kind": self.source_kind.value,
            "source_asset_id": str(self.source_asset_id),
            "source_asset_digest": self.source_asset_digest,
            "source_media_kind": self.source_media_kind,
            "production_shot_ids": [str(value) for value in self.production_shot_ids],
            "production_shot_keys": list(self.production_shot_keys),
            "generation_segment_id": (
                str(self.generation_segment_id) if self.generation_segment_id else None
            ),
            "timeline_start_ms": self.timeline_start_ms,
            "timeline_duration_ms": self.timeline_duration_ms,
            "source_in_ms": self.source_in_ms,
            "source_out_ms": self.source_out_ms,
            "fit_mode": self.fit_mode.value,
            "audio_behavior": self.audio_behavior.value,
            "transition_in": self.transition_in.value,
            "transition_out": self.transition_out.value,
            "transition_duration_ms": self.transition_duration_ms,
        }


@dataclass(frozen=True, slots=True)
class CaptionCue:
    ordinal: int
    text: str
    start_ms: int
    end_ms: int
    kind: CaptionKind = CaptionKind.SCRIPT_CAPTIONS

    def __post_init__(self) -> None:
        if self.ordinal < 1 or not self.text.strip() or not 0 <= self.start_ms < self.end_ms:
            raise AssemblyInvalidTimeline("caption cue is invalid")
        if len(self.text) > 500:
            raise AssemblyInvalidTimeline("caption text exceeds the bounded limit")

    def primitive(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "kind": self.kind.value,
        }


@dataclass(frozen=True, slots=True)
class OverlayInstruction:
    ordinal: int
    text: str
    start_ms: int
    end_ms: int
    position: str
    style_token: OverlayStyle
    source_provenance: str

    def __post_init__(self) -> None:
        if (
            self.ordinal < 1
            or not self.text.strip()
            or len(self.text) > 500
            or not 0 <= self.start_ms < self.end_ms
            or self.position not in {"TOP_SAFE", "CENTER_SAFE", "BOTTOM_SAFE"}
        ):
            raise AssemblyInvalidTimeline("overlay instruction is invalid")
        if self.style_token is OverlayStyle.DISCLAIMER and self.end_ms - self.start_ms < 1500:
            raise AssemblyInvalidTimeline("disclaimer duration is too short")

    def primitive(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "position": self.position,
            "style_token": self.style_token.value,
            "source_provenance": self.source_provenance,
        }


@dataclass(frozen=True, slots=True)
class AssemblyPlan:
    tenant_id: UUID
    product_id: UUID
    production_plan_id: UUID
    production_plan_digest: str
    creative_concept_id: UUID
    creative_concept_digest: str
    items: tuple[AssemblyItem, ...]
    captions: tuple[CaptionCue, ...]
    overlays: tuple[OverlayInstruction, ...]
    timeline_duration_ms: int
    semantic_digest: str
    created_by_user_id: UUID
    id: UUID = field(default_factory=uuid4)
    schema_version: int = 1
    render_profile: RenderProfile = field(default_factory=RenderProfile)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.items or [item.ordinal for item in self.items] != list(
            range(1, len(self.items) + 1)
        ):
            raise AssemblyInvalidTimeline("assembly items must be contiguous")
        cursor = 0
        for item in self.items:
            if item.timeline_start_ms != cursor:
                raise AssemblyInvalidTimeline("assembly timeline contains a gap or overlap")
            cursor += item.timeline_duration_ms
        if cursor != self.timeline_duration_ms:
            raise AssemblyInvalidTimeline("assembly duration does not cover its timeline")
        represented = [shot for item in self.items for shot in item.production_shot_ids]
        if len(represented) != len(set(represented)):
            raise AssemblyInvalidTimeline("a ProductionShot is represented more than once")
        if not all(
            _DIGEST.fullmatch(value)
            for value in (
                self.production_plan_digest,
                self.creative_concept_digest,
                self.semantic_digest,
            )
        ):
            raise AssemblyInvalidTimeline("assembly provenance digest is invalid")
        if self.semantic_digest != canonical_digest(self.semantic_content()):
            raise AssemblyInvalidTimeline("assembly semantic digest does not match its content")

    def semantic_content(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "production_plan_digest": self.production_plan_digest,
            "creative_concept_digest": self.creative_concept_digest,
            "render_profile": self.render_profile.primitive(),
            "timeline_duration_ms": self.timeline_duration_ms,
            "items": [item.primitive() for item in self.items],
            "captions": [cue.primitive() for cue in self.captions],
            "overlays": [item.primitive() for item in self.overlays],
            "audio_policy": {"version": 1, "source_audio": "KEEP", "silent_allowed": True},
        }


@dataclass(frozen=True, slots=True)
class AssemblyJob:
    tenant_id: UUID
    assembly_plan_id: UUID
    idempotency_key: str
    created_by_user_id: UUID
    id: UUID = field(default_factory=uuid4)
    status: AssemblyJobStatus = AssemblyJobStatus.READY
    failure_code: str | None = None
    output_asset_id: UUID | None = None
    final_creative_id: UUID | None = None
    renderer: str | None = None
    renderer_version: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def transition(
        self,
        status: AssemblyJobStatus,
        *,
        failure_code: str | None = None,
        output_asset_id: UUID | None = None,
        final_creative_id: UUID | None = None,
        renderer: str | None = None,
        renderer_version: str | None = None,
    ) -> AssemblyJob:
        allowed = {
            AssemblyJobStatus.READY: {AssemblyJobStatus.RENDERING, AssemblyJobStatus.FAILED},
            AssemblyJobStatus.RENDERING: {AssemblyJobStatus.IMPORTING, AssemblyJobStatus.FAILED},
            AssemblyJobStatus.IMPORTING: {AssemblyJobStatus.SUCCEEDED, AssemblyJobStatus.FAILED},
        }
        if status not in allowed.get(self.status, set()):
            raise ValueError("invalid assembly job transition")
        return replace(
            self,
            status=status,
            failure_code=failure_code,
            output_asset_id=output_asset_id,
            final_creative_id=final_creative_id,
            renderer=renderer,
            renderer_version=renderer_version,
            updated_at=datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class FinalCreative:
    tenant_id: UUID
    product_id: UUID
    assembly_plan_id: UUID
    assembly_plan_digest: str
    production_plan_id: UUID
    production_plan_digest: str
    creative_concept_id: UUID
    creative_concept_digest: str
    output_asset_id: UUID
    output_asset_digest: str
    renderer: str
    renderer_version: str
    duration_ms: int
    width: int
    height: int
    fps: int
    has_audio: bool
    source_count: int
    semantic_digest: str
    id: UUID = field(default_factory=uuid4)
    render_profile_key: str = "social_vertical_v1"
    render_profile_version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not all(
            _DIGEST.fullmatch(value)
            for value in (
                self.assembly_plan_digest,
                self.production_plan_digest,
                self.creative_concept_digest,
                self.output_asset_digest,
                self.semantic_digest,
            )
        ):
            raise ValueError("FinalCreative digest provenance is invalid")
        if (
            self.render_profile_key != "social_vertical_v1"
            or self.render_profile_version != 1
            or (self.width, self.height, self.fps) != (1080, 1920, 24)
            or self.duration_ms <= 0
            or self.source_count <= 0
        ):
            raise ValueError("FinalCreative render profile is invalid")
        expected = final_creative_digest(
            assembly_plan_digest=self.assembly_plan_digest,
            output_asset_digest=self.output_asset_digest,
            renderer=self.renderer,
            renderer_version=self.renderer_version,
        )
        if self.semantic_digest != expected:
            raise ValueError("FinalCreative semantic digest is invalid")


@dataclass(frozen=True, slots=True)
class FinalCreativeDecision:
    tenant_id: UUID
    final_creative_id: UUID
    final_creative_digest: str
    output_asset_id: UUID
    output_asset_digest: str
    state: FinalCreativeDecisionState
    decided_by_user_id: UUID
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not _DIGEST.fullmatch(self.final_creative_digest) or not _DIGEST.fullmatch(
            self.output_asset_digest
        ):
            raise ValueError("FinalCreative decision binding is invalid")


def final_creative_digest(
    *, assembly_plan_digest: str, output_asset_digest: str, renderer: str, renderer_version: str
) -> str:
    return canonical_digest(
        {
            "assembly_plan_digest": assembly_plan_digest,
            "output_asset_digest": output_asset_digest,
            "renderer": renderer,
            "renderer_version": renderer_version,
            "render_profile": "social_vertical_v1@1",
        }
    )
