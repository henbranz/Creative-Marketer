# 06 — Event Model

## Why Events

Agents must not form a brittle call chain. Domain events create loose coupling, replayability, auditability, and future extensibility.

## Event Envelope

Every event should include:

```json
{
  "event_id": "uuid",
  "event_type": "marketing.post.published.v1",
  "schema_version": 1,
  "tenant_id": "uuid",
  "aggregate_type": "publication",
  "aggregate_id": "uuid",
  "occurred_at": "ISO-8601",
  "actor": {
    "type": "agent|user|system|integration",
    "id": "..."
  },
  "correlation_id": "uuid",
  "causation_id": "uuid|null",
  "payload": {}
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
- `governance.tool.denied.v1`

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
