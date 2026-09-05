# ADR-023 — Observability and Telemetry Boundary

## Status

Accepted

## Decision

OpenTelemetry is the vendor-neutral standard for operational traces and metrics. Audit records,
Domain Events, operational telemetry, application logs, and future business analytics remain
separate systems with separate authority, retention, and privacy rules. Telemetry is diagnostic
and never authorizes work or replaces durable state.

Instrumentation is explicit at API, application-service, Tool Gateway, executor, persistence,
publisher, and consumer boundaries. Domain modules do not import OpenTelemetry or logging SDKs.
Production export uses bounded asynchronous processors and optional OTLP/HTTP; exporter failure is
fail-open for business execution. Invalid startup configuration still fails fast.

Telemetry fields are allow-listed, bounded, and secret/PII-minimized. Raw Tool inputs, outputs,
prompts, provider responses, request bodies, query strings, headers, and credentials are not
recorded. Metric dimensions are restricted to reviewed bounded values and never include tenant,
user, ToolCall, operation, event, correlation, or trace identifiers. No tenant/actor fingerprint is
emitted in Phase 0; if later required, it will use a separate deployment-secret HMAC key.

W3C `traceparent` and bounded `tracestate` are captured when an Outbox row is created and are
immutable delivery metadata. They are excluded from Event Canonical JSON V1 and `event_digest`.
They carry no tenant, actor, permission, approval, idempotency, or operation authority. Publisher
and consumer retries may create multiple spans while Inbox still controls business deduplication.

## Consequences

- `trace_id` and the business `correlation_id` remain distinct.
- the API does not accept browser trace context in Phase 0, so a caller cannot force sampling;
  local root sampling is bounded by `OTEL_TRACE_SAMPLE_RATIO`
- service resources include name, version, deployment environment, and instance ID
- API, publisher, and consumer deployments use distinct service names when composed
- liveness never depends on PostgreSQL or a collector; readiness depends on PostgreSQL but not OTel
- durable unknown Tool outcomes and terminal Outbox rows remain the source of truth; telemetry only
  signals them
- no observability database, dashboard vendor, alert router, Sentry stack, or customer SLO is
  selected by this ADR

Candidate future alerts are unknown external outcomes, terminal Outbox failures, sustained Outbox
backlog/age, elevated Tool failure rates, and database readiness failures. Candidate SLIs are API
success/latency, Gateway decision latency, Tool success, publication delay, processing delay, and
unknown-outcome frequency.
