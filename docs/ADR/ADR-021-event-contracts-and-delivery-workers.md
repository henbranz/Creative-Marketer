# ADR-021 — Event Contracts and Narrow Delivery Workers

## Status

Accepted

## Context

Reliable propagation requires producers and consumers to agree on immutable semantics while the
cross-tenant publisher must not inherit ordinary business authority.

## Decision

Canonical event payload contracts are self-contained JSON Schema 2020-12 files. The application
loads them into an explicit registry, rejects unknown types and all `$ref`, validates before
Outbox insertion and before handling, and persists a canonical SHA-256 schema digest. A breaking
semantic change creates a new event suffix and schema version.

The immutable DomainEvent envelope uses explicit tenant/platform scope, trusted actor and tenant
provenance, correlation/causation, bounded strict-JSON payloads, and Event Canonical JSON V1. Its
semantic digest binds the complete immutable envelope. Event Canonical JSON V1 is independently
versioned and does not alter ActionBinding canonicalization.

Publication uses an inward-owned `EventTransport` port and a reusable application publisher. No
discarding production transport exists. Until a deliberate adapter is configured, the worker
fails closed and Outbox records remain pending. PostgreSQL is the Phase-0 durability source; no
broker, Redis durability, Temporal workflow, Tool Gateway, event sourcing, or Agent Runtime is
introduced.

## Alternatives considered

- Pydantic-only contracts: rejected because they are not language-neutral boundary authorities.
- Remote schema references: rejected because resolution creates drift and a network/SSRF surface.
- Runtime cross-tenant role for publication: rejected because it expands compromise impact.
- Exactly-once claims: rejected because transport acknowledgement cannot atomically commit with
  PostgreSQL; at-least-once plus Inbox deduplication states the real guarantee.

## Consequences

- Contract files and digests must remain immutable for a published version.
- Consumers reject local/stored digest mismatch rather than reinterpret old events.
- The publisher has its own production secret and deployment identity.
- Retention, partitioning, archive, replay administration, broker choice, and platform-consumer
  authorization remain explicit future decisions.
