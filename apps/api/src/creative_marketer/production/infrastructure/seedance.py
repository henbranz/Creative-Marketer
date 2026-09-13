from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from creative_marketer.production.application import SEEDANCE_MODEL
from creative_marketer.production.media import (
    MediaProviderError,
    MediaProviderOutcomeUnknown,
    ProviderGenerationState,
    VideoGenerationRequest,
    VideoStartResult,
    VideoStatusResult,
    assert_reference_limits,
)

DEFAULT_BASE_URL = "https://operator.las.ap-southeast-1.bytepluses.com"
TASK_PATH = "/api/v1/contents/generations/tasks"


class JsonHttpTransport(Protocol):
    async def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]: ...


class UrllibJsonHttpTransport:
    async def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        def execute() -> tuple[int, bytes]:
            request = Request(url, data=body, headers=dict(headers), method=method)
            try:
                with urlopen(request, timeout=30) as response:
                    return response.status, response.read()
            except HTTPError as error:
                return error.code, error.read()

        try:
            return await asyncio.to_thread(execute)
        except (TimeoutError, URLError) as error:
            raise MediaProviderOutcomeUnknown("BytePlus transport outcome is unknown") from error


@dataclass(slots=True)
class SeedanceMediaProvider:
    api_key: str
    transport: JsonHttpTransport | None = None
    base_url: str = DEFAULT_BASE_URL
    allow_real_face_references: bool = False

    def __post_init__(self) -> None:
        if not self.api_key or self.api_key.startswith(("disabled-", "test-", "fake-", "replace-")):
            raise ValueError("a non-placeholder BytePlus API key is required")
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or not parsed.hostname.endswith(".bytepluses.com")
        ):
            raise ValueError("BytePlus LAS BaseURL must be an HTTPS BytePlus regional origin")
        self.base_url = self.base_url.rstrip("/")
        if self.transport is None:
            self.transport = UrllibJsonHttpTransport()

    @property
    def _headers(self) -> Mapping[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def start(self, request: VideoGenerationRequest) -> VideoStartResult:
        if not 4 <= request.duration_seconds <= 30:
            raise ValueError("Seedance 2.5 duration must be 4 to 30 seconds")
        if request.resolution not in {"480p", "720p"} or request.aspect_ratio not in {
            "16:9",
            "4:3",
            "1:1",
            "3:4",
            "9:16",
            "21:9",
        }:
            raise ValueError("Seedance route does not support requested media dimensions")
        if not 3600 <= request.execution_expires_after <= 259200:
            raise ValueError("Seedance execution expiry is outside official bounds")
        assert_reference_limits(request.references, images=30, videos=10, audio=10)
        if not self.allow_real_face_references and any(
            item.contains_real_face for item in request.references
        ):
            raise ValueError("real-face references require BytePlus material-library allowlisting")
        content: list[dict[str, object]] = [{"type": "text", "text": request.instruction}]
        for item in request.references:
            kind = item.media_type.split("/", 1)[0]
            if kind == "video":
                raise ValueError("V1 does not inline large video references")
            encoded = base64.b64encode(item.data).decode("ascii")
            url = f"data:{item.media_type};base64,{encoded}"
            content.append(
                {
                    "type": f"{kind}_url",
                    f"{kind}_url": {"url": url},
                    "role": item.role,
                }
            )
        payload = {
            "model": SEEDANCE_MODEL,
            "content": content,
            "generate_audio": request.generate_audio,
            "resolution": request.resolution,
            "ratio": request.aspect_ratio,
            "duration": request.duration_seconds,
            "watermark": False,
            "execution_expires_after": request.execution_expires_after,
        }
        assert self.transport is not None
        status, raw = await self.transport.request(
            "POST", self.base_url + TASK_PATH, self._headers, json.dumps(payload).encode()
        )
        if status >= 500:
            raise MediaProviderOutcomeUnknown("BytePlus start outcome is unknown")
        if status >= 400:
            raise MediaProviderError("BytePlus rejected generation before acceptance")
        try:
            task_id = json.loads(raw)["id"]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise MediaProviderOutcomeUnknown("BytePlus start response was ambiguous") from error
        if not isinstance(task_id, str) or not task_id or len(task_id) > 256:
            raise MediaProviderOutcomeUnknown("BytePlus returned an invalid task reference")
        return VideoStartResult(task_id)

    async def status(self, provider_operation_ref: str) -> VideoStatusResult:
        if not provider_operation_ref or "/" in provider_operation_ref:
            raise ValueError("BytePlus task reference is invalid")
        assert self.transport is not None
        status, raw = await self.transport.request(
            "GET", f"{self.base_url}{TASK_PATH}/{provider_operation_ref}", self._headers, None
        )
        if status >= 400:
            raise MediaProviderError("BytePlus status request failed")
        try:
            value = json.loads(raw)
            state = {
                "queued": ProviderGenerationState.QUEUED,
                "running": ProviderGenerationState.RUNNING,
                "succeeded": ProviderGenerationState.SUCCEEDED,
                "failed": ProviderGenerationState.FAILED,
                "cancelled": ProviderGenerationState.FAILED,
                "expired": ProviderGenerationState.EXPIRED,
            }[value["status"].strip()]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise MediaProviderError("BytePlus returned an invalid status response") from error
        locator = None
        if state is ProviderGenerationState.SUCCEEDED:
            content = value.get("content")
            locator = content.get("video_url") if isinstance(content, dict) else None
            if not isinstance(locator, str) or not locator.startswith("https://"):
                raise MediaProviderError("BytePlus success omitted its temporary result")
        usage = value.get("usage")
        return VideoStatusResult(
            state,
            locator,
            value.get("duration") if isinstance(value.get("duration"), int) else None,
            value.get("resolution") if isinstance(value.get("resolution"), str) else None,
            "BYTEPLUS_TERMINAL_FAILURE" if state is ProviderGenerationState.FAILED else None,
            usage if isinstance(usage, dict) else None,
        )

    async def download(self, temporary_result_locator: str) -> bytes:
        if not temporary_result_locator.startswith("https://"):
            raise ValueError("provider result locator must use HTTPS")
        assert self.transport is not None
        status, content = await self.transport.request("GET", temporary_result_locator, {}, None)
        if status >= 400:
            raise MediaProviderError("BytePlus output download failed")
        return content
