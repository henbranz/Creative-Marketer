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

No tool is executed in this flow yet. `FAILED_PRE_EFFECT` may acquire a new attempt. A success is
replayed through a safe result reference. `UNKNOWN_EXTERNAL_OUTCOME`, including a crash after a
possible side effect, blocks retry until an active owner/admin records explicit reconciliation.
Lease expiry alone never proves that no external effect occurred.
