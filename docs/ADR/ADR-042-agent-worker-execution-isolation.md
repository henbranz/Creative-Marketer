# ADR-042 — Agent Worker Execution Mode Is Enforced Before Claim

## Status

Accepted

## Context

Fake and OpenAI Agent workers historically polled the same capability task queue. The Docker fake
worker could therefore claim a `live-*` acceptance AgentRun, create provider-start provenance, and
leave a conservative unknown-cost recovery incident while appearing under the same local workload
identity as the operator worker. Temporal task-queue membership alone did not express execution-mode
eligibility.

## Decision

AgentRuntime applies a deterministic execution-admission policy to the locked authoritative
AgentRun before route resolution, claim, ModelAttempt creation, or provider authority. Fake workers
reject the governed `live-*` idempotency namespace. OpenAI workers retain the live path. A rejected
activity may be retried by Temporal, but the rejected worker performs no AgentRun or cost mutation.

The Docker fake Agent worker is available only through the explicit `fake-agent` Compose profile,
uses `local-fake-agent-worker`, and remains suitable for normal fake/demo runs. The operator-started
OpenAI worker defaults to `local-live-agent-worker` and rejects the known fake identity. Workload IDs
remain provenance rather than authorization.

Cost reconciliation remains append-only. Operational reads derive original unknown cost,
reconciled actual cost, and remaining unknown cost from the immutable ModelAttempt plus its optional
immutable reconciliation row.

## Consequences

Accidental coexistence is less likely, and fake execution fails closed even if both worker modes
share a Temporal queue. Live admission currently depends on the governed `live-*` convention; any
future acceptance namespace must be added to the deterministic policy and regression tests. Queue
separation may still be adopted later, but is not the sole security boundary.
