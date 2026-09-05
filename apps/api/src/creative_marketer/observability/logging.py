import contextvars
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from opentelemetry import trace

from creative_marketer.observability.safety import safe_attributes

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "observability_correlation_id", default=None
)


class correlation_scope:
    def __init__(self, correlation_id: UUID | str) -> None:
        self._value = str(correlation_id)

    def __enter__(self) -> None:
        self._token = _correlation_id.set(self._value)

    def __exit__(self, *_args: object) -> None:
        _correlation_id.reset(self._token)


class SafeJsonFormatter(logging.Formatter):
    def __init__(self, service: str, environment: str) -> None:
        super().__init__()
        self._service, self._environment = service, environment

    def format(self, record: logging.LogRecord) -> str:
        context = trace.get_current_span().get_span_context()
        fields: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": self._service,
            "environment": self._environment,
            "component": getattr(record, "component", record.name)[:100],
        }
        if context.is_valid:
            fields["trace_id"] = f"{context.trace_id:032x}"
            fields["span_id"] = f"{context.span_id:016x}"
        if correlation_id := _correlation_id.get():
            fields["correlation_id"] = correlation_id
        fields.update(safe_attributes({"action": getattr(record, "action", "log")}))
        fields.update(safe_attributes(getattr(record, "safe_fields", None)))
        if record.exc_info:
            fields["error"] = record.exc_info[0].__name__ if record.exc_info[0] else "Error"
        return json.dumps(fields, separators=(",", ":"), sort_keys=True)


def configure_structured_logging(service: str, environment: str, level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJsonFormatter(service, environment))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
