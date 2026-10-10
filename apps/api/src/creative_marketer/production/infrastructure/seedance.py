from __future__ import annotations

import asyncio
import base64
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from creative_marketer.production.application import SEEDANCE_MODEL
from creative_marketer.production.media import (
    InvalidMediaResult,
    MediaProviderActivationRequired,
    MediaProviderBadRequest,
    MediaProviderError,
    MediaProviderInsufficientCredits,
    MediaProviderOutcomeUnknown,
    MediaProviderTransientFailure,
    ProviderGenerationState,
    VideoGenerationRequest,
    VideoStartResult,
    VideoStatusResult,
    assert_reference_limits,
)

DEFAULT_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
TASK_PATH = "/contents/generations/tasks"
MAX_REQUEST_BYTES = 64 * 1024 * 1024


class JsonHttpTransportError(Exception):
    """A request failed without an authoritative HTTP response."""


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
            raise JsonHttpTransportError("ModelArk transport did not return HTTP") from error


def _json_object(raw: bytes) -> Mapping[str, object]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise InvalidMediaResult("ModelArk returned invalid JSON") from error
    if not isinstance(value, dict):
        raise InvalidMediaResult("ModelArk response must be an object")
    return value


def _error_code(raw: bytes) -> str | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    error = value.get("error")
    nested_code: object = error.get("code") if isinstance(error, dict) else None
    if isinstance(nested_code, str):
        return nested_code[:128]
    top_level_code: object = value.get("code")
    return top_level_code[:128] if isinstance(top_level_code, str) else None


def _classified_error(status: int, raw: bytes) -> MediaProviderError:
    code = (_error_code(raw) or "").casefold()
    if code in {
        "modelnotopen",
        "operationdenied.servicenotopen",
        "managedagentnotopen",
    }:
        return MediaProviderActivationRequired("ModelArk model activation is required")
    if code in {
        "accountoverdueerror",
        "operationdenied.serviceoverdue",
        "balancenotenough",
    }:
        return MediaProviderInsufficientCredits("ModelArk account credit is unavailable")
    if status in {400, 422} or code in {"invalidparameter", "missingparameter"}:
        return MediaProviderBadRequest("ModelArk rejected the request contract")
    if status in {408, 409, 425, 429} or status >= 500:
        return MediaProviderTransientFailure("ModelArk is temporarily unavailable")
    return MediaProviderError("ModelArk request was rejected")


def _https_url(value: str, *, label: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError(f"{label} must be a credential-free HTTPS URL")
    return value


def _reference_content(request: VideoGenerationRequest) -> list[dict[str, object]]:
    content: list[dict[str, object]] = [{"type": "text", "text": request.instruction}]
    for item in request.references:
        kind = item.media_type.split("/", 1)[0].casefold()
        if kind not in {"image", "video", "audio"}:
            raise ValueError("ModelArk reference media type is unsupported")
        if item.source_url is not None:
            locator = _https_url(item.source_url, label="ModelArk reference")
        elif kind == "video":
            raise ValueError("ModelArk video references require a bounded HTTPS source URL")
        else:
            encoded = base64.b64encode(item.data).decode("ascii")
            locator = f"data:{item.media_type.casefold()};base64,{encoded}"
        role = {"video": "reference_video", "audio": "reference_audio"}.get(kind)
        if kind == "image":
            role = (
                item.role
                if item.role in {"first_frame", "last_frame", "reference_image"}
                else "reference_image"
            )
        content.append(
            {
                "type": f"{kind}_url",
                f"{kind}_url": {"url": locator},
                "role": role,
            }
        )
    return content


@dataclass(slots=True)
class BytePlusModelArkVideoProvider:
    api_key: str
    transport: JsonHttpTransport | None = None
    base_url: str = DEFAULT_BASE_URL
    allow_real_face_references: bool = False

    def __post_init__(self) -> None:
        if not self.api_key or self.api_key.startswith(("disabled-", "test-", "fake-", "replace-")):
            raise ValueError("a non-placeholder ModelArk API key is required")
        parsed = urlsplit(self.base_url)
        hostname = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or not re.fullmatch(r"ark\.[a-z0-9-]+\.bytepluses\.com", hostname)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/api/v3"
        ):
            raise ValueError("ModelArk BaseURL must be an HTTPS BytePlus regional /api/v3 endpoint")
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
            raise ValueError("real-face references require ModelArk material-library allowlisting")
        payload = {
            "model": SEEDANCE_MODEL,
            "content": _reference_content(request),
            "generate_audio": request.generate_audio,
            "resolution": request.resolution,
            "ratio": request.aspect_ratio,
            "duration": request.duration_seconds,
            "watermark": False,
            "execution_expires_after": request.execution_expires_after,
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if len(body) > MAX_REQUEST_BYTES:
            raise ValueError("ModelArk request exceeds the official body-size limit")
        assert self.transport is not None
        try:
            status, raw = await self.transport.request(
                "POST", self.base_url + TASK_PATH, self._headers, body
            )
        except JsonHttpTransportError as error:
            raise MediaProviderOutcomeUnknown("ModelArk start outcome is unknown") from error
        if status >= 500:
            raise MediaProviderOutcomeUnknown("ModelArk start outcome is unknown")
        if status >= 400:
            raise _classified_error(status, raw)
        try:
            task_id = _json_object(raw)["id"]
        except (KeyError, InvalidMediaResult) as error:
            raise MediaProviderOutcomeUnknown("ModelArk start response was ambiguous") from error
        if not isinstance(task_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", task_id
        ):
            raise MediaProviderOutcomeUnknown("ModelArk returned an invalid task reference")
        return VideoStartResult(task_id)

    async def status(self, provider_operation_ref: str) -> VideoStatusResult:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", provider_operation_ref):
            raise ValueError("ModelArk task reference is invalid")
        assert self.transport is not None
        try:
            status, raw = await self.transport.request(
                "GET", f"{self.base_url}{TASK_PATH}/{provider_operation_ref}", self._headers, None
            )
        except JsonHttpTransportError as error:
            raise MediaProviderTransientFailure("ModelArk polling transport failed") from error
        if status >= 400:
            raise _classified_error(status, raw)
        value = _json_object(raw)
        if value.get("id") != provider_operation_ref:
            raise InvalidMediaResult("ModelArk polling response task identity does not match")
        try:
            state = {
                "queued": ProviderGenerationState.QUEUED,
                "running": ProviderGenerationState.RUNNING,
                "succeeded": ProviderGenerationState.SUCCEEDED,
                "failed": ProviderGenerationState.FAILED,
                "cancelled": ProviderGenerationState.FAILED,
                "expired": ProviderGenerationState.EXPIRED,
            }[str(value["status"]).strip().casefold()]
        except (KeyError, TypeError) as error:
            raise InvalidMediaResult("ModelArk returned an invalid task status") from error
        locator = None
        if state is ProviderGenerationState.SUCCEEDED:
            response_content = value.get("content")
            locator = (
                response_content.get("video_url") if isinstance(response_content, dict) else None
            )
            if not isinstance(locator, str):
                raise InvalidMediaResult("ModelArk success omitted its temporary result")
            try:
                locator = _https_url(locator, label="ModelArk result")
            except ValueError as error:
                raise InvalidMediaResult("ModelArk returned an invalid result URL") from error
        usage = value.get("usage")
        duration_value = value.get("duration")
        resolution_value = value.get("resolution")
        return VideoStatusResult(
            state,
            locator,
            duration_value if isinstance(duration_value, int) else None,
            resolution_value if isinstance(resolution_value, str) else None,
            "BYTEPLUS_MODELARK_TASK_FAILED"
            if state is ProviderGenerationState.FAILED
            else "BYTEPLUS_MODELARK_TASK_EXPIRED"
            if state is ProviderGenerationState.EXPIRED
            else None,
            usage if isinstance(usage, dict) else None,
        )

    async def download(self, temporary_result_locator: str) -> bytes:
        _https_url(temporary_result_locator, label="provider result locator")
        assert self.transport is not None
        try:
            status, content = await self.transport.request(
                "GET", temporary_result_locator, {}, None
            )
        except JsonHttpTransportError as error:
            raise MediaProviderTransientFailure("ModelArk output download failed") from error
        if status >= 400:
            raise _classified_error(status, content)
        return content
