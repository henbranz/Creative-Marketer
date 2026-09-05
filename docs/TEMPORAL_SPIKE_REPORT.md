# Temporal Adoption Spike Report

## Decision

**ACCEPTED for bounded durable orchestration.** Temporal materially reduces the amount of custom
state-machine, timer, retry, recovery, and replay infrastructure required for Creative Marketer's
approval, generation, and publication workflows. It does not replace PostgreSQL, Tool Gateway,
Approval, Permission, Idempotency, Audit, Outbox, or Inbox.

Production workflow activation remains blocked until deployment-specific workload authentication
and an authoritative durable request resolver exist. Accepting the engine is not authorization to
ship a worker that impersonates the initiating human.

## Implemented spike architecture

```text
Temporal Workflow (safe references only)
        ↓ Activity
trusted workload/request resolver (production dependency)
        ↓
application service / existing Tool Gateway
        ↓
Permission + Approval + Idempotency + Audit + Outbox
```

Temporal-specific code is isolated in `creative_marketer.infrastructure.temporal`. Pure input and
result contracts live in `creative_marketer.workflow_orchestration`; they contain no SDK types.
The worker factory is independent of FastAPI and accepts composed application Activities.

Initial topology is one namespace per environment, not tenant, with one
`creative-marketer-workflows` queue for the Phase-0 worker. A separate
`creative-marketer-tool-activities` capability queue is the first justified split when connector
execution becomes a distinct trust/scale zone. Do not create queues per tenant or per Agent. Stable IDs are
`tenant/<tenant UUID>/operation/<op_UUID>` and
`tenant/<tenant UUID>/generation/<op_UUID>`. IDs locate orchestration; they confer no authority.

## Scenarios and evidence

| Scenario | Result |
| --- | --- |
| Approval wait/resume | PASS: signal wakes the workflow; the Activity/Gateway rechecks current DB-owned approval state. |
| Forged/duplicate signal | PASS: a signal without approval causes no effect; duplicate signals cause one effect. |
| Approval expiry | PASS: durable timeout followed by authoritative recheck ends `EXPIRED`. |
| Approval worker restart | PASS: the first worker was shut down and a new worker resumed the same execution. |
| Generation polling | PASS: start Activity, durable timers, PENDING/PROCESSING/READY polling. |
| Generation restart | PASS: polling resumed after worker recreation; one logical provider job/start remained. |
| Transient poll failure | PASS: bounded Activity retry recovered; pending was never modeled as an exception. |
| Terminal generation failure | PASS: `CONTENT_REJECTED` became a non-retryable ApplicationError and stopped after one poll. |
| Generation deadline | PASS: bounded workflow deadline ended `EXPIRED`. |
| Scheduled publication | PASS: a one-off Workflow timer fired and the Tool Gateway Activity executed. |
| Revoked approval / policy DENY | PASS: both were rechecked at fire time and produced zero publications. |
| Lost Tool Activity response | PASS against the existing R4 fake Tool Gateway: retry reused the operation ID, returned gateway replay, and side-effect count stayed one. |
| Cancellation | PASS while waiting for approval and generation; no claim is made that cancellation reverses an external effect. |
| Replay | PASS using the SDK Replayer against representative branching/timer/retry history. |
| History privacy | PASS: decoded payload inspection found only bounded IDs/references/status; forbidden credential, header, prompt, PII, raw Tool and provider fields were absent. |

The official Python SDK time-skipping server made hour-scale waits deterministic in seconds.
Temporal service process restart was not asserted: the SDK test server is intentionally ephemeral
and in-memory. Worker restart is directly tested. A persistent local dev-server volume is provided
for manual service-restart verification; production persistence/failover remains deployment work.

## Retry and Activity design

No global retry policy is defined:

| Activity class | StartToClose | ScheduleToClose | Initial/backoff | Attempts |
| --- | ---: | ---: | --- | ---: |
| Tool Gateway | 60 s | 5 min | 1 s / 2x / max 10 s | 3 |
| Generation start/poll | 30 s | 3 min | 500 ms / 2x / max 10 s | 5 |
| Short state lookup (reserved) | 15 s | use-case-specific | 250 ms / 2x / max 5 s | 5 |

Provider `PENDING` and `PROCESSING` are ordinary results. Stable terminal provider failures are
explicitly non-retryable. Long jobs use start/poll Activities with Workflow timers; heartbeats are
reserved for bounded but lengthy Activities where progress/checkpoint or prompt cancellation is
useful (large transfer/transcode), not a 45-minute provider wait.

The same platform `operation_id` is passed on every retry. Temporal retry alone never supplies
exactly-once external effects. The Tool Gateway/idempotency record owns replay and unknown-outcome
safety. If the effect occurred but an Activity response is lost, retry returns the existing result;
if the gateway persisted `UNKNOWN_EXTERNAL_OUTCOME`, automatic retry remains blocked for
reconciliation.

## Approval event bridge and dual durability

The chosen bridge uses the existing `ProcessEvent` ordering:

1. validate event and reserve Inbox receipt inside its tenant transaction;
2. resolve approval-to-workflow association from authoritative application state;
3. issue the idempotent Temporal wake-up signal;
4. commit Inbox only after the RPC succeeds.

A five-minute default durable Workflow fallback recheck bounds wake-up latency if an operator must
recover an exceptional bridge failure. It is not a one-second database poll and Approval remains
authoritative. Production should make the resolver use the same consumer transaction where
practical. If holding a transaction across the RPC proves operationally unacceptable, replace the
handler with a dedicated PostgreSQL Temporal-signal outbox; do not reverse the ordering into a
best-effort post-commit signal.

| Failure | Outcome and recovery |
| --- | --- |
| DB state/outbox commits; Temporal unavailable | Event remains in Outbox/transport retry. No Inbox receipt exists yet. |
| Signal RPC fails before Inbox commit | Handler raises; Inbox transaction rolls back; delivery retries. |
| Signal succeeds; Inbox commit fails | Event redelivers and sends a duplicate signal; workflow signal counter and Gateway idempotency make it safe. |
| Consumer crashes before signal | Inbox transaction rolls back; event redelivers. |
| Consumer crashes after signal, before commit | Duplicate signal on redelivery; safe. |
| Duplicate source event | Existing Inbox returns `ALREADY_PROCESSED`; no second handler call. |
| Signal is forged or targets wrong tenant | Locator/application validation fails or signal only wakes; Gateway still re-resolves tenant, actor, policy, and approval. |

Temporal `update-with-start` is useful for atomic Temporal-side creation/update but cannot make a
PostgreSQL transaction atomic with Temporal. It is not the selected bridge mechanism.

## Scheduling experiment

One-off Workflow timers are selected for scheduled social publication because the publication is a
single logical operation with existing approval, cancellation, and result state. Rescheduling is a
workflow update/signal concern and cannot overlap itself. Temporal Schedules are better for
recurring cadence (for example metrics collection), provide pause/backfill/overlap policies and
calendar visibility, but add a second named resource and API lifecycle. If used later, choose the
conservative skip/buffer-one overlap behavior; never overlap publication attempts for one logical
schedule.

## State, security, and privacy

PostgreSQL owns membership, tenant relationships, Agent/Tool/Permission versions, Approval,
ToolCall/idempotency outcome, Audit, Outbox, and product facts. Temporal owns only current workflow
progress and history. Query status such as `WAITING_APPROVAL`, `GENERATING`, or `SCHEDULED` is UI
diagnostic state, never the product record.

Workflow input tenant ID is a locator. Activities must resolve a current authenticated workload,
then load the tenant-scoped request and construct trusted context through application code. They
must compare tenant, Agent, operation, and Tool against the locator before calling the Gateway.
The spike's adapter fails closed on mismatch and never constructs a User from workflow input.

History contains UUIDs, operation IDs, tool key, opaque internal request/job/result references,
bounded timings, states, and stable error codes. It excludes raw Tool input/output, credentials,
headers, provider bodies, prompts, email, phone, and customer data. A custom payload codec is
deferred. Before production, choose server-side encryption and assess a client payload codec if
infrastructure operators must not read even these references. Search Attributes, if added, are
limited to workflow type/state/risk class; tenant, user, product, prompt, and operation IDs are not
metric labels or Search Attributes.

## Observability

The Temporal client uses the SDK's OpenTelemetry interceptor, inheriting the configured global OTel
provider. Activities add explicit spans around generation and Tool Gateway calls while preserving
the business `correlation_id`. Workflow ID/run ID may occur on sampled trace spans; they are not
metric dimensions. Bounded `activity.retries` is emitted. Temporal's native workflow/activity/task
metrics should feed the same collector; duplicating its workflow completion counters in business
code is unnecessary. Audit and events remain authoritative.

## Local development and CI

`temporalio==1.32.0` adds roughly 13 MiB plus transitive RPC/protobuf packages. `make temporal-up`
starts the official lightweight CLI dev server under an optional Compose profile with persisted
SQLite state and UI; `make temporal-down` stops it. Normal `make dev-up`, API, web, and non-Temporal
tests do not require Temporal. `make temporal-test` uses the official ephemeral time-skipping test
server. CI isolates these tests in a dedicated job and retains replay as an architecture gate.

The operational cost is one additional service/namespace, an independent worker deployment,
capability task queues, SDK/server compatibility management, deterministic-code review, replay
history curation, visibility/retention policy, worker build/version rollout, and monitoring. Cloud
versus self-hosting is deferred; no cloud account or credentials were created.

## Temporal versus PostgreSQL jobs plus Outbox

| Capability | Temporal | PostgreSQL jobs + Outbox | Decision |
| --- | --- | --- | --- |
| Durable wait/timer | Native history/timer | Job rows plus scheduler/leases | Temporal for long waits |
| Approval wait | Signal + durable fallback | State polling/notifications | Temporal |
| Multi-step state | Workflow code/history | Explicit transition tables | Temporal for bounded workflows |
| Activity retry | Per-Activity policy/timeouts | Custom attempts/backoff/leases | Temporal |
| Crash recovery | Replay from history | Lease expiry and state reconstruction | Temporal |
| Replay/determinism | Native Replayer | Custom transition replay tests | Temporal |
| One-off schedule | Workflow timer | `run_at` job | Temporal when part of workflow; PostgreSQL is adequate alone |
| Recurring schedule | Temporal Schedule | cron + jobs | Revisit per use case |
| Domain fact publication | Not transactional with business DB | Existing transactional Outbox | PostgreSQL Outbox |
| Business queries/reporting | Poor authority fit | Native relational model | PostgreSQL |
| Dual-write risk | DB/Temporal bridge required | Lower when all DB-local | PostgreSQL simpler |
| Operations/developer load | New server, worker, determinism/replay model | More custom scheduler/state code | Temporal cost justified once long workflows ship |

PostgreSQL jobs would be cheaper for a single `run_at` task. Across approval, generation, QA,
publication, cancellation, deadlines, and retries, they require a custom workflow engine: job and
transition schemas, leases, timer scans, recovery rules, version migration, visibility, and replay
tooling. Temporal provides those mechanics while leaving business truth in PostgreSQL.

## Workflow evolution convention

Workflow code is immutable in behavior for histories already in production. Compatible additive
changes are replay-tested. A branch/timer/Activity ordering change uses the SDK patch/versioning
mechanism with old and new branches retained until old histories finish, or introduces a new
workflow type/version. Representative redacted histories are checked into or securely fetched by
CI. Worker build/version routing should be adopted before multiple incompatible worker builds are
live; advanced deployment configuration is intentionally not part of this spike.

Cancellation is cooperative and appropriate for user-requested stop while waiting/polling.
Termination skips workflow cleanup and is reserved for audited operator recovery. Neither reverses
a provider effect already owned by the Tool Gateway.

## Known risks and required follow-up

- Implement and authenticate production workload identity; never retain or reconstruct the human
  session for a days-long workflow.
- Persist authoritative request references and approval-to-workflow association without storing raw
  payloads in Temporal.
- Load-test Inbox transactions that include a bounded Temporal RPC; adopt a signal outbox if needed.
- Establish namespace retention, archival, visibility, capacity, backup/DR, and server ownership.
- Decide self-hosted versus managed Temporal and payload-encryption policy.
- Curate representative histories and worker deployment/version rollback runbooks.
- Verify persistent Temporal service restart/failover in deployment integration tests.
- Add production dashboards/alerts using native Temporal and existing bounded application metrics.

These are production hardening dependencies, not reasons to recreate durable orchestration in
PostgreSQL. The architecture is ready to continue Phase-0 architecture/security hardening; it is
not yet ready to activate real scheduled/background side effects.
