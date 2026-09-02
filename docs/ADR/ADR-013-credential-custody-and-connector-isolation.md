# ADR-013 — Credential Custody and Connector Isolation

## Status
Accepted

## Decision
Agents, prompts, general application services, and ordinary workers receive credential references, never credential values. Secrets are resolved only inside a credentialed connector execution boundary after tenant, actor, tool, approval, and resource authorization succeed.

OAuth is preferred where supported. Stored tokens use a managed secret service or a dedicated encrypted credential store with envelope encryption and KMS-backed keys. Credential metadata in PostgreSQL excludes secret material and supports ownership, status, scopes, rotation, revocation, and audit.

Connector execution and research ingestion are separate trust zones. Credentialed workers use restrictive egress and expose normalized, redacted results. Secrets never enter prompts, agent context, general events, standard logs, traces, audit metadata, or tool results returned to agents.

## Alternatives

- pass API keys to agent/tool prompts
- store plaintext or application-encrypted tokens in ordinary domain tables
- execute research browsing and privileged connectors in the same worker
- let each provider adapter implement its own authorization and redaction policy

## Rationale
The connector boundary minimizes the number of processes and libraries that can access high-value credentials and limits prompt-injection and dependency-compromise blast radius.

## Tradeoffs
Isolation adds deployment, egress, secret-manager, and local-development complexity. Some providers return sensitive response fields that require connector-specific redaction and schema review.

## Consequences

- no production secret is stored in configuration files or agent definitions
- credential access is attributable to tenant, actor, tool invocation, and connector
- revocation and rotation do not require prompt or domain changes
- local development uses fake providers or explicitly marked development credentials outside source control
- final secret-store and cloud vendors remain deferred
