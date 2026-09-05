# ADR-018 — Tool Registry Versioning and Contracts

## Status
Accepted

## Decision

The Tool Registry is a platform-global capability catalog. `ToolDefinition` owns a stable,
provider-neutral canonical tool key. `ToolVersion` is an immutable, monotonically numbered
snapshot of the capability's language-neutral input/output contracts and security-relevant
classifications. `ToolActivation` is the single mutable pointer selecting the current version.

Input and output contracts use self-contained JSON Schema 2020-12 object schemas. Registration
rejects non-local `$ref`, bounds canonical size, depth, and node count, and rejects
credential-shaped schema values. Canonical JSON SHA-256 digests identify both schemas and the
complete semantic version configuration. Historical versions remain unchanged and can be
reactivated for rollback.

Tool Registry existence and AgentVersion tool declarations do not authorize execution. Tenant
policy belongs to future `ToolPermission`; the future Permission Engine is authoritative. A
read-only reconciliation service reports known-active, known-unavailable, unknown, and explicitly
denied declarations without returning an authorization decision.

The ordinary runtime database role may read Registry tables but cannot insert, update, delete, or
truncate them. Internal writes use a separately configured privileged control-plane connection,
require explicit trusted system/workload context, and append platform audit atomically. No public
mutation API exists. Phase 0 reuses the migration/control-plane role rather than adding a third
database role; a narrow audit policy permits that role to append platform records.

## Alternatives

- mutable tool rows containing identity, schema, implementation, and activation
- tenant-owned arbitrary capabilities in the initial catalog
- provider SDK function declarations as the canonical contract
- AgentVersion allow lists as authorization
- concrete executor paths or credentials stored with the capability
- remote JSON Schema reference resolution

## Rationale

Stable identity plus immutable snapshots makes approvals, policy decisions, audit, replay, and
future ToolCall records reproducible. Separate activation permits rollout and rollback. A global,
read-only-at-runtime catalog minimizes capability-injection risk while tenant permission remains a
separate concern. Self-contained schemas prevent validation from becoming an SSRF boundary.

## Tradeoffs

Every contract or classification change creates a version, increasing retained configuration
history. JSON Schema validation adds a dependency and deliberate boundary mapping. Platform
catalog changes currently require an internal privileged process because product-grade platform
administration authorization is not yet available. Schema compatibility analysis is manual; no
semantic diff engine is included.

## Consequences

- tool keys use the same shared canonical grammar as AgentVersion declarations
- archived keys remain reserved and archived definitions cannot be resurrected
- version allocation and activation serialize on the definition row
- risk, side effect, execution, credential, and idempotency fields are declarations only
- resolved tools contain no credential or provider SDK object
- full schemas are excluded from audit in favor of IDs and digests
- runtime schema validation performs no network access
- ToolPermission, approvals, idempotency execution, ToolCall, adapters, and Tool Gateway remain deferred
