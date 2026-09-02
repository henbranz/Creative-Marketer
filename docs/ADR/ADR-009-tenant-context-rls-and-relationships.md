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
