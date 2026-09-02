# ADR-004 — Durable Workflow Engine

## Status
Proposed

## Decision
Use a durable workflow engine for long-running, retryable, approval-blocking, and scheduled workflows when application-level durability requirements justify it. Temporal is the preferred candidate but is not yet mandatory.

ADR status remains Proposed until a Phase 0 spike demonstrates:

- pause and durable resume for human approval
- long-running media-generation polling
- durable scheduled publishing
- crash/restart recovery
- retry, timeout, and non-retryable error behavior
- acceptable local-development and operational complexity

Simple synchronous operations do not use Temporal merely because it is present.

## Alternatives
- ad-hoc queues + cron
- LLM loop orchestration
- application DB polling only
- PostgreSQL-backed jobs plus transactional outbox

## Rationale
Creative production, approvals, scheduled publishing, external generation, and metric collection can span hours or days and must survive crashes. A spike is required because workflow code and operations acquire real Temporal coupling that should not be hidden behind a superficial abstraction.

## Consequences
If adopted, Temporal workflow code must obey determinism constraints, activities must be idempotent, and SDK types remain outside domain contracts. If the spike does not justify adoption, Phase 0 will retain PostgreSQL outbox/inbox and a smaller job mechanism until a long-lived workflow requires more.
