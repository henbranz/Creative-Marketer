from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


class MediaProviderError(Exception):
    code = "MEDIA_PROVIDER_ERROR"
    outcome_unknown = False


class MediaProviderOutcomeUnknown(MediaProviderError):
    code = "MEDIA_PROVIDER_OUTCOME_UNKNOWN"
    outcome_unknown = True


class InvalidMediaResult(MediaProviderError):
    code = "MEDIA_PROVIDER_INVALID_RESULT"


class ProviderGenerationState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class MaterializedReference:
    media_type: str
    data: bytes
    role: str
    contains_real_face: bool = False


@dataclass(frozen=True, slots=True)
class ImageGenerationRequest:
    instruction: str
    size: str
    quality: str
    references: tuple[MaterializedReference, ...] = ()


@dataclass(frozen=True, slots=True)
class ImageGenerationResult:
    content: bytes
    media_type: str
    provider_response_id: str | None
    actual_cost: Decimal | None
    usage: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class VideoGenerationRequest:
    instruction: str
    duration_seconds: int
    resolution: str
    aspect_ratio: str
    generate_audio: bool
    references: tuple[MaterializedReference, ...] = ()
    execution_expires_after: int = 172800


@dataclass(frozen=True, slots=True)
class VideoStartResult:
    provider_operation_ref: str


@dataclass(frozen=True, slots=True)
class VideoStatusResult:
    state: ProviderGenerationState
    temporary_result_locator: str | None = None
    output_duration_seconds: int | None = None
    resolution: str | None = None
    failure_code: str | None = None
    usage: Mapping[str, int] | None = None


class ImageProvider(Protocol):
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult: ...


class VideoProvider(Protocol):
    async def start(self, request: VideoGenerationRequest) -> VideoStartResult: ...

    async def status(self, provider_operation_ref: str) -> VideoStatusResult: ...

    async def download(self, temporary_result_locator: str) -> bytes: ...


def image_signature(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    raise InvalidMediaResult("generated image signature is not permitted")


def validate_image_result(content: bytes, *, maximum_bytes: int = 25 * 1024 * 1024) -> str:
    if not content or len(content) > maximum_bytes:
        raise InvalidMediaResult("generated image size is outside the allowed limit")
    return image_signature(content)


def validate_video_result(content: bytes, *, maximum_bytes: int = 250 * 1024 * 1024) -> str:
    if not content or len(content) > maximum_bytes:
        raise InvalidMediaResult("generated video size is outside the allowed limit")
    if len(content) < 12 or content[4:8] != b"ftyp":
        raise InvalidMediaResult("generated video is not a recognized MP4")
    return "video/mp4"


def assert_reference_limits(
    references: Sequence[MaterializedReference], *, images: int, videos: int, audio: int
) -> None:
    counts = {
        "image": sum(item.media_type.startswith("image/") for item in references),
        "video": sum(item.media_type.startswith("video/") for item in references),
        "audio": sum(item.media_type.startswith("audio/") for item in references),
    }
    if counts["image"] > images or counts["video"] > videos or counts["audio"] > audio:
        raise ValueError("media references exceed provider limits")
