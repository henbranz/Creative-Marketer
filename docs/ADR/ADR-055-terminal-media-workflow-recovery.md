# ADR-055 — Terminal media workflows require governed stranded-start recovery

Status: Accepted

## Context

`MediaProductionWorkflow` records an application failure in its query state and returns its bounded
job results normally. Temporal therefore correctly records the execution itself as `COMPLETED`.
PostgreSQL can simultaneously retain a `STARTING` GenerationJob when the worker stopped after
persisting the pre-provider boundary and before persisting a task identity. Treating every
`STARTING` row as active made the Product resolver wait forever after the workflow was terminal.

Absence of a provider task identity does not prove that the request never crossed the provider
boundary. Automatic retry would therefore risk duplicate paid generation.

## Decision

- PostgreSQL remains authoritative for GenerationJob state and Temporal remains authoritative for
  workflow execution state. The resolver consults both for exact `READY` jobs and for an exact
  `STARTING` Job with no provider operation, Asset, actual cost, or unknown cost.
- An active workflow resolves `WAIT_FOR_MEDIA`. A terminal or missing workflow resolves the
  executable, non-provider action `RECOVER_STRANDED_MEDIA_START`.
- That action atomically moves only the exact stranded Job to `OUTCOME_UNKNOWN`, records its full
  conservative reservation as unknown exposure, and writes an immutable audit event. It does not
  emit a workflow continuation, invoke Tool Gateway, create a Job, or create a reservation.
- A no-task/no-Asset unknown outcome exposes `ABANDON_UNKNOWN_MEDIA_AND_RETRY`. It requires the exact
  action-bound approval phrase, current Product/Research/Creative authority, rights, immutable route
  compatibility, pricing, reservation, and sufficient budget. The prior unknown exposure is
  preserved rather than erased.
- An approved ambiguity retry reuses the same GenerationJob and reservation and emits a dedicated
  transactional event. Its consumer uses a recovery-only Temporal start with `ALLOW_DUPLICATE` plus
  conflict `FAIL`; ordinary approval and spend-cap events retain `ALLOW_DUPLICATE_FAILED_ONLY`.
  Thus a prior terminal `COMPLETED` run may be followed, while two active workflows for one
  ProductionPlan remain forbidden.
- `READY` jobs are considered in progress only while the authoritative ProductionPlan workflow is
  active. `READY` plus a terminal or missing workflow resolves the free
  `RESTART_READY_MEDIA_WORKFLOW` action. It revalidates the approved Plan, immutable job identities,
  route/pricing/provenance/rights/reservations, and prior duplicate-risk acceptance before emitting
  `production.media.workflow_restart_requested.v1`; it changes no Job, reservation, approval, or
  spend value and performs no provider execution.
- The restart event binds the exact READY job IDs and is delivered through Outbox/Inbox. Its consumer
  reloads the complete persisted job set and uses the same recovery-only Temporal start. The event ID
  is the deterministic transition ID bound to the terminal Temporal execution reference, the
  ProductionPlan row serializes concurrent requests, and the Temporal conflict policy rejects a
  second active workflow. Repeated requests for the same terminal execution collapse to one event;
  a genuinely later terminal execution receives a distinct transition identity.
- A spend-cap denial on a READY retry that retains historical unknown exposure returns the bounded
  `LIVE_E2E_SPEND_CAP_REACHED_WITH_PRESERVED_UNKNOWN_COST` result. It does not attempt the ordinary
  zero-unknown-cost `BLOCKED_SPEND_CAP` transition and therefore cannot mask the real cap failure.
- Historical Seedance route identities remain immutable. Their compatible execution path continues
  through the ModelArk adapter described by ADR-054; no LAS endpoint or credential is restored.

## Consequences

The resolver can no longer report a terminal workflow as in-progress. Recovery is deliberately a
two-step operator flow: first establish the unknown historical outcome with zero external effect,
then separately accept duplicate-provider risk before any continuation can be admitted. Conservative
spend includes both retained unknown exposure and the retry reservation until the ambiguity is
reconciled externally.
An already-consumed recovery event can no longer strand READY jobs after its workflow terminates;
the resolver derives redispatch eligibility from PostgreSQL job state plus authoritative Temporal
state, never from age heuristics.
