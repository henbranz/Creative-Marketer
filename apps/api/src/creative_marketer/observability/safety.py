import re
from collections.abc import Mapping

from creative_marketer.observability.metrics import ALLOWED_METRIC_DIMENSIONS
from creative_marketer.observability.ports import SafeScalar

REDACTED = "[REDACTED]"
_FORBIDDEN = re.compile(
    r"(?:authorization|cookie|password|secret|token|api[_-]?key|email|phone|full[_-]?name|"
    r"shipping[_-]?address|prompt|input|output|payload|headers?|query)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(r"(?:bearer\s+\S+|sk-[A-Za-z0-9_-]{8,})", re.IGNORECASE)
_SAFE_ATTRIBUTE_KEYS = frozenset(
    {
        "action",
        "component",
        "correlation_id",
        "deployment.environment",
        "error.code",
        "event.type",
        "http.request.method",
        "http.response.status_code",
        "operation_id",
        "result",
        "risk",
        "service.name",
        "tool.key",
        "url.route",
    }
)


def safe_attributes(
    values: Mapping[str, object] | None, *, metrics: bool = False
) -> dict[str, SafeScalar]:
    allowed = ALLOWED_METRIC_DIMENSIONS if metrics else _SAFE_ATTRIBUTE_KEYS
    cleaned: dict[str, SafeScalar] = {}
    for key, value in (values or {}).items():
        if key not in allowed or _FORBIDDEN.search(key):
            continue
        if not isinstance(value, str | bool | int | float):
            continue
        rendered = str(value)
        cleaned[key] = REDACTED if _SECRET_VALUE.search(rendered) else rendered[:160]
    return cleaned


def safe_error(error: BaseException) -> str:
    name = type(error).__name__
    return name[:100] if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,99}", name) else "Error"


def sanitize_metadata(value: object, *, _depth: int = 0) -> object:
    """Recursively bound diagnostic metadata; callers still need an explicit field allow-list."""
    if _depth > 4:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, child in list(value.items())[:32]:
            key = str(raw_key)[:100]
            result[key] = (
                REDACTED if _FORBIDDEN.search(key) else sanitize_metadata(child, _depth=_depth + 1)
            )
        return result
    if isinstance(value, list | tuple):
        return [sanitize_metadata(child, _depth=_depth + 1) for child in value[:32]]
    if isinstance(value, str):
        return REDACTED if _SECRET_VALUE.search(value) else value[:160]
    if isinstance(value, bool | int | float) or value is None:
        return value
    return type(value).__name__


def assert_metric_dimensions(attributes: Mapping[str, object] | None) -> None:
    rejected = set(attributes or {}) - ALLOWED_METRIC_DIMENSIONS
    if rejected:
        raise ValueError(f"unbounded metric dimensions are forbidden: {sorted(rejected)}")
