# ADR-004 — Durable Workflow Engine

## Status
Accepted

## Decision
Adopt Temporal as the durable orchestration engine for bounded workflows that wait for humans or
external jobs, schedule future work, or need crash-safe retry and resume. PostgreSQL remains the
source of business and governance truth. Outbox/Inbox remains the transactional fact-delivery
mechanism. Temporal owns only orchestration progress, durable timers, waits, Activity retry, and
workflow history.

The TASK-012 spike proved approval wait/resume, generation polling, scheduled fake publication,
actual worker recreation, retry/non-retryable behavior, deadlines, cancellation, replay, minimal
history payloads, and same-operation Tool Gateway replay after a lost Activity response. The
evidence and limitations are recorded in `docs/TEMPORAL_SPIKE_REPORT.md`.

Simple synchronous operations do not use Temporal merely because it is present. Production
workflow activation is gated on authenticated workload identity and a durable authoritative
request/approval-to-workflow resolver; the spike does not fabricate a long-lived User actor.

## Alternatives
- ad-hoc queues + cron
- LLM loop orchestration
- application DB polling only
- PostgreSQL-backed jobs plus transactional outbox

## Rationale
Creative production, approvals, scheduled publishing, external generation, and metric collection can span hours or days and must survive crashes. A spike is required because workflow code and operations acquire real Temporal coupling that should not be hidden behind a superficial abstraction.

## Consequences
Temporal workflow code obeys determinism constraints, Activities rely on application idempotency,
and SDK types remain outside domain/application contracts. One-off publication scheduling uses a
Workflow timer; recurring platform schedules may use Temporal Schedules later. Namespace is shared
per environment, task queues are capability-based rather than tenant/Agent-specific, and worker
deployment remains separate from FastAPI.

Representative histories must replay in CI before workflow changes deploy. In-flight workflow
changes use Temporal patching/versioning APIs or a new workflow type; incompatible code is never
silently substituted. Cancellation requests cooperative cleanup but cannot undo an external
effect. Termination is an exceptional operator action and is not exposed to ordinary users or
Agents.
