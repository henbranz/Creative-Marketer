# 09 — Workflows

## Workflow Principles

A workflow is durable coordination. An agent is cognitive reasoning.

Do not use an LLM loop where a workflow/state machine is sufficient.

## Product Onboarding

```text
Create Product
   ↓
Upload/ingest product assets
   ↓
Validate required product fields
   ↓
Build Product Digital Twin
   ↓
Request initial Research Snapshot
```

TASK-001 implements the deterministic pre-research portion as Brand creation, Product/Profile
creation, sectional Brief completion, and explicit immutable Product Knowledge Snapshot creation.
Research request and asset ingestion remain later slices; neither is simulated by the current UI.

## Autonomous Creative Loop

```text
Goal / trigger
   ↓
Research freshness check
   ├─ valid → reuse
   └─ stale → Researcher refresh
   ↓
Creative Strategist creates concepts + hypotheses
   ↓
Approval policy
   ↓
Producer checks source assets
   ├─ missing → request user assets + WAIT
   └─ complete
   ↓
Generate media through provider adapter
   ↓
QA
   ↓
Approval policy
   ↓
Marketer creates experiment/publication schedule
   ↓
Publish through Tool Gateway
   ↓
Collect metrics at defined windows
   ↓
Join commerce/traffic data
   ↓
Intelligence proposes insights
   ↓
Validate / activate insights
   ↓
Next creative cycle
```

## Missing Asset Workflow

The Producer should not ask for a generic “upload more photos.”

It should produce precise requirements:
- view angle
- orientation
- minimum resolution
- background preference
- whether human interaction is needed
- reference use
- acceptable alternatives

## Publication Workflow

```text
Create draft
  ↓
Validate platform constraints
  ↓
Apply risk/approval policy
  ↓
Schedule durable timer
  ↓
At execution: revalidate token/account/content/status
  ↓
Publish idempotently
  ↓
Record external post ID
  ↓
Emit post.published
```

## Commerce Order Workflow

```text
Webhook
  ↓
Verify signature
  ↓
Deduplicate
  ↓
Normalize order
  ↓
Upsert internal order state
  ↓
Allocate inventory deterministically
  ↓
Determine production requirement
  ↓
Emit internal commerce events
  ↓
Sync optional Sheets/Excel operational view
```

## Research Refresh Workflow

Research is not performed before every action.

Refresh when:
- no valid snapshot exists
- TTL expired
- material anomaly/event
- user request
- scheduled cadence
- new market/product context

## Failure Handling

Every durable workflow should define:
- retryable errors
- non-retryable errors
- max attempts
- backoff
- compensation strategy if applicable
- user escalation
- dead-letter/manual intervention path

## Transactional event propagation

Completed facts cross context boundaries through PostgreSQL Outbox publication. Application state,
required Audit evidence, and the Outbox event commit together. Publishers claim short leased
batches and perform transport I/O outside database transactions. A crash after send can redeliver;
that is expected and safe because consumer state and its Inbox receipt commit together.

When a tenant consumer emits a new fact, the new event keeps the source correlation ID and uses the
source event ID as causation. It can join the handler state and Inbox receipt in the same local
transaction. No global event order is inferred from timestamps.

## Approval and Idempotency Preparation

Phase 0 now persists the safety state that a future Tool Gateway will consume:

```text
REQUIRES_APPROVAL PermissionDecision
  → trusted normalized input
  → shared ActionBindingV1 / action digest
  → immutable ApprovalRequest
  → immutable human decision or revocation
  → current authorization and exact-binding validation
  → idempotency reservation
  → one leased execution attempt
```

The Phase-0 gateway now enforces this control flow with fake executors in tests; production
provider execution remains deferred. `FAILED_PRE_EFFECT` may acquire a new attempt. A success is
replayed through a safe result reference. `UNKNOWN_EXTERNAL_OUTCOME`, including a crash after a
possible side effect, blocks retry until an active owner/admin records explicit reconciliation.
Lease expiry alone never proves that no external effect occurred.

## Operational trace continuity

An invocation span ends when approval is required; it is never held open while waiting for a
person. Resume creates a new Gateway invocation correlated by the durable operation and business
correlation IDs. Outbox creation captures W3C trace context separately from the immutable Domain
Event envelope. Publisher and consumer spans continue that context, while duplicate delivery may
legitimately create additional spans and Inbox remains the sole business deduplication authority.

## Governed Tool operation lifecycle

A low-risk allowed operation progresses `READY → EXECUTING → SUCCEEDED` (or a safe failure/unknown
outcome). Approval-required work first commits `AWAITING_APPROVAL` with the immutable ApprovalRequest,
Audit, and Outbox evidence. Resume reuses the same operation and approval; any changed payload or
binding conflicts. `FAILED_PRE_EFFECT` can retry the same operation. `UNKNOWN_EXTERNAL_OUTCOME`
cannot retry until deterministic reconciliation establishes whether an effect occurred. A confirmed
success replays its stored result reference without another executor invocation.

## Temporal orchestration boundary

Temporal is used only for bounded durable coordination: approval waits, external-job polling,
future execution, retry, cancellation, and crash recovery. Workflow code never imports database,
provider, Gateway, authentication, or HTTP adapters. Safe Activity inputs contain platform IDs and
opaque references; Activities re-resolve current trusted workload, tenant, Agent, Tool,
Permission, Approval, and idempotency state.

Approval events may wake a Workflow through an Inbox handler, but the signal carries no authority.
The handler signals before Inbox commit so RPC failure rolls back the receipt and commit failure
causes a safe duplicate. A bounded durable fallback recheck prevents permanent wait after an
exceptional bridge failure. One-off publication scheduling uses a Workflow timer; recurring
cadences may use Temporal Schedules after a use-case-specific overlap policy is selected.
