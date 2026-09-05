# Phase-0 Architecture and Security Hardening Report

Date: 2026-09-05

## Scope and conclusion

TASK-013 reviewed the implemented Phase-0 platform boundary from repository imports and public API
routes through PostgreSQL roles/RLS, identity, registries, permission, approval, idempotency, Tool
Gateway, Audit, Outbox/Inbox, OpenTelemetry, and Temporal. It did not implement a provider, business
Agent, webhook, object store, secret manager, or production deployment.

The implemented scope has no unresolved `CRITICAL` or `HIGH` finding. One `MEDIUM` database
least-privilege finding was corrected by migration `20260905_0011`: internal trigger functions had
PostgreSQL's default `PUBLIC EXECUTE`, and three owner-validation triggers unnecessarily used
`SECURITY DEFINER`. The migration makes all three invoker-rights and revokes public execution from
all application trigger functions. Final-schema catalog tests make the correction permanent.

This report does **not** declare Phase 0 complete. TASK-014 owns the final review decision.

## Automated invariants and attack categories

The permanent `make phase0-gate` (alias `make architecture-security`) runs lint, formatting, strict
typing, a freshly migrated PostgreSQL-backed complete test suite, the static architecture/security
checks, coverage, Temporal time-skipping scenarios, and replay tests. CI exposes the same boundary
as the clearly named `architecture-security` job. The detailed mapping from every control to its
test is in `docs/PHASE_0_INVARIANT_MATRIX.md`.

The suite covers:

- dependency inversion, cross-context adapter discipline, provider/Agent-framework absence, no
  business-Agent cognition, executor call sites, route enumeration, secret patterns, debug calls,
  contract ownership, and a linear migration graph;
- database roles, ownership, inheritance, tenant-table discovery, forced RLS, policy predicates,
  missing tenant context, repeated pool reuse, same-tenant relationships, function security, and
  `PUBLIC` ACLs;
- identity/header spoofing, exact external identity, lifecycle denial, dev-auth environment gating,
  Agent/Tool version immutability, exhaustive permission outcomes, approval races/binding, and
  repeated idempotency concurrency;
- unknown external outcomes, post-effect persistence failure, raw-SQL ToolCall mutation, missing
  budget/credential controls, and unsupported obligations;
- Audit immutability/redaction/atomicity, event schema/digest/privacy attacks, Outbox publisher
  crashes, Inbox concurrency/deduplication, and trace non-authority;
- telemetry privacy/cardinality/fail-open behavior and Temporal determinism, replay, signal
  non-authority, history privacy, worker recreation, cancellation, and same-operation retry.

## Findings and fixes

| Severity | Finding | Resolution |
| --- | --- | --- |
| MEDIUM | Internal PostgreSQL trigger functions inherited default `PUBLIC EXECUTE`; three Agent Registry owner-validation functions also used unnecessary owner rights. This enlarged the privilege surface and made safe behavior depend on trigger-only invocation semantics and schema ACLs. | Added `20260905_0011_function_privilege_hardening.py`, changed those functions to `SECURITY INVOKER`, revoked every application trigger function from `PUBLIC`, and added final-schema catalog assertions. |
| LOW | GitHub Actions use semantic major tags and Docker images use mutable version tags rather than immutable commit/digest pins. | Explicitly retained for this task because trusted SHAs/digests were not guessed. Resolve and pin verified upstream references, with an automated update owner, before a production release. |
| LOW | No deterministic dependency vulnerability audit/update policy is yet integrated. | Lockfile-frozen installs remain enforced. Add ecosystem-native audit/update automation with triage ownership before production release; do not make the core gate depend on an unreliable external SaaS. |

No production application behavior was added. The database fix is a focused privilege correction,
not a schema expansion.

## Database role and RLS findings

The final schema is inspected through PostgreSQL catalogs rather than a hand-maintained table list.
`creative_marketer_runtime` and `creative_marketer_event_publisher` must remain login roles that are
not superusers, database/schema/table owners, role/database creators, replication roles, or
`BYPASSRLS` roles, and they inherit no privileged role. Runtime DDL attempts are denied.

Every application table discovered with a `tenant_id` column must have RLS enabled and forced.
Every such table must have policy coverage, and no policy granted to runtime may use an
unconditional `true` predicate. Missing context is checked across Identity, Agent, Permission,
Approval, Idempotency, Inbox, and ToolCall tables. Repeated `A → B → missing → A` transactions on a
single-connection pool prove transaction-local tenant state is cleared.

Tenant-to-tenant foreign keys are catalog inspected for paired tenant identity. The reviewed
Agent Registry template/version relationships use exact definition/version composite keys and
focused database triggers to compare denormalized scope/tenant ownership. Approval's resolved
AgentVersion is likewise paired with its exact resolved definition because that definition may be
either tenant-owned or an explicit platform template; the separately requested Agent relationship
is tenant-composite. The publisher has cross-tenant Outbox read plus only the delivery columns it
must update; it has no business read, event insert, Inbox read, or envelope update authority.

## Governance and identity findings

Identity authority remains `credential → exact issuer/subject → User → selected tenant membership
verification → ExecutionContext`. Browser headers, query/body identity, email equality, provider
roles, and model input do not grant authority. Development authentication is rejected by settings
in staging and production. Context structures have narrow frozen fields and carry no authorization
header, access/refresh/ID token, password, or arbitrary claim bag.

Agent and Tool definitions retain stable identity, immutable version snapshots, exact active
version resolution, archived terminality, and explicit template provenance. Tool risk is resolved
from the exact active ToolVersion. Tool contracts remain self-contained bounded JSON Schema and
reject remote/malformed references, pathological depth/size, open unexpected properties, and
credential-shaped examples/defaults.

Permission remains a pure deterministic intersection. Missing configuration, Agent deny, policy
deny, scope/environment mismatch, unavailable version, and R7 Agent use deny. R4-R6 require
approval under the current baseline. Decisions bind exact Agent, Tool, Permission versions,
configuration digests, engine version, and scope digest.

## Approval, idempotency, and Tool Gateway findings

Approval uses the same strict `ActionBindingV1` as idempotency and binds tenant, operation, exact
versions/digests, scope/resource/environment, normalized input, risk, and expiry. Immutable decision
and revocation history derives current state. Repeated concurrent approve/deny creates one terminal
decision. Owner/admin rules and R6 owner-only rules are based on current authoritative context;
Agent, workload, member, or browser-supplied roles cannot decide.

Repeated concurrent reservations preserve one logical record for same key/digest and conflict on
changed digest. Attempt ownership rejects stale completion. `FAILED_PRE_EFFECT` alone is retryable;
an unexpected failure after possible execution becomes `UNKNOWN_EXTERNAL_OUTCOME` and needs
explicit reconciliation. The post-effect persistence failure regression asserts the fake effect
count remains one.

The Gateway is the only production-source call site for `ToolExecutor.execute`. It re-resolves the
active Agent, Tool, and Permission immediately before reserving execution, rejects stale bindings,
validates exact input/output contracts, and fails closed on an unknown obligation, absent budget
guard, or unavailable connector credential boundary. Raw SQL attacks cannot change ToolCall tenant,
operation, binding digests, status, result, or error after completion.

## Audit, events, async delivery, and privacy findings

Audit remains an independent append-only security evidence store. Runtime can insert but cannot
read/update/delete/truncate; tenant inserts are forced-RLS checked. Metadata is bounded and
recursively redacted, and security-relevant successful mutations join their state, Audit, and
Outbox fact in one transaction. Telemetry disabled or failing does not replace, suppress, or alter
Audit.

Events use strict canonical envelopes, local language-neutral `.vN` schemas, schema and event
digests, authoritative scope/actor/correlation fields, and privacy-bounded payloads. Trace context
is separate immutable delivery metadata and contributes no authority. A publisher crash after send
causes deliberate at-least-once redelivery; Inbox uniqueness and atomic consumer state produce one
effect. Same event ID with another digest is corruption, not a duplicate.

Existing generic infrastructure rejects credential, prompt, raw payload, customer contact/address,
and unbounded content in Audit, Events, telemetry, Temporal history, ToolCall, and idempotency. Full
Commerce PII isolation remains mandatory when that context begins because there is no customer PII
model in Phase 0.

## Temporal and telemetry findings

OpenTelemetry remains behind no-op-safe application-owned ports. Metric dimensions are centrally
allow-listed and exclude tenant/user/operation/event/correlation/trace identifiers. Logs and traces
exclude bodies, queries, headers, Tool input/output, prompts, provider responses, secrets, and PII.
Exporter failure is fail-open; invalid startup configuration still fails fast.

Temporal SDK imports remain confined to `infrastructure/temporal`. Workflows coordinate only safe
references and deterministic SDK operations. Replay, long waits with time skipping, actual worker
replacement, signals, polling, scheduling, retry, cancellation, and privacy are permanent tests.
Signals only wake workflows; Activities and Gateway re-resolve PostgreSQL-owned authority. Lost
Activity responses reuse the same operation and the fake external effect remains single. The
production worker deliberately refuses to start until workload authentication and authoritative
request resolution are composed.

## Supply-chain findings

Python CI installs with `uv sync --frozen`; Node installs with `npm ci`. No `curl | bash` installer,
secret echo, or unlocked resolver appears in CI. Python and Node dependency graphs are committed in
`uv.lock` and `package-lock.json`.

Action references (`actions/checkout@v4`, `actions/setup-node@v4`, `astral-sh/setup-uv@v7`) and
container references (`postgres:17-alpine`, application base images, and the optional Temporal dev
image) remain mutable tags. Arbitrary hashes were not invented. This is an accepted LOW repository
supply-chain risk for the current development foundation, not an acceptable final production
release policy.

## Deferred and not-applicable controls

The following are intentionally not represented by fake implementations:

- Research prompt-injection ingestion isolation: `NOT_APPLICABLE_YET`; mandatory at Phase 2
  Research ingestion (`untrusted web → sanitizer/extractor → structured contract → Agent`).
- Webhook signature/timestamp/replay verification: `NOT_APPLICABLE_YET`; mandatory before the first
  provider webhook route.
- Object-storage tenant isolation and rights metadata: `NOT_APPLICABLE_YET`; mandatory before an
  object-storage adapter.
- Commerce customer PII schema, purpose-limited access, deletion, and retention:
  `NOT_APPLICABLE_YET`; mandatory before Commerce persistence.
- Production secret manager, encrypted credential store, connector isolation, and egress:
  `DEFERRED`; mandatory before any real provider adapter can execute.
- Cross-tenant analytics/learning, regional residency, and final retention schedules: `DEFERRED`
  pending product/compliance requirements.

## Residual production blockers

- authenticated workload identity for Temporal and other background workers;
- durable authoritative background request resolution and production approval-to-workflow
  association;
- production secret custody, connector execution isolation, restrictive egress, and provider
  adapters;
- Temporal hosting choice, namespace retention/archival, backup/DR, capacity, worker build routing,
  and service failover testing;
- production collector/dashboards/alert routing for unknown outcomes, Outbox terminal failures,
  backlog age, Tool failures, and readiness;
- verified immutable CI Action and production container pins plus dependency vulnerability/update
  ownership;
- provider-specific webhook and object-storage controls when those surfaces are introduced.

## Recommendation for TASK-014

**READY for TASK-014 Phase-0 Final Review**, subject to the complete `architecture-security` CI job
passing on the pushed commit. This recommendation means the implemented foundation is ready to be
reviewed; it does not waive or prematurely close the production blockers above.
