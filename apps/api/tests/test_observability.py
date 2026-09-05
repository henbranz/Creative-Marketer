# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

import asyncio
import json
import logging
import time
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from creative_marketer.events.application import TraceContext
from creative_marketer.infrastructure.database.event_delivery import PostgresOutboxWriter
from creative_marketer.observability.configuration import (
    ObservabilityConfiguration,
    build_runtime,
)
from creative_marketer.observability.logging import SafeJsonFormatter, correlation_scope
from creative_marketer.observability.runtime import ObservabilityRuntime
from creative_marketer.observability.safety import (
    REDACTED,
    assert_metric_dimensions,
    safe_attributes,
    safe_error,
    sanitize_metadata,
)
from creative_marketer.observability.tracing import capture_trace_context
from creative_marketer.permission_governance.domain import Decision
from creative_marketer.tool_execution.domain import (
    GatewayStatus,
    OutcomeUnknown,
    ToolInvocationRequest,
    TrustedAgentInvocation,
)
from creative_marketer_api.main import create_app
from tests.test_event_delivery import event
from tests.test_tool_gateway import AGENT_ID, FakeReadExecutor, context, gateway, permission, tool


def runtime():
    spans = InMemorySpanExporter()
    reader = InMemoryMetricReader()
    value = ObservabilityRuntime.create(
        service_name="creative-marketer-test",
        service_version="0.1.0",
        environment="test",
        instance_id="test-1",
        span_exporter=spans,
        metric_reader=reader,
    )
    return value, spans, reader


def test_safe_attributes_and_metric_allowlist() -> None:
    assert safe_attributes(
        {
            "tool.key": "fake.read",
            "authorization": "Bearer SECRET",
            "payload": {"email": "person@example.test"},
            "action": "sk-123456789",
            "unknown": "discard",
        }
    ) == {"tool.key": "fake.read", "action": REDACTED}
    assert safe_error(ValueError("Bearer SECRET")) == "ValueError"
    assert sanitize_metadata(
        {"nested": [{"password": "p", "value": "Bearer SECRET"}], "safe": "ok"}
    ) == {
        "nested": [{"password": REDACTED, "value": REDACTED}],
        "safe": "ok",
    }
    assert_metric_dimensions({"result": "ok", "tool.key": "fake.read"})
    with pytest.raises(ValueError, match="unbounded"):
        assert_metric_dimensions({"tenant_id": str(uuid4())})


def test_trace_context_is_strict_and_not_semantic_authority() -> None:
    valid = "00-" + "1" * 32 + "-" + "2" * 16 + "-01"
    assert TraceContext(valid, "vendor=value").traceparent == valid
    for invalid in ("bad", "00-" + "0" * 32 + "-" + "2" * 16 + "-01"):
        with pytest.raises(ValueError):
            TraceContext(invalid)
    with pytest.raises(ValueError):
        TraceContext(valid, "x" * 513)
    with pytest.raises(ValueError):
        TraceContext(valid, "bad\nstate")
    with pytest.raises(ValueError):
        TraceContext(valid, "bad state")
    with pytest.raises(ValueError):
        TraceContext(valid, "vendor=one,vendor=two")


def test_runtime_hierarchy_metrics_and_context_capture() -> None:
    value, exporter, reader = runtime()
    with value.span("tool_gateway.invoke", {"tool.key": "fake.read"}):
        context = capture_trace_context()
        assert context is not None
        with value.span("tool_gateway.executor") as child:
            child.set_attribute("result", "EXECUTED")
        value.count("tool_gateway.invocations", attributes={"result": "EXECUTED"})
        value.duration("tool_gateway.duration", 0.01, {"tool.key": "fake.read"})
        value.gauge("outbox.pending", 2)
        value.count("dropped.metric", attributes={"tenant_id": str(uuid4())})
    assert value.tracer_provider.force_flush()
    spans = exporter.get_finished_spans()
    root = next(item for item in spans if item.name == "tool_gateway.invoke")
    child_span = next(item for item in spans if item.name == "tool_gateway.executor")
    assert child_span.parent and child_span.parent.span_id == root.context.span_id
    assert root.resource.attributes["service.name"] == "creative-marketer-test"
    assert root.resource.attributes["deployment.environment"] == "test"
    assert root.resource.attributes["service.instance.id"] == "test-1"
    metric_names = {
        metric.name
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert metric_names == {"tool_gateway.invocations", "tool_gateway.duration", "outbox.pending"}
    value.shutdown()


def test_async_trace_context_continues_without_changing_event_semantics() -> None:
    value, exporter, _reader = runtime()
    domain_event = event()
    digest = domain_event.event_digest
    correlation_id = domain_event.correlation_id
    with value.span("event.produce"):
        carrier = capture_trace_context()
    assert carrier is not None
    with value.span(
        "event.consume",
        traceparent=carrier.traceparent,
        tracestate=carrier.tracestate,
    ):
        assert domain_event.event_digest == digest
        assert domain_event.correlation_id == correlation_id
    assert value.tracer_provider.force_flush()
    spans = exporter.get_finished_spans()
    producer = next(span for span in spans if span.name == "event.produce")
    consumer = next(span for span in spans if span.name == "event.consume")
    assert producer.context.trace_id == consumer.context.trace_id
    assert consumer.parent and consumer.parent.span_id == producer.context.span_id
    value.shutdown()


@pytest.mark.asyncio
async def test_outbox_persists_trace_as_nonsemantic_creation_metadata(monkeypatch) -> None:
    trace_context = TraceContext("00-" + "a" * 32 + "-" + "b" * 16 + "-01")

    class Session:
        statement = None

        async def execute(self, statement):
            self.statement = statement

    session = Session()
    monkeypatch.setattr(
        "creative_marketer.infrastructure.database.event_delivery.capture_trace_context",
        lambda: trace_context,
    )
    domain_event = event()
    digest = domain_event.event_digest
    await PostgresOutboxWriter(session).append(domain_event)
    assert session.statement is not None
    parameters = session.statement.compile().params
    assert parameters["traceparent"] == trace_context.traceparent
    assert parameters["tracestate"] is None
    assert domain_event.event_digest == digest


@pytest.mark.asyncio
async def test_tool_gateway_end_to_end_trace_has_safe_hierarchy() -> None:
    value, exporter, reader = runtime()
    ctx, candidate = context(), tool()
    service, _uows, executor = gateway(ctx, candidate, permission(ctx, candidate))
    service.telemetry = value
    raw_secret = "Bearer SECRET sk-123456789 person@example.test"
    invalid = await service.invoke(
        TrustedAgentInvocation(ctx, AGENT_ID),
        ToolInvocationRequest("fake.read", {"value": raw_secret, "prompt": raw_secret}),
    )
    assert invalid.status is GatewayStatus.INVALID_INPUT and executor.count == 0
    result = await service.invoke(
        TrustedAgentInvocation(ctx, AGENT_ID),
        ToolInvocationRequest("fake.read", {"value": "safe"}, "op_" + "9" * 32),
    )
    assert result.status is GatewayStatus.EXECUTED and executor.count == 1
    denied_service, _, denied_executor = gateway(
        ctx, candidate, permission(ctx, candidate, decision=Decision.DENY)
    )
    denied_service.telemetry = value
    denied = await denied_service.invoke(
        TrustedAgentInvocation(ctx, AGENT_ID),
        ToolInvocationRequest("fake.read", {"value": "safe"}),
    )
    assert denied.status is GatewayStatus.DENIED and denied_executor.count == 0
    unknown_executor = FakeReadExecutor([OutcomeUnknown()])
    unknown_service, unknown_uows, _ = gateway(
        ctx, candidate, permission(ctx, candidate), executor=unknown_executor
    )
    unknown_service.telemetry = value
    unknown = await unknown_service.invoke(
        TrustedAgentInvocation(ctx, AGENT_ID),
        ToolInvocationRequest("fake.read", {"value": "safe"}, "op_" + "8" * 32),
    )
    assert unknown.status is GatewayStatus.UNKNOWN_OUTCOME
    assert unknown_uows.state["calls"][unknown.tool_call_id].status.value == (
        "UNKNOWN_EXTERNAL_OUTCOME"
    )
    assert value.tracer_provider.force_flush()
    spans = exporter.get_finished_spans()
    root = [span for span in spans if span.name == "tool_gateway.invoke"][-1]
    names = {
        span.name
        for span in spans
        if span.parent is not None and span.parent.span_id == root.context.span_id
    }
    assert {
        "tool_gateway.resolve",
        "tool_gateway.input_validation",
        "tool_gateway.permission",
    } <= names
    assert "tool_gateway.executor" in {span.name for span in spans}
    exported = str([(span.name, span.attributes) for span in spans])
    assert raw_secret not in exported and "prompt" not in exported
    metric_data = reader.get_metrics_data()
    metric_names = {
        metric.name
        for resource in metric_data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert {"permission.decisions", "tool_gateway.unknown_outcomes"} <= metric_names
    assert all(
        "tenant" not in point.attributes
        and "operation_id" not in point.attributes
        and "tool_call_id" not in point.attributes
        for resource in metric_data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        for point in metric.data.data_points
    )
    value.shutdown()


def test_structured_logs_omit_messages_secrets_and_pii() -> None:
    value, _exporter, _reader = runtime()
    formatter = SafeJsonFormatter("service", "test")
    record = logging.LogRecord(
        "component", logging.ERROR, __file__, 1, "Bearer SECRET person@example.test", (), None
    )
    record.action = "request.failed"
    record.safe_fields = {"tool.key": "fake.read", "prompt": "private prompt"}
    with correlation_scope(uuid4()), value.span("request"):
        parsed = json.loads(formatter.format(record))
    assert parsed["tool.key"] == "fake.read"
    assert {"timestamp", "level", "service", "environment", "component", "action"} <= parsed.keys()
    assert "trace_id" in parsed and "span_id" in parsed and "correlation_id" in parsed
    assert "SECRET" not in json.dumps(parsed)
    assert "example.test" not in json.dumps(parsed)
    value.shutdown()


class FailingExporter(SpanExporter):
    def export(self, spans):
        return SpanExportResult.FAILURE

    def shutdown(self):
        pass


class SlowExporter(FailingExporter):
    def export(self, spans):
        time.sleep(0.3)
        return SpanExportResult.SUCCESS


def test_exporter_failure_is_fail_open() -> None:
    value, _, _ = runtime()
    value.tracer_provider.add_span_processor(SimpleSpanProcessor(FailingExporter()))
    ctx, candidate = context(), tool()
    service, uows, executor = gateway(ctx, candidate, permission(ctx, candidate))
    service.telemetry = value
    result = asyncio.run(
        service.invoke(
            TrustedAgentInvocation(ctx, AGENT_ID),
            ToolInvocationRequest("fake.read", {"value": "safe"}),
        )
    )
    assert result.status is GatewayStatus.EXECUTED and executor.count == 1
    assert len(uows.state["audits"]) > 0 and len(uows.state["events"]) == 1
    value.shutdown()


def test_batch_exporter_slowness_does_not_block_operation() -> None:
    value = ObservabilityRuntime.create(
        service_name="test",
        service_version="1",
        environment="test",
        instance_id="test",
        span_exporter=SlowExporter(),
    )
    started = time.monotonic()
    with value.span("transaction.boundary"):
        completed = True
    elapsed = time.monotonic() - started
    assert completed and elapsed < 0.1
    shutdown_started = time.monotonic()
    value.shutdown(timeout_millis=10)
    assert time.monotonic() - shutdown_started < 0.1


def test_configuration_and_http_instrumentation(settings) -> None:
    assert (
        build_runtime(ObservabilityConfiguration("disabled", "service", "1", "test", "instance"))
        is None
    )
    with pytest.raises(ValueError, match="OTEL_EXPORTER"):
        build_runtime(ObservabilityConfiguration("otlp", "service", "1", "test", "instance"))
    in_memory = build_runtime(
        ObservabilityConfiguration("in_memory", "service", "1", "test", "instance")
    )
    assert in_memory is not None
    assert isinstance(in_memory.span_exporter, InMemorySpanExporter)
    assert isinstance(in_memory.metric_reader, InMemoryMetricReader)
    in_memory.shutdown()

    value, exporter, _reader = runtime()

    async def request():
        transport = ASGITransport(app=create_app(settings, observability=value))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/health/live?token=SECRET",
                headers={"X-Correlation-ID": "forged", "traceparent": "malformed"},
            )
        return response

    response = asyncio.run(request())
    assert response.status_code == 200
    assert response.headers["x-correlation-id"]
    assert response.headers["x-correlation-id"] != "forged"
    assert value.tracer_provider.force_flush()
    http_span = next(item for item in exporter.get_finished_spans() if item.name == "http.request")
    assert http_span.attributes["url.route"] == "/health/live"
    assert "SECRET" not in str(http_span.attributes)
    value.shutdown()
