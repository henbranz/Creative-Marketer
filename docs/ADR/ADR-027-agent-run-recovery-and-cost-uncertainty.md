# ADR-027: AgentRun Recovery and Cost Uncertainty

## Status

Accepted

## Context

A worker can stop before provider I/O, during an ambiguous provider call, after receiving a response,
or after recording response metadata but before completing local validation. A lease proves only
that the worker has stopped reporting progress. Treating expiry as permission to retry could duplicate
a billed inference, lose provenance, or understate tenant budget use.

## Decision

Every claimed AgentRun creates one durable `ModelAttempt`. Its immutable route, pricing, workload,
claim, and lease identify the logical call. The worker commits `PROVIDER_STARTED` immediately before
provider I/O and commits safe response identity, normalized usage, and calculated cost as
`RESPONSE_RECORDED` before local validation. Prompt, evidence text, and raw response are never stored
on the attempt.

An expired attempt derives one classification: `SAFE_BEFORE_PROVIDER`,
`PROVIDER_OUTCOME_UNKNOWN`, or `RESPONSE_RECORDED`. Expiry never changes the AgentRun and never starts
provider I/O. A trusted deployment-configured operator may explicitly abandon it or rerun as new.
Rerun atomically closes the predecessor, reserves a fresh budget in the recovery time's policy period, creates one lineage-linked
successor with the predecessor's frozen configuration and context, records Audit, and emits the
ordinary requested event. Disabled definitions or unavailable historical route/pricing block rerun.

Safe-before-provider abandonment releases the reservation. A recorded response settles its exact
calculated cost. Ambiguous provider outcome transfers the full reservation to `unknown_cost`, which
continues to count against the period ceiling. An append-only, one-time reconciliation may later move
that amount to authoritative actual cost. Recovery is CLI-only; no browser or public HTTP endpoint
holds this authority. Exact run/attempt/workload predicates and row locking reject late worker writes
and concurrent recovery losers.

## Consequences

The system favors duplicate-inference prevention and conservative accounting over availability.
Operators may need provider-console evidence before choosing an action, and a tenant may remain budget
blocked while cost is unknown. A recovery rerun is visibly a new billable run, not a continuation.
Exactly-once provider billing remains impossible without provider-supported idempotency, but every
local decision and uncertainty is durable, tenant-isolated, and auditable.
