from opentelemetry import propagate

from creative_marketer.events.application import TraceContext


def capture_trace_context() -> TraceContext | None:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    traceparent = carrier.get("traceparent")
    if traceparent is None:
        return None
    return TraceContext(traceparent, carrier.get("tracestate"))
