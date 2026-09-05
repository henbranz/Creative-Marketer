# ADR-016 — Audit Immutability and Redaction

## Status
Accepted

## Decision
Audit records are append-only evidence of security-relevant and consequential actions. Application code may append records but cannot update or delete them through ordinary runtime paths.

Each record includes tenant, authoritative actor, agent/run when applicable, action, resource, decision/outcome, correlation, timestamp, relevant policy/tool/schema versions, and safe before/after digests or references. It excludes raw secrets, credential headers, unnecessary PII, unrestricted prompts, and unrestricted provider payloads.

Audit, domain events, and telemetry are separate concerns:

- audit answers who attempted or performed what under which policy
- domain events communicate facts for application behavior
- telemetry supports debugging and performance analysis

Access to audit data is itself authorized and audited. Retention and export support later tamper-evident or WORM storage without making that infrastructure mandatory during Phase 0.

## Alternatives

- use application logs as the audit trail
- treat domain events as complete audit records
- store full request, prompt, and provider payloads for convenience
- allow ordinary administrators to edit audit history

## Rationale
Operational logs and events have different retention, access, mutation, and privacy needs. A dedicated append-only model preserves accountability without increasing secret and PII exposure.

## Tradeoffs
Append-only records grow continuously and require partitioning, retention, export, and access tooling. Redaction can reduce debugging detail, requiring protected references to separately governed evidence.

## Consequences

- allowed and denied governed actions emit audit records
- approval decisions, credential resolution metadata, policy changes, and elevated access are audited
- audit write failure handling is defined for each risk class and fails closed for consequential external actions
- Phase 0 tests verify completeness and secret/PII redaction
- final WORM/archive technology and retention periods remain deferred

## Phase 0 implementation clarification

Security audit is implemented as its own bounded concern:

```text
Trusted Actor / ExecutionContext
          ↓
Audit Builder
          ↓
AuditWriter Port
          ↓
PostgreSQL append-only adapter
          ↓
audit.audit_records
```

Records explicitly use `platform` or `tenant` scope; a database constraint requires platform
records to have no tenant and tenant records to have one. Tenant builders derive tenant, actor,
environment, and correlation from `ExecutionContext`. Pre-tenant authentication uses either
`anonymous` or `external_principal`; the latter stores an HMAC-SHA-256 fingerprint of issuer and
subject rather than the raw subject. The HMAC key is deployment secret material and should be
rotated under an explicit version/migration policy if correlation across rotations is required.

Actions use stable dot-separated machine names (`authentication.failed`,
`identity.tenant_context.resolved`) and change only when semantics change. Structured outcomes
are `success`, `denied`, `failed`, or `error`; stable reason codes carry non-sensitive detail.
The record format starts at `audit_schema_version = 1`, independent of application releases.

The runtime database role has schema usage and table insert only. It has no audit select, update,
delete, or truncate privilege, does not own the table, and remains subject to forced RLS. RLS
rejects a tenant record whose tenant does not match the transaction-local trusted tenant context.
No generic CRUD or runtime audit-reader port exists.

Safe metadata recursively replaces forbidden secret/PII keys and recognizable credential values
with `[REDACTED]`. It is deterministically serialized, limited to 4096 bytes after redaction, and
revalidated at the persistence boundary. PostgreSQL additionally requires an object and caps its
rendered size. Oversized safe data is rejected rather than truncated. State evidence uses
SHA-256 over canonical sanitized JSON; full before/after snapshots are not audit defaults.

Business success audit is appended through the same explicit unit-of-work transaction and rolls
back with the governed state. Pre-authentication and denial evidence uses a named platform-only
standalone writer with its own short transaction; it rejects tenant records because setting tenant
authority from the record being written would create a spoofing path. Audit failures are never silently swallowed: consequential
success paths fail closed, while a denied authentication never becomes successful if its audit
write fails. Appending an audit record does not recursively produce another audit record.

Partitioning, retention periods, governed reader/support access, immutable export, tamper-evident
chains, and WORM storage remain deferred. The table preserves UUID identity and evidence time but
is not treated as a globally ordered event stream.
