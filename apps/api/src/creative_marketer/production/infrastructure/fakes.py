from __future__ import annotations

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


@dataclass(slots=True)
class FakeImageProvider:
    outcome: str = "success"
    content: bytes = b"\x89PNG\r\n\x1a\nproduction-image"
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
    content: bytes = b"\x00\x00\x00\x18ftypmp42production-video"
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
