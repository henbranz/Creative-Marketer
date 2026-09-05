from dataclasses import dataclass
from typing import Literal

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics.export import (
    InMemoryMetricReader,
    MetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from creative_marketer.observability.runtime import ObservabilityRuntime


@dataclass(frozen=True, slots=True)
class ObservabilityConfiguration:
    mode: Literal["disabled", "development", "console", "otlp", "test", "in_memory"]
    service_name: str
    service_version: str
    environment: str
    instance_id: str
    otlp_endpoint: str | None = None
    sample_ratio: float = 1.0


def build_runtime(configuration: ObservabilityConfiguration) -> ObservabilityRuntime | None:
    if configuration.mode == "disabled":
        return None
    span_exporter: SpanExporter | None = None
    metric_reader: MetricReader | None = None
    if configuration.mode in {"development", "console"}:
        span_exporter = ConsoleSpanExporter()
    elif configuration.mode in {"test", "in_memory"}:
        span_exporter = InMemorySpanExporter()
        metric_reader = InMemoryMetricReader()
    elif configuration.mode == "otlp":
        if not configuration.otlp_endpoint:
            raise ValueError("OTEL_EXPORTER_OTLP_ENDPOINT is required in otlp mode")
        span_exporter = OTLPSpanExporter(
            endpoint=f"{configuration.otlp_endpoint.rstrip('/')}/v1/traces"
        )
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=f"{configuration.otlp_endpoint.rstrip('/')}/v1/metrics"),
            export_interval_millis=30_000,
        )
    return ObservabilityRuntime.create(
        service_name=configuration.service_name,
        service_version=configuration.service_version,
        environment=configuration.environment,
        instance_id=configuration.instance_id,
        span_exporter=span_exporter,
        metric_reader=metric_reader,
        sample_ratio=configuration.sample_ratio,
    )
