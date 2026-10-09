# ADR-053 — Authoritative pre-generation state resolver

Status: Accepted

## Context

Research, Creative, approval, and Producer continuation rules had accumulated in the Creative Cycle
reconciler and the local live-acceptance script. Recoverable failed runs could become terminal cycle
failures, while operators had to infer which UUID-specific command applied. That duplication could
drift and create normal governed states with no continuation.

## Decision

The orchestration application owns one typed, deterministic `PipelineStateResolver` for the path:

```text
Research -> Creative Strategist -> human Creative approval
         -> Producer -> human Production Plan approval
```

An RLS-scoped repository projects persisted AgentRuns, ModelAttempts, ResearchSnapshots,
CreativeConcept decisions/revalidations, ProductionPlans, and plan decisions into explicit Research,
Creative, and Producer states. Optional immutable IDs bind an operator session; they select records
but never become authority. The resolver returns exactly one stage/action plus a bounded blocking
reason and explicit cost, human-approval, and provider-permission flags.

The durable Creative Cycle delegates its pre-generation decisions to this resolver. Recoverable
failures no longer transition directly to terminal `FAILED`; they require the resolver's governed
operator recovery. HTTP and live tooling render the resolver result and do not reproduce transition
rules. `make live-e2e` is read-only and stops at `READY_FOR_GENERATION`; media execution remains a
separate approval-bound workflow.

`REQUIRES_RESTRATEGY` and a rejected Concept admit a new normal Creative Strategist run only through
current standard admission. The new run freezes an immutable `creative_restrategy` input reference
to the exact historical Concept/run and decision or revalidation trigger, and binds the latest current
ResearchSnapshot. The historical successful Creative run and all provider/cost records remain
unchanged.

Provider-outcome-unknown states never permit provider execution. They require reconciliation first.
Deterministic revalidation and human review actions are non-billable and do not permit a provider
call. Billed start/retry/restrategy actions require an explicit owner/admin action and retain normal
budget, provider-contract, idempotency, and provenance checks.

### Complete transition table

The table is exhaustive for the pre-generation boundary. “Evaluate next stage” means the resolver
continues in the same call; it is not an operator-visible dead end.

| Research state | Sole next transition |
| --- | --- |
| `NOT_STARTED` | `RUN_RESEARCH` |
| `PENDING`, `RUNNING` | `WAIT_FOR_RESEARCH` |
| `SUCCEEDED_CURRENT` | evaluate Creative |
| `SUCCEEDED_EXPIRED` | `REFRESH_RESEARCH` |
| `FAILED_BEFORE_PROVIDER`, `FAILED_NO_RESPONSE` | `RETRY_RESEARCH` through explicit recovery |
| `FAILED_RESPONSE` | `RERUN_RESEARCH` with current authority |
| `OUTCOME_UNKNOWN` | `RECONCILE_RESEARCH_OUTCOME`; provider execution denied |

| Creative state | Sole next transition |
| --- | --- |
| `NOT_STARTED` | `RUN_CREATIVE` |
| `PENDING`, `RUNNING` | `WAIT_FOR_CREATIVE` |
| `SUCCEEDED`, `APPROVAL_REQUIRED` | `APPROVE_CREATIVE` |
| `FAILED` | `RERUN_CREATIVE` with current Research |
| `OUTPUT_LIMITED` | `REPLACE_OUTPUT_LIMITED_CREATIVE` with predecessor lineage |
| `OUTCOME_UNKNOWN` | `RECONCILE_CREATIVE_OUTCOME`; provider execution denied |
| `REJECTED`, `REQUIRES_RESTRATEGY` | `RESTRATEGIZE_CREATIVE` with immutable Concept/trigger lineage |
| `STALE_RESEARCH` | `REVALIDATE_CREATIVE` deterministically |
| `APPROVED_FOR_PRODUCTION`, `REVALIDATED_FOR_PRODUCTION` | evaluate Producer |

| Producer state | Sole next transition |
| --- | --- |
| `NOT_STARTED` | `RUN_PRODUCER` |
| `PENDING`, `RUNNING` | `WAIT_FOR_PRODUCER` |
| `SUCCEEDED`, `PRODUCTION_PLAN_REVIEW_REQUIRED` | `REVIEW_PRODUCTION_PLAN` |
| `FAILED_NO_RESPONSE` | `RETRY_PRODUCER` through explicit recovery |
| `FAILED_RESPONSE`, `PRODUCTION_PLAN_INVALID`, `PRODUCTION_PLAN_REJECTED` | `RERUN_PRODUCER` from current approved authority |
| `OUTCOME_UNKNOWN` | `RECONCILE_PRODUCER_OUTCOME`; provider execution denied |
| `CONTRACT_UPGRADE_REQUIRED` | `UPGRADE_PRODUCER_CONTRACT` with predecessor lineage |
| `APPROVED_FOR_GENERATION` | `READY_FOR_GENERATION`; media remains out of scope |

Run locators and returned provenance IDs bind every recovery to the observed immutable history.
Recovery admission, replacement admission, restrategy admission, and normal stage admission each
re-resolve current tenant, Product, Research, approval, contract, budget, and idempotency authority;
the resolver response alone never grants execution authority.

## Consequences

Every expected pre-generation state has one tested continuation. Adding a new state requires adding
one resolver rule and test; delivery adapters cannot invent their own. The state endpoint is safe to
poll and exposes no Product prose, prompts, credentials, or provider response body. It does not claim
exactly-once provider execution; ambiguous external outcomes remain fail-closed.
