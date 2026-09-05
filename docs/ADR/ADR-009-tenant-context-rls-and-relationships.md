# ADR-009 — Tenant Context, RLS, and Same-Tenant Relationships

## Status
Accepted

## Decision
Tenant context is an explicit input to tenant-aware application use cases, repositories, jobs, events, cache keys, object references, and connector operations. There is no process-global implicit current tenant.

Tenant-owned relationships must not reference resources owned by another tenant. Where practical, each tenant-owned table exposes a unique `(tenant_id, id)` key and tenant-owned foreign keys include both columns.

PostgreSQL Row Level Security is defense in depth alongside application authorization. When RLS is enabled:

- tenant context is set within each transaction, using transaction-scoped state such as `SET LOCAL`
- missing or invalid tenant context fails closed
- pooled connection reuse cannot retain tenant state after transaction completion
- runtime roles cannot bypass RLS
- migration, maintenance, and administrative roles are separate from runtime roles
- elevated cross-tenant operations use a separate explicit and audited path

## Alternatives

- application query filters without RLS
- database-per-tenant from the beginning
- schema-per-tenant
- session-scoped tenant variables on pooled connections

## Rationale
Application authorization remains necessary, but database constraints and RLS limit the blast radius of a missing filter or compromised repository path. Composite relationships prevent referential cross-tenant corruption that row filters alone do not address.

## Tradeoffs
Composite keys and RLS add migration, query, test, and operational complexity. Database-per-tenant offers stronger physical isolation but is too costly for the expected early SaaS shape. Transaction-scoped tenant state requires disciplined transaction boundaries.

## Consequences

- every background job and event consumer carries tenant context explicitly
- integration tests exercise pooled-connection reuse and missing-context denial
- migration tooling uses a non-runtime role
- tenant-owned repository APIs require tenant identity
- globally scoped records use explicit ownership semantics rather than accidental null-tenant fallback

## TASK-002 implementation notes

The initial identity schema uses an async SQLAlchemy 2.x unit of work because API and future worker entrypoints are concurrent and I/O bound. Each application use case owns one transaction. The adapter establishes `app.current_tenant_id` with `set_config(..., true)`, whose `true` flag makes the value transaction-local; policies use `current_setting(..., true)` and `nullif` so an unset value matches no protected row. An invalid UUID fails the statement.

`identity.tenants` and `identity.memberships` use forced RLS. `identity.users` is deliberately platform-scoped because one user can belong to several tenants; tenant code reaches users only through purpose-specific repositories rather than a tenant ownership fiction. The runtime login is a non-owner with `NOBYPASSRLS`; the migration login owns schema objects and is never an application credential.

The custom tenant setting is not itself an authorization grant: PostgreSQL permits a role to set custom GUC values. Only the trusted application boundary may choose its value, based on the authoritative context introduced in TASK-003. RLS limits missing-filter mistakes and pooled-session leakage but does not replace credential security or application authorization.

Membership uses `(tenant_id, user_id)` as its primary key with foreign keys to the tenant and global user. Future relationships between two tenant-owned resources use a unique `(tenant_id, id)` target plus a composite `(tenant_id, resource_id)` foreign key; no artificial surrogate relationship was added merely to demonstrate that convention.
