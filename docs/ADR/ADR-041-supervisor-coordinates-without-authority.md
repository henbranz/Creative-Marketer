# ADR-041 — Supervisor Coordinates; It Does Not Hold Authority

## Status

Accepted

## Context

The product now spans several governed agents and deterministic workflows. Asking users to infer
dependencies from individual tabs is unsafe and unusable, while granting one model transitive
authority would defeat least privilege, exact approvals, and immutable provenance.

## Decision

Add a deterministic `orchestration` bounded context and a separate explanatory Supervisor Agent.
The versioned Creative Cycle state machine, readiness, transition validation, canonical reload,
idempotency, checkpoint observation, and reconciliation are application code backed by PostgreSQL.
Temporal carries identifiers only and coordinates waits. Cross-context events wake reconciliation
but are never interpreted as commands.

The Supervisor Agent has one structured GPT-5.6 Sol call, medium reasoning, zero tools, no web,
connectors, memory, approval, publication, media, Product mutation, governance, or Commerce action
authority. Its suggestions are validated against the deterministic allowed-action set and cannot
change cycle state.

Concept, ProductionPlan, FinalCreative, R4 publication, and experiment decisions remain exact
human facts in their owning contexts. One V1 cycle ends at the experiment decision boundary; a
subsequent cycle requires explicit user initiation and preserves lineage.

## Consequences

Users gain one Command Center and readable blockers without weakening any existing control. Cycle
history remains reconstructable if Temporal is unavailable. The application performs more
cross-context reads and must maintain explicit binding/reconciliation logic, but avoids duplicating
domain ownership or creating a privileged super-agent.
