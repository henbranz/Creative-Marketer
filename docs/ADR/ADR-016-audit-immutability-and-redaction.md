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
