from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from threading import Thread
from time import monotonic
from typing import Any

from opentelemetry import metrics, propagate, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Status, StatusCode

from creative_marketer.observability.ports import NullSpan, OperationalSpan, SafeScalar
from creative_marketer.observability.safety import assert_metric_dimensions, safe_attributes


class _Span:
    def __init__(self, span: trace.Span) -> None:
        self._span = span

    def set_attribute(self, key: str, value: SafeScalar) -> None:
        for safe_key, safe_value in safe_attributes({key: value}).items():
            self._span.set_attribute(safe_key, safe_value)

    def record_error(self, code: str) -> None:
        self._span.set_status(Status(StatusCode.ERROR, code[:100]))
        self._span.set_attribute("error.code", code[:100])


class _SpanScope(AbstractContextManager[OperationalSpan]):
    def __init__(self, manager: AbstractContextManager[trace.Span]) -> None:
        self._manager = manager
        self._entered = False

    def __enter__(self) -> OperationalSpan:
        try:
            span = self._manager.__enter__()
            self._entered = True
            return _Span(span)
        except Exception:
            return NullSpan()

    def __exit__(self, *args: Any) -> bool | None:
        if not self._entered:
            return False
        try:
            return self._manager.__exit__(*args)
        except Exception:
            return False


@dataclass(slots=True)
class ObservabilityRuntime:
    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    span_exporter: SpanExporter | None = None
    metric_reader: MetricReader | None = None
    _tracer: trace.Tracer = field(init=False)
    _meter: metrics.Meter = field(init=False)
    _counters: dict[str, metrics.Counter] = field(default_factory=dict, init=False)
    _histograms: dict[str, metrics.Histogram] = field(default_factory=dict, init=False)
    _gauges: dict[str, Any] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._tracer = self.tracer_provider.get_tracer("creative_marketer", "0.1.0")
        self._meter = self.meter_provider.get_meter("creative_marketer", "0.1.0")

    @classmethod
    def create(
        cls,
        *,
        service_name: str,
        service_version: str,
        environment: str,
        instance_id: str,
        span_exporter: SpanExporter | None = None,
        metric_reader: MetricReader | None = None,
        sample_ratio: float = 1.0,
    ) -> "ObservabilityRuntime":
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.version": service_version,
                "deployment.environment": environment,
                "service.instance.id": instance_id,
            }
        )
        if not 0.0 <= sample_ratio <= 1.0:
            raise ValueError("trace sample ratio must be between zero and one")
        tracer_provider = TracerProvider(
            resource=resource, sampler=ParentBased(TraceIdRatioBased(sample_ratio))
        )
        if span_exporter is not None:
            tracer_provider.add_span_processor(
                BatchSpanProcessor(span_exporter, max_queue_size=2048)
            )
        readers = (metric_reader,) if metric_reader is not None else ()
        return cls(
            tracer_provider,
            MeterProvider(resource=resource, metric_readers=readers),
            span_exporter,
            metric_reader,
        )

    def span(
        self,
        name: str,
        attributes: Mapping[str, SafeScalar] | None = None,
        *,
        traceparent: str | None = None,
        tracestate: str | None = None,
    ) -> AbstractContextManager[OperationalSpan]:
        try:
            carrier = {"traceparent": traceparent or "", "tracestate": tracestate or ""}
            parent = propagate.extract(carrier) if traceparent else None
            manager = self._tracer.start_as_current_span(
                name,
                context=parent,
                attributes=safe_attributes(attributes),
                record_exception=False,
                set_status_on_exception=False,
            )
            return _SpanScope(manager)
        except Exception:
            from contextlib import nullcontext

            return nullcontext(NullSpan())

    def count(
        self, name: str, value: int = 1, attributes: Mapping[str, SafeScalar] | None = None
    ) -> None:
        try:
            assert_metric_dimensions(attributes)
            instrument = self._counters.get(name)
            if instrument is None:
                instrument = self._meter.create_counter(name)
                self._counters[name] = instrument
            instrument.add(value, safe_attributes(attributes, metrics=True))
        except Exception:
            pass

    def duration(
        self, name: str, seconds: float, attributes: Mapping[str, SafeScalar] | None = None
    ) -> None:
        try:
            assert_metric_dimensions(attributes)
            instrument = self._histograms.get(name)
            if instrument is None:
                instrument = self._meter.create_histogram(name, unit="s")
                self._histograms[name] = instrument
            instrument.record(seconds, safe_attributes(attributes, metrics=True))
        except Exception:
            pass

    def shutdown(self, timeout_millis: int = 5000) -> None:
        deadline = monotonic() + max(0, timeout_millis) / 1000
        actions = (
            self.tracer_provider.shutdown,
            lambda: self.meter_provider.shutdown(timeout_millis=timeout_millis),
        )
        for action in actions:
            worker = Thread(target=action, daemon=True)
            worker.start()
            worker.join(max(0.0, deadline - monotonic()))

    def gauge(
        self, name: str, value: int | float, attributes: Mapping[str, SafeScalar] | None = None
    ) -> None:
        try:
            assert_metric_dimensions(attributes)
            instrument = self._gauges.get(name)
            if instrument is None:
                instrument = self._meter.create_gauge(name)
                self._gauges[name] = instrument
            instrument.set(value, safe_attributes(attributes, metrics=True))
        except Exception:
            pass
