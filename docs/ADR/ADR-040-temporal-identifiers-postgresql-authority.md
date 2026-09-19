# ADR-040 — Temporal Carries Identifiers; PostgreSQL Reconstructs Authority

Status: Accepted

## Context

Commerce workflows can wait for human approval, survive process restarts, and retry activities.
Workflow history is durable, but it is not the authoritative store for mutable identity,
membership, permission, approval, provider target, or credential state. Copying those values into
Temporal payloads would turn an old workflow event into continuing authority and could expose
sensitive business data in workflow history.

## Decision

Temporal Commerce inputs contain identifiers only: tenant, proposal or sync-request ID,
correlation ID, and an opaque durable request reference. The reference resolves an immutable
ToolCall in PostgreSQL; it does not encode proposal content, store/order/SKU data, approval state,
credentials, or permission state.

Every Commerce activity reconstructs authority from PostgreSQL. It reloads the initiating user,
current membership and tenant state, requested Commerce AgentDefinition, immutable ToolCall and
ApprovalRequest, canonical identifier-only input, proposal and digest, CommerceConnection target,
and current observations. Tool Gateway re-evaluates the active AgentVersion, exact active
ToolVersion, permission policy, approval binding, resource scope, and shared idempotency record
before execution.

The dedicated Commerce workload has a separate deployment identity. It proves which workload is
executing but never replaces or fabricates the initiating user. Both identities are preserved in
durable records. Approval signals merely wake a workflow; they do not convey approval authority.

`OUTCOME_UNKNOWN` transitions permanently from mutation submission to bounded read-only status
reconciliation. Stable Tool Gateway operation identity is the only mutation idempotency authority.

## Consequences

Workflows remain replay-safe and contain no trusted business payloads or secrets. Revoked access
fails closed even after an earlier approval, tenant/provider targets cannot be redirected by a
browser or stale workflow payload, and worker restarts do not permit duplicate mutations. This
requires additional PostgreSQL reads at activity boundaries and a durable sync-request record, but
keeps authority centralized and auditable.
