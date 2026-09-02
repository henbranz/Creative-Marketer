# ADR-001 — PostgreSQL as Primary Source of Truth

## Status
Accepted

## Decision
Use PostgreSQL as the authoritative operational datastore for tenant, product, agent, creative, commerce, governance, and insight state.

## Alternatives
- Google Sheets as operational DB
- NoSQL-first
- SaaS automation tools as state

## Rationale
The platform requires transactions, integrity, isolation, schema evolution, joins, and auditability.

## Consequences
Sheets/Excel become synchronized views/exports. External platform objects may remain externally authoritative for platform-specific status but are normalized internally.
