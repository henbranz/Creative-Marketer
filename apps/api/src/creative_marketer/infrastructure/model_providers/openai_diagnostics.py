"""Content-free diagnostics; never stringify an SDK error, body, or headers."""

from __future__ import annotations

import re
from collections.abc import Mapping

from openai import APIStatusError

_TYPES = frozenset(
    {
        "invalid_request_error",
        "authentication_error",
        "permission_error",
        "server_error",
        "rate_limit_error",
    }
)
_CODES = frozenset(
    {
        "invalid_json_schema",
        "invalid_request",
        "invalid_api_key",
        "model_not_found",
        "unsupported_parameter",
        "unsupported_value",
        "invalid_value",
        "missing_required_parameter",
        "context_length_exceeded",
        "insufficient_quota",
        "rate_limit_exceeded",
        "invalid_service_tier",
        "permission_denied",
    }
)
_PARAMS = frozenset(
    {
        "model",
        "instructions",
        "input",
        "text",
        "text.format",
        "text.format.type",
        "text.format.name",
        "text.format.schema",
        "text.format.strict",
        "reasoning",
        "reasoning.effort",
        "max_output_tokens",
        "tools",
        "store",
        "service_tier",
    }
)
_CLASSES = frozenset(
    {
        "APIStatusError",
        "BadRequestError",
        "AuthenticationError",
        "PermissionDeniedError",
        "NotFoundError",
        "ConflictError",
        "UnprocessableEntityError",
        "RateLimitError",
        "InternalServerError",
    }
)


def safe_status_diagnostic(error: APIStatusError) -> dict[str, int | str | None]:
    body = error.body
    if isinstance(body, Mapping) and isinstance(body.get("error"), Mapping):
        body = body["error"]
    fields = body if isinstance(body, Mapping) else {}

    def allowed(value: object, choices: frozenset[str]) -> str | None:
        if value is None:
            return None
        return value if isinstance(value, str) and value in choices else "REDACTED"

    request_id = error.request_id
    return {
        "http_status": error.status_code,
        "error_type": allowed(fields.get("type"), _TYPES),
        "error_code": allowed(fields.get("code"), _CODES),
        "param": allowed(fields.get("param"), _PARAMS),
        "request_id": request_id
        if isinstance(request_id, str) and re.fullmatch(r"req_[A-Za-z0-9_-]{8,80}", request_id)
        else None,
        "exception_class": allowed(type(error).__name__, _CLASSES),
    }
