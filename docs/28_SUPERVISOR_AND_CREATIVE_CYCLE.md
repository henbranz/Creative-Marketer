# Supervisor and Creative Cycle

## Purpose

`orchestration` coordinates one assisted creative learning iteration for one Product. It does not
replace Product Brain, Research, Creative, Production, Assembly, Publishing, Measurement, or
Intelligence. PostgreSQL is authoritative; Temporal only provides durable wakeups, timers, and
reconciliation retries.

```text
Product snapshot → Research → Concepts → Production → Final Creative
                 → Publish → Performance → Intelligence → Experiment decision
```

V1 permits one active cycle per Product and supports only `ASSISTED` mode. A cycle never approves
a concept, generation plan, FinalCreative, publication, or experiment. It never creates an endless
loop. An approved experiment may seed a new, explicitly started child cycle through
`parent_cycle_id` and `source_experiment_proposal_id`.

## State machine and provenance

`creative-cycle-v1` advances through `CREATED`, `CHECKING_READINESS`, `RESEARCHING`,
`CREATIVE_STRATEGY`, the concept checkpoint, production planning and approval, governed media,
deterministic assembly, FinalCreative review, publication input and R4 approval, publishing,
measurement, Intelligence, and the experiment checkpoint. `BLOCKED`, `FAILED`, `NEEDS_RECOVERY`,
`CANCELLED`, and `COMPLETED` are explicit outcomes.

When Intelligence produces an ExperimentProposal, the cycle pauses for its exact human decision.
When Intelligence correctly produces no proposal—for example, because one unmatched publication
cannot support a useful hypothesis—the cycle completes at the Intelligence boundary instead of
creating an impossible decision checkpoint.

An experiment decision is immutable and replay-safe. Repeating the same decision returns the
existing fact; a conflicting later decision fails closed. Approval completes the current cycle but
does not create concepts, media, or another cycle. The user must choose **Start next cycle from
experiment**. That boundary accepts only the exact approved proposal digest bound by a completed
parent cycle, creates at most one child cycle per proposal, and returns the existing child on replay.
Rejected proposals cannot seed a cycle.

The mutable cycle row stores only current state and exact immutable bindings. Append-only
transitions and step attempts preserve history. Bindings include the Product snapshot and every
created or approved artifact through the resulting ExperimentProposal. A Product edit never swaps
the cycle's snapshot; the UI reports that the Product changed after the cycle began.

Every Agent initiation uses `cycle:<cycle-id>:<step>:v1`. The existing AgentRuntime remains the
only inference boundary. Existing governed workflows remain the only media, assembly, publication,
and measurement execution paths. Ambiguous Agent or external-provider outcomes become
`NEEDS_RECOVERY`; a lease expiry or timeout never authorizes blind replay.

The durable workload is the actor that reconciles a cycle, while AgentRuns created by that
reconciliation preserve the cycle initiator as their human authority provenance. AgentRuntime
records the workload that actually claims and executes each run separately. Downstream production
can therefore revalidate the initiating user's current authority without treating workload identity
as human approval or weakening its fail-closed checks.

## Reconciliation and wakeups

`ReconcileCreativeCycle` reloads canonical tenant state, derives readiness, checks whether the
current step already completed, and performs at most one transition or one safe AgentRun request.
Outbox facts only wake reconciliation; they confer no authority. `CreativeCycleWorkflow` contains
only tenant, cycle, and correlation UUIDs and periodically invokes the same reconciler as a bounded
fallback. Losing Temporal history does not erase or reinterpret cycle state.

## Readiness and human checkpoints

The backend emits stable `READY`, `WAITING`, `BLOCKED`, and `COMPLETED` requirements with readable
messages and optional resource references. Product Brief completeness remains 80%. Missing
evidence and unavailable Agents block startup; a missing current Product snapshot is prepared at
the explicit cycle-start boundary.

Final assembly is also readiness-gated. If a ProductionPlan contains a manual-capture shot without
an approved source Asset, the cycle remains at `ASSEMBLING_FINAL_CREATIVE`, exposes
`PREPARE_FINAL_ASSEMBLY`, and does not create an AssemblyPlan or return a server error. Once every
source is ready, reconciliation starts the existing deterministic assembly workflow exactly once.
An immutable AssemblyJob that reaches `FAILED` makes the cycle fail closed rather than polling the
stage forever; a new attempt requires a new explicitly started cycle.

The Command Center displays the Product, current stage, timeline, blockers, attention items,
artifact lineage, and persistent `DEMO / FAKE` disclosure. It links users to existing review and
composer surfaces rather than duplicating their forms.

## Supervisor Agent boundary

The platform Agent key is `creative_supervisor`. Its logical profile is `supervisor_balanced`,
routed to GPT-5.6 Sol with medium reasoning, one model call, zero tools, no web/connectors, no
memory, and a 4,000-token output limit. The immutable `SupervisorContextManifest` contains bounded
cycle/readiness/artifact references only. `SupervisorReportV1` may summarize, explain, and suggest
only actions present in the deterministic readiness allowlist. Reports cannot transition state.

Phase 8 acceptance uses deterministic fake report generation and makes no paid model call.

## Security and operation

All orchestration tables use forced tenant RLS. Product, Product snapshot, parent cycle, experiment,
AgentRun, and report links use same-tenant composite foreign keys where available. Transition,
attempt, manifest, and report history is immutable. OWNER/ADMIN may start, reconcile, cancel, and
request explanation; MEMBER is read-only. Cancellation stops future coordination and never claims
to reverse an already executed side effect.

Cycle events are reference-only. Audit records meaningful user control actions, not polls. The
Supervisor receives no prompt, provider response, credential, signed URL, caption, customer PII,
or Commerce action authority. It also cannot decide an experiment or invoke the
experiment-to-cycle handoff.

## Fake acceptance and live activation

The closed-loop acceptance profile uses FakeModelProvider, fake image/video providers,
FakeSocialProvider, accelerated fake measurement, and deterministic assembly. Synthetic trust is
preserved through Intelligence. Live GPT-5.6 Sol, Sunburst, and Seedance activation changes only
existing provider composition and does not alter orchestration semantics; it requires the existing
separate live-provider acceptance gate.
