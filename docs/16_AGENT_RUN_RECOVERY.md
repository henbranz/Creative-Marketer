# AgentRun Recovery and Reconciliation

## Safety invariant

An expired lease means only that the owning worker is no longer trusted to finish. It does not prove
that provider inference was never attempted. No poller, Temporal retry, API request, or lease scanner
may reclaim a stranded run or invoke the provider again.

## Attempt classification

| Durable attempt state | Recovery classification | Cost treatment on close |
| --- | --- | --- |
| `CLAIMED` | `SAFE_BEFORE_PROVIDER` | release reserved cost |
| `PROVIDER_STARTED` or `UNKNOWN` | `PROVIDER_OUTCOME_UNKNOWN` | move full reservation to unknown cost |
| `RESPONSE_RECORDED` | `RESPONSE_RECORDED` | settle recorded calculated cost as actual |

The runtime writes `PROVIDER_STARTED` in a committed transaction immediately before provider I/O.
After a normalized response returns, it commits response ID, usage, and calculated cost before schema,
budget-envelope, and citation validation. Attempt storage contains no prompt, evidence, or model
output.

## Operator flow

Recovery requires both `AGENT_RECOVERY_TENANT_ID` and a deployment-issued
`AGENT_RECOVERY_OPERATOR_ID`. The configured tenant is the sole scope of each invocation. There is no
recovery HTTP endpoint.

```bash
AGENT_RECOVERY_TENANT_ID=<uuid> AGENT_RECOVERY_OPERATOR_ID=<workload> make agent-runs-stranded
AGENT_RECOVERY_TENANT_ID=<uuid> AGENT_RECOVERY_OPERATOR_ID=<workload> make agent-run-abandon RUN_ID=<uuid>
AGENT_RECOVERY_TENANT_ID=<uuid> AGENT_RECOVERY_OPERATOR_ID=<workload> make agent-run-rerun RUN_ID=<uuid>
AGENT_RECOVERY_TENANT_ID=<uuid> AGENT_RECOVERY_OPERATOR_ID=<workload> make agent-run-reconcile-cost RUN_ID=<uuid> ACTUAL_COST=0.012345 CURRENCY=USD
```

Inspect first. Use `abandon` when no replacement should be created. Use `rerun` only after accepting
that it is a fresh billable operation. The transaction closes the predecessor, reserves a new budget
in the current policy period,
creates a successor linked by `recovery_of_run_id`, appends Audit, and publishes the normal request
event. Only one successor can win. The exact historical AgentVersion, generic context
kind/digest/references, contract, route, pricing version, and limits remain frozen; disabled
definitions or an unavailable
historical route fail closed.

When provider evidence later establishes actual cost for an ambiguous run, `reconcile-cost` creates
immutable evidence exactly once and moves unknown cost to actual cost. It never changes the original
AgentRun outcome. Do not guess an amount to unblock a tenant.

## Race and privacy behavior

Recovery locks the authoritative run and attempt. A late worker must still match run, attempt,
workload, and state, so response and terminal writes lose after recovery. Concurrent reruns produce at
most one successor. All tables use forced RLS and composite tenant foreign keys. Audit, events,
telemetry, CLI output, and Temporal history expose only bounded identifiers, classifications, safe
route metadata, usage, and cost—never credentials or content.

Apply migration `20260912_0016` with Agent workers stopped. Any complete legacy `RUNNING` row is
backfilled as conservatively unknown; an incomplete legacy row aborts migration rather than becoming
retryable. Populated attempt/reconciliation/lineage evidence also blocks downgrade because dropping it
would erase billing and forensic history.
