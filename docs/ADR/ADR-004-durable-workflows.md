# ADR-004 — Durable Workflow Engine

## Status
Proposed

## Decision
Use a durable workflow engine for long-running, retryable, approval-blocking, scheduled workflows. Temporal is the current preferred candidate.

## Alternatives
- ad-hoc queues + cron
- LLM loop orchestration
- application DB polling only

## Rationale
Creative production, approvals, scheduled publishing, external generation, and metric collection can span hours/days and must survive crashes.

## Consequences
Adds infrastructure/operational complexity; should be introduced with clear boundaries rather than for every short request.
