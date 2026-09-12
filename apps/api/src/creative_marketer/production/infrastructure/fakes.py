from __future__ import annotations

import base64
from dataclasses import dataclass, field
from decimal import Decimal

from creative_marketer.production.media import (
    ImageGenerationRequest,
    ImageGenerationResult,
    InvalidMediaResult,
    MediaProviderError,
    MediaProviderOutcomeUnknown,
    ProviderGenerationState,
    VideoGenerationRequest,
    VideoStartResult,
    VideoStatusResult,
)

# Browser-decodable, public-domain-style synthetic fixtures. They are intentionally tiny and are
# still validated and imported through the ordinary provider -> Asset path.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+Xz2vWQAAAABJRU5ErkJggg=="
)
_MP4 = base64.b64decode(
    "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"
)
DEMO_MP4 = _MP4


@dataclass(slots=True)
class FakeImageProvider:
    outcome: str = "success"
    content: bytes = _PNG
    actual_cost: Decimal = Decimal("0.125")
    call_count: int = 0
    requests: list[ImageGenerationRequest] = field(default_factory=list)

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        self.call_count += 1
        self.requests.append(request)
        if self.outcome == "unknown":
            raise MediaProviderOutcomeUnknown("fake image outcome is unknown")
        if self.outcome == "failure":
            raise MediaProviderError("fake image provider failure")
        if self.outcome == "malformed":
            return ImageGenerationResult(b"invalid", "application/octet-stream", None, None, {})
        if self.outcome == "oversized":
            raise InvalidMediaResult("fake image exceeds configured output bound")
        return ImageGenerationResult(
            self.content,
            "image/png",
            "fake-image-response",
            self.actual_cost,
            {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        )


@dataclass(slots=True)
class FakeSeedanceMediaProvider:
    start_outcome: str = "success"
    statuses: list[ProviderGenerationState] = field(
        default_factory=lambda: [
            ProviderGenerationState.QUEUED,
            ProviderGenerationState.RUNNING,
            ProviderGenerationState.SUCCEEDED,
        ]
    )
    content: bytes = _MP4
    start_count: int = 0
    status_count: int = 0
    download_count: int = 0
    requests: list[VideoGenerationRequest] = field(default_factory=list)

    async def start(self, request: VideoGenerationRequest) -> VideoStartResult:
        self.start_count += 1
        self.requests.append(request)
        if self.start_outcome == "unknown":
            raise MediaProviderOutcomeUnknown("fake Seedance start outcome is unknown")
        if self.start_outcome == "failure":
            raise MediaProviderError("fake Seedance rejected the request")
        return VideoStartResult("fake-seedance-task")

    async def status(self, provider_operation_ref: str) -> VideoStatusResult:
        if provider_operation_ref != "fake-seedance-task":
            raise MediaProviderError("unknown fake Seedance task")
        self.status_count += 1
        state = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return VideoStatusResult(
            state,
            "https://fake.invalid/output.mp4"
            if state is ProviderGenerationState.SUCCEEDED
            else None,
            12 if state is ProviderGenerationState.SUCCEEDED else None,
            "720p" if state is ProviderGenerationState.SUCCEEDED else None,
            "FAKE_TERMINAL_FAILURE"
            if state in {ProviderGenerationState.FAILED, ProviderGenerationState.EXPIRED}
            else None,
            {"output_seconds": 12} if state is ProviderGenerationState.SUCCEEDED else None,
        )

    async def download(self, temporary_result_locator: str) -> bytes:
        if temporary_result_locator != "https://fake.invalid/output.mp4":
            raise InvalidMediaResult("fake result locator is invalid")
        self.download_count += 1
        return self.content
