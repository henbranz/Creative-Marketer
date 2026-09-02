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

Material changes after approval invalidate the approval.

## Logging

Never log:
- raw access tokens
- secret headers
- payment credentials
- full unnecessary PII

Audit metadata should remain useful without exposing secrets.

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
