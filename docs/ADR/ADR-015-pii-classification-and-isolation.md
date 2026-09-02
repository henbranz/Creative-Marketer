# ADR-015 — PII Classification and Isolation

## Status
Accepted

## Decision
Data is classified at least as public, tenant-confidential, personal data, sensitive personal/commerce data, credential material, or security/audit data. Collection and access follow purpose limitation and data minimization.

Customer contact and address data are isolated from general commerce state using dedicated tables/schema boundaries and purpose-specific application interfaces. Research, Creative, Producer, and Marketer agents receive no raw customer PII. Intelligence receives aggregated or de-identified data unless an explicitly approved use case requires otherwise.

PII access is authorized by tenant, actor, purpose, and field scope and is audited. Logs, traces, events, model inputs, exports, and test fixtures use redaction or synthetic data. Deletion and retention propagate to derived projections, object references, and retrieval indexes where applicable.

## Alternatives

- keep PII in ordinary order JSON payloads
- rely on prompts to tell agents not to reveal PII
- provide broad commerce records and redact only at presentation time
- defer classification until compliance work begins

## Rationale
Separating personal data early prevents unrelated agents, analytics, and observability systems from inheriting unnecessary access and reduces breach and deletion scope.

## Tradeoffs
Isolation complicates joins, support workflows, exports, deletion, and analytics. Exact legal retention and residency policies depend on target markets and contracts.

## Consequences

- raw provider payloads containing PII require restricted storage and retention
- general domain events carry references or de-identified fields, not raw PII
- production support access requires explicit audited elevation
- exact retention periods, regional residency, and cross-tenant aggregation remain deferred product/compliance decisions
