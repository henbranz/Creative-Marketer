# ADR-056 — Confirmed provider failure retry preserves immutable attempt history

Status: Accepted

## Context

A ModelArk task can return an authoritative terminal failure after its task identity has been
persisted. This is materially different from a lost start response: the prior task is known, its
terminal state is known, and another request cannot accidentally resume it. The earlier generic
`RECOVER_MEDIA_FAILURE` projection explained the failure but offered no executable governed
transition. Clearing the task ID without retaining evidence would destroy provenance, while creating
a replacement GenerationJob or reservation would split one approved media intent and double-count
its budget authority.

Provider failures must also remain classified conservatively. Authentication rejection and
authoritative request rejection happen before a task exists; transport or 5xx failure during start
can have an unknown external outcome. Neither is proof of a terminal failed provider task.

## Decision

- The resolver exposes `RETRY_FAILED_MEDIA` only when every selected Job is `FAILED`, has an
  allowlisted confirmed-provider failure code, retains a provider operation reference, has no output
  Asset, and its ProductionPlan workflow is terminal or missing.
- The action requires the exact phrase `I_APPROVE_RETRY_FAILED_MEDIA`. It revalidates tenant and
  Product authority, the approved Plan and Creative lineage, rights, exact route/model/pricing,
  reservation integrity, and the Product spend cap before changing state.
- The spend-cap minimum is cumulative committed Product spend plus the exact reservation required by
  the next attempt. Historical actual and unknown exposure remain committed; neither is erased or
  offset by the existing reservation row.
- The transition reuses the same GenerationJob and reservation. In one transaction it inserts an
  immutable `generation_provider_attempts` record containing bounded terminal facts, then returns the
  Job to `READY`, clears only its active provider operation pointer and execution identities, and
  emits `production.media.failed_retry_requested.v1` through the transactional Outbox.
- Provider-attempt history is append-only, forced-RLS tenant isolated, and unavailable for UPDATE or
  DELETE to the runtime role. Events and audit metadata include only bounded IDs and codes—never the
  provider message, request body, output URL, or credential.
- The event consumer reloads the authoritative Job set and starts the recovery-only Temporal workflow
  with `ALLOW_DUPLICATE` and conflict `FAIL`. An active workflow blocks admission, and Temporal remains
  the final race guard against two concurrent executions.
- ModelArk response parsing accepts only bounded provider error codes. Known capacity, credit,
  account, and activation codes map deterministically; unknown terminal task codes remain the generic
  confirmed task-failure code. HTTP 401 is an authentication pre-effect failure. Ambiguous start
  transport/5xx outcomes remain `OUTCOME_UNKNOWN` and cannot use this action.

## Consequences

Operators can resume an approved media intent after a proven terminal provider failure without
losing task provenance, duplicating Jobs, or duplicating reservations. The retry is intentionally
not automatic: it creates a new possible charge and therefore needs current authorization, explicit
approval, and sufficient cap. The additional append-only table and recovery event increase storage
and workflow complexity, but make billing reconciliation and incident review truthful.
