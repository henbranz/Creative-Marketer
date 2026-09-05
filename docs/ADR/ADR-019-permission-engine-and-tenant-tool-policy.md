# ADR-019 — Deterministic Permission Engine and Tenant Tool Policy

## Status

Accepted

## Decision

Tenant tool authorization uses a stable `ToolPermission` identity, immutable monotonically
numbered `ToolPermissionVersion` snapshots, and one mutable `ToolPermissionActivation` pointer.
The identity binds a tenant, the requested tenant `AgentDefinition`, and a platform
`ToolDefinition`. It never binds the resolved platform-template definition. PostgreSQL composite
foreign keys enforce that the agent subject belongs to the same tenant, and forced row-level
security isolates all three policy tables.

The pure `PermissionEngine` receives only authoritative `ExecutionContext`, resolved Agent and
Tool versions, the resolved active permission version, and application-constructed trusted scope
requirements. It performs no I/O, model call, prompt evaluation, provider execution, input
validation, budget consumption, credential resolution, approval operation, or idempotency
operation. The application service resolves each dependency, maps every missing or unavailable
state to denial, invokes the engine, and appends a safe audit record.

Authority is the intersection of the AgentVersion scope and tool declarations, the active tenant
permission scope and environment constraints, and the platform ToolVersion risk classification.
Missing policy and every mismatch deny. R0–R3 may be allowed, R4–R6 require approval, and agent
execution of R7 is always denied. `ALWAYS` may make low-risk operations require approval;
permission policy cannot bypass the R4–R7 baseline. Legitimately scope-free operations require an
explicit trusted marker; an empty ordinary scope list is invalid.

Positive decisions are unusable if their decision audit cannot be committed. A denial remains a
denial if denial auditing fails. Decisions contain immutable IDs and digests, reason codes, the
scope-request digest, engine version, and downstream obligations, but no prompt, raw tool input,
schema, credential, or policy body.

## Alternatives

- one mutable permission JSON document
- AgentVersion tool declarations as complete authorization
- policy attached to a resolved platform template
- model-supplied scopes or model-evaluated authorization
- arbitrary Python, SQL, prompt, or generic policy-language expressions
- OPA or another policy runtime during Phase 0
- tenant policy that can suppress mandatory approval for high-risk tools

## Rationale

Immutable revisions and explicit activation make historical decisions reproducible and rollback
auditable. A small typed policy model is easier to review, test, and fail closed than an open-ended
policy language. Binding to the requested tenant agent preserves tenant ownership across platform
template upgrades. Separating the pure engine from resolution and audit keeps security logic
deterministic without weakening persistence controls.

## Tradeoffs

Each policy change retains another row, and policy administration is intentionally limited to an
active OWNER or ADMIN user. The Phase-0 risk table is conservative and cannot express conditional
autonomy. Resource identifiers are recorded only in the trusted scope digest; resource ownership
must be established by the future Tool Gateway-side requirement builder. Dynamic budget, approval,
idempotency, rate-limit, feature-flag, credential, and input checks remain downstream obligations.

## Consequences

- no public policy-mutation endpoint exists
- an Agent cannot mutate policy or invoke authorization as a Tool
- policy versions cannot be updated or deleted by the runtime role
- archived policy identities are terminal and their subject tuple remains reserved
- rollback changes only the activation pointer and is audited
- decision audits use IDs, digests, reason codes, and obligations only
- PermissionDecision is not persisted in a dedicated table during Phase 0
- policy engine semantics are explicitly versioned as version 1
