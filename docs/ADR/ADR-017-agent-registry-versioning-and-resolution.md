# ADR-017 — Agent Registry Versioning and Resolution

## Status
Accepted

## Decision

An agent definition is a stable governed identity and ownership record. Its behavior is stored in
immutable, monotonically numbered agent versions. The active version pointer is separate mutable
activation state. Tenant definitions may reference a platform definition explicitly; no platform
agent is ambient and no fallback occurs by agent type, key, or display name.

Resolution for an active tenant definition is deterministic:

1. return its active tenant version when present
2. otherwise follow its explicit active platform template activation
3. otherwise return `AgentUnavailable`

A tenant linked to a template definition follows changes to that template's explicit activation.
This enables controlled central rollout without rewriting tenant records. A tenant-owned
activation pins and overrides the template until deliberately changed or removed by a future
governed operation. The resolved value preserves requested tenant definition, actual resolved
definition/version, tenant, and provenance.

Platform template writes use the migration/internal control-plane role during Phase 0. Tenant
runtime may read platform definitions and active versions only for explicit resolution; it cannot
create or mutate them. No public registry mutation API exists before the Permission Engine.

## Alternatives

- store mutable prompts and policies directly on the definition
- mark versions active by mutating historical version rows
- silently fall back to a platform record by agent type
- pin every tenant link directly to one platform version
- expose tenant mutation endpoints before authorization exists

## Rationale

Immutable snapshots make behavior reproducible and attributable. Separate activation supports
rollback without rewriting history. Definition-level template links plus explicit platform
activation provide a simple rollout mechanism; provenance keeps this dynamic behavior observable.
Application and PostgreSQL boundaries prevent accidental cross-tenant or platform mutation.

## Tradeoffs

Dynamic template activation can change a tenant's resolved behavior without changing its tenant
record. The platform activation is therefore a consequential audited control-plane decision once
that administration path exists. Tenants requiring stability must create and activate a tenant
version; explicit template-version pinning may be added later if product rollout requirements show
it is necessary.

Version configuration uses typed domain values but JSONB persistence for nested policies and
declarations. Application validation is richer than database JSON shape checks, so ordinary writes
must use registry use cases. Platform bootstrap remains a privileged internal operation.

## Consequences

- archived definition keys remain reserved and are not reusable
- archived definitions cannot be resurrected; disabled-to-active enabling is deferred
- disabled definitions may receive staged versions but cannot activate or resolve them
- lifecycle transitions are active → disabled/archived and disabled → archived
- runtime cannot update or delete agent versions
- definition row locks serialize version-number allocation and activation changes
- database uniqueness remains the final concurrency guard
- activation rollback re-points the activation row and audits a new decision
- allowed tools/scopes remain inert declarations until future governance evaluates them
- Agent Registry contains no runtime, model-provider mapping, credentials, memory, or agent runs
