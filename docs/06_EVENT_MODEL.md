# 06 — Event Model

## Why Events

Agents must not form a brittle call chain. Domain events create loose coupling, replayability, auditability, and future extensibility.

Events are not a replacement for ordinary application calls. Deterministic workflows may synchronously invoke an Agent Runtime, validate and persist the result, and then record the resulting facts. Agents still do not call other agents directly.

## Commands Versus Events

- A command requests work and may be rejected.
- An event records a fact that already occurred.
- Event consumers must not infer that an event is an imperative command.
- Not every internal function call or state transition requires asynchronous messaging.
- Cross-context event propagation uses transactional publication.

## Event Envelope

Every event uses the implemented immutable canonical envelope:

```json
{
  "event_id": "uuid",
  "event_type": "marketing.post.published.v1",
  "schema_version": 1,
  "scope_kind": "tenant|platform",
  "tenant_id": "uuid|null",
  "aggregate_type": "publication",
  "aggregate_id": "uuid",
  "occurred_at": "ISO-8601",
  "actor_kind": "agent|user|system|workload",
  "actor_id": "uuid",
  "agent_definition_id": "uuid|null",
  "agent_version_id": "uuid|null",
  "agent_run_id": "uuid|null",
  "correlation_id": "uuid",
  "causation_id": "uuid|null",
  "payload": {},
  "payload_schema_digest": "sha256:...",
  "event_digest": "sha256:..."
}
```

## Core Event Families

### Catalog
- `catalog.product.created.v1`
- `catalog.product.updated.v1`
- `catalog.asset.added.v1`

### Research
- `research.snapshot.requested.v1`
- `research.snapshot.created.v1`
- `research.snapshot.expired.v1`
- `research.trend.detected.v1`

### Creative
- `creative.concept.requested.v1`
- `creative.concept.created.v1`
- `creative.concept.approved.v1`
- `creative.concept.rejected.v1`

### Production
- `production.plan.created.v1`
- `production.asset.required.v1`
- `production.generation.started.v1`
- `production.asset.ready.v1`
- `production.asset.failed.v1`
- `production.asset.approved.v1`

### Marketing
- `marketing.experiment.created.v1`
- `marketing.publication.scheduled.v1`
- `marketing.post.published.v1`
- `marketing.post.failed.v1`
- `marketing.metrics.observed.v1`

### Commerce
- `commerce.order.created.v1`
- `commerce.order.updated.v1`
- `commerce.inventory.changed.v1`
- `commerce.fulfillment.created.v1`
- `commerce.shipment.sent.v1`
- `commerce.refund.requested.v1`
- `commerce.refund.approved.v1`

### Intelligence
- `intelligence.insight.proposed.v1`
- `intelligence.insight.validated.v1`
- `intelligence.insight.expired.v1`
- `intelligence.insight.invalidated.v1`

### Governance
- `governance.approval.requested.v1`
- `governance.approval.granted.v1`
- `governance.approval.denied.v1`
- `governance.approval.revoked.v1`
- `governance.tool.denied.v1`

Only the four Approval event schemas are registered as production contracts in TASK-009. The
other families above are roadmap names, not currently accepted contracts.

## Rules

1. Events are immutable facts.
2. Commands are not events.
3. Event names use past tense for facts.
4. Schemas are versioned.
5. Consumers must be idempotent.
6. Duplicate delivery is expected.
7. Event ordering should not be globally assumed.
8. Sensitive payloads must be minimized; prefer references.
9. PII should not be broadcast on the general event bus.
10. Correlation and causation IDs are required for traceability.
11. Tenant, actor, agent-version, and run identity are supplied by trusted runtime state, never accepted from a model-produced payload as authoritative.

## Transactional Publication and Consumption

PostgreSQL is the initial durability mechanism:

1. an application use case writes aggregate state and an outbox record in one database transaction
2. a publisher delivers the outbox record at least once
3. each consumer records `(consumer_name, event_id)` in an inbox or equivalent idempotency store
4. consumer state change and inbox acknowledgement commit atomically
5. retries, poison messages, and terminal failures remain observable

Exactly-once delivery is not assumed. Event payloads minimize PII and secrets and prefer stable references.

Implemented invariant:

```text
Business State + Audit + Outbox → one atomic commit
Transport                       → at least once
Consumer State + Inbox          → one atomic commit
```

Payload contracts are self-contained JSON Schema 2020-12 with `additionalProperties: false`.
Unknown types, remote/relative `$ref`, version-suffix mismatch, malformed payloads, and local
schema-digest mismatch fail closed. Event Canonical JSON V1 is a documented strict portable JSON
subset (null, booleans, safe integers, UTF-8 strings, arrays, and string-keyed objects), serialized
with sorted keys and compact separators. Payloads are limited to 16 KiB canonical bytes and reject
credential- and PII-shaped fields/values before persistence.

The Outbox state machine is `PENDING → PUBLISHING → PUBLISHED`, with retryable failure returning
to `PENDING` and exhausted/permanent failure entering `FAILED_TERMINAL`. Publisher leases permit
recovery and intentional duplicate delivery. Inbox identity is `(consumer_name, event_id)`;
handler version is stored only for traceability. Same ID with a changed event digest is corruption,
not a duplicate.

## Example Flow

```text
creative.concept.approved.v1
        ↓
Production workflow starts
        ↓
production.plan.created.v1
        ↓
production.asset.required.v1   (if source assets missing)
        ↓
production.generation.started.v1
        ↓
production.asset.ready.v1
        ↓
marketing.publication.scheduled.v1
        ↓
marketing.post.published.v1
        ↓
marketing.metrics.observed.v1
        ↓
intelligence.insight.proposed.v1
```
