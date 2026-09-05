# 07 — Security Architecture

Security is a product requirement, not a later hardening phase.

## Threat Model Highlights

The platform connects to high-value systems:

- social accounts
- commerce accounts
- customer/order information
- content providers
- object storage
- potentially finance-related workflows later

Primary threats include:

- credential leakage
- cross-tenant data access
- prompt injection
- malicious/compromised external content
- excessive agent privilege
- duplicate side effects
- unauthorized publishing
- webhook spoofing
- insecure provider integrations
- PII leakage into models/logs
- supply-chain dependency compromise

## Trust Boundaries

```text
Untrusted Internet Content
        │
        ▼
Research Ingestion / Sanitization
        │
        ▼
Structured Evidence Store
        │
        ▼
Agent Runtime

Agent Runtime
        │ structured request
        ▼
Tool Gateway
  ├─ Authentication
  ├─ Authorization
  ├─ Validation
  ├─ Approval
  ├─ Rate/Budget limits
  ├─ Idempotency
  └─ Audit
        │
        ▼
Secrets/Connector Layer
        │
        ▼
External API
```

## Credential Policy

- OAuth where possible.
- Never store user passwords for external services.
- Encrypt tokens at rest.
- Store tokens in managed secret storage or dedicated encrypted credential store.
- Access secrets only inside connector execution boundary.
- Never serialize secrets into agent context, event payloads, traces, prompts, or audit details.
- Support revocation and token rotation.

Secrets are resolved only inside the credentialed connector execution boundary. They never enter prompts, agent context, general event payloads, standard logs, or tool results returned to agents.

## Tenant Isolation

Required across:

- relational data
- object storage prefixes/buckets
- queues/jobs
- caches
- logs/traces
- events
- secrets
- vector search
- exports

PostgreSQL Row Level Security is recommended as defense-in-depth, not as a substitute for application authorization.

Tenant-owned relationships must not reference resources owned by another tenant. Use unique `(tenant_id, id)` keys and same-tenant composite foreign keys where practical.

When RLS is enabled:

- tenant context is explicit and transaction scoped, for example with `SET LOCAL`
- missing tenant context fails closed
- connection-pool reuse is tested to prove context cannot leak between requests/jobs
- runtime roles cannot bypass RLS
- migration, maintenance, and administrative roles are distinct from runtime roles
- any elevated cross-tenant operation uses a separate, explicit, audited path

## Authoritative Runtime Identity

Tenant, authenticated actor, agent version, and run identity come from trusted runtime state established by platform authentication and orchestration.

Never trust identity supplied by:

- prompts
- model output
- tool arguments
- browser-controlled fields, headers, or route parameters without server-side authorization

The Tool Gateway and repositories receive authoritative identity from the application boundary, not from model-generated input.

Agent Registry tenant mutations likewise require a trusted human `ExecutionContext`. They do not
accept tenant or actor authority separately, and Agent actors cannot modify their own definitions,
versions, scopes, tools, budgets, or activation. Platform-template writes remain outside ordinary
runtime until a separately authorized platform administration path exists.

The `agent_governance` schema uses forced RLS. Tenant runtime can read its own registry state and
platform templates needed for explicit resolution, but cannot read another tenant. Insert/update
policies admit only the transaction-local tenant. Database triggers validate template ownership and
denormalized version/activation ownership. Historical versions have no runtime update/delete
privilege; definition identity is trigger-protected and archived definitions cannot be resurrected.

System instructions are bounded and reject recognizable credential-shaped content. They remain
inside immutable registry configuration and are not copied into audit, logs, or telemetry. Agent
configuration contains logical model/tool references only and never connector credentials.

## Prompt Injection Defense

Research/browser input is data, never instructions.

Preferred flow:

```text
Fetcher/Crawler
  ↓
Content classifier/sanitizer
  ↓
Extractor
  ↓
Typed research/evidence schema
  ↓
Researcher
```

Do not give arbitrary fetched web pages direct authority over tool-enabled agents.

Sanitization and classification reduce risk but are not complete security boundaries. Research ingestion/crawling and credentialed connector execution run as separate trust zones with different egress, credentials, and tool surfaces. Structured evidence retains provenance and untrusted-content labeling.

## PII

Separate customer PII from general commerce state.

Creative/Research/Producer agents should not receive:
- addresses
- personal phone numbers
- customer emails
- payment-related details

Intelligence should use aggregated/de-identified values where possible.

## Webhook Security

For each provider:
- verify provider signature
- enforce timestamp/replay window where supported
- deduplicate event ID
- store raw receipt safely when needed
- normalize into internal event
- process idempotently

## Egress

Where practical:
- allow-list known provider endpoints for sensitive workers
- block arbitrary outbound traffic from privileged connector workers
- isolate research browsing from credentialed connector execution

## Approval Security

Approval must bind to the exact proposed action using:
- payload hash
- tenant
- actor
- tool
- resource
- expiration

The binding uses a versioned canonical representation of normalized action input and also covers tool/version, environment, policy version, and idempotency context. Approval state transitions are atomic and auditable. Reuse, expiration, revocation, payload mismatch, or material state change fails closed. Authorization, budget, connector state, and approval validity are rechecked immediately before execution to reduce time-of-check/time-of-use risk.

## Idempotency and External Outcomes

- Idempotency is scoped at least by tenant, tool/version, and key.
- The record stores a canonical request digest.
- Reusing a key with a different digest fails.
- Concurrent acquisition uses an atomic constraint or execution lease.
- Execution state distinguishes failure before an effect from an unknown external outcome.
- Unknown outcomes are reconciled before a retry can create another side effect.
- Provider-native idempotency keys are used where available but do not replace platform records.

Material changes after approval invalidate the approval.

## Logging

Never log:
- raw access tokens
- secret headers
- payment credentials
- full unnecessary PII

Audit metadata should remain useful without exposing secrets.

## Security Audit

Audit is accountability evidence and is not an application log, domain event, or telemetry span.
The Phase 0 flow is explicit: trusted `Actor`/`ExecutionContext` → audit builder → inward-owned
`AuditWriter` → PostgreSQL adapter → `audit.audit_records`. Tenant builders derive authoritative
actor, tenant, environment, and correlation values from the execution context. Pre-tenant unknown
principals are represented by a keyed HMAC-SHA-256 issuer/subject fingerprint, never a fabricated
User and never a raw subject.

The runtime database role receives only `USAGE` on the audit schema and `INSERT` on the table. It
cannot select, update, delete, or truncate records. Forced RLS checks tenant inserts against the
transaction-local tenant setting, and scope constraints prevent platform records from carrying a
tenant. Governed audit reads and elevated support access are intentionally not exposed yet.

Metadata is recursively sanitized and obvious credential-shaped values are redacted even when
their key is innocuous. Forbidden secret and PII fields become `[REDACTED]`; values are not logged
elsewhere as a fallback. Metadata over 4096 canonical bytes is rejected, not truncated, and the
persistence adapter revalidates it. Full before/after payloads are avoided in favor of canonical
SHA-256 digests where evidence is needed.

Successful governed mutations append audit in the same unit-of-work transaction. Denials and
pre-authentication failures use an explicit platform-only standalone short transaction; tenant
records require a trusted context-bound transaction. Audit failure is visible
and never ignored; successful consequential work fails closed, while denial remains denial. Audit
append itself is deliberately non-recursive. Retention, partitioning, governed readers, export,
tamper-evident chaining, and WORM storage are deferred until policy and volume requirements exist.

## Security Tests

Must include:
- cross-tenant access attempts
- forged agent identity
- forged approval
- expired approval
- duplicate publish
- webhook replay
- prompt injection content
- secret redaction
- provider error handling
- object storage tenant escape
