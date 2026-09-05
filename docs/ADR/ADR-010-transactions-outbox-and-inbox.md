# ADR-010 — Transaction Boundaries and Transactional Outbox/Inbox

## Status
Accepted

## Decision
An application use case defines the normal database transaction boundary. Aggregate state changes and the domain events they produce are written atomically to PostgreSQL using a transactional outbox.

Outbox delivery is at least once. Every event consumer is idempotent and records `(consumer_name, event_id)` in an inbox or equivalent deduplication store. Consumer state changes and inbox acknowledgement commit atomically where they share a database.

Events are immutable facts, not commands, and the platform does not adopt event sourcing in Phase 0. Cross-context workflows may use synchronous application interfaces when atomic local behavior is required and events when propagating completed facts.

## Alternatives

- write application state and publish to a broker independently
- distributed transactions
- event sourcing as the primary persistence model
- best-effort in-process event callbacks

## Rationale
The outbox prevents a committed state change from losing its corresponding event. The inbox makes duplicate delivery safe without requiring an impossible exactly-once transport guarantee.

## Tradeoffs
Outbox polling/publication introduces latency, table growth, retries, and operational monitoring. Consumers must design for duplicates and out-of-order events. Cross-context eventual consistency must be visible in product states.

## Consequences

- outbox records include tenant, event type/version, aggregate, actor, correlation, causation, payload, and publication state
- poison messages and terminal delivery failures require an observable intervention path
- retention and partitioning are reviewed as volume grows
- schema compatibility is validated before deployment
- no external broker is required during initial Phase 0

## TASK-009 implementation notes

The `event_delivery` PostgreSQL schema now contains immutable `outbox_events` and
`inbox_receipts`. Approval request, decision, and revocation use cases append their governance
fact through the same SQLAlchemy session that owns domain mutation and Audit. There is no hidden
connection and no post-commit callback.

Publisher workers claim ready rows in a short transaction with `FOR UPDATE SKIP LOCKED`, change
them to `PUBLISHING`, increment the attempt, and establish a bounded owner lease. Transport I/O
happens after that transaction commits. A crash after transport acceptance but before the
`PUBLISHED` update deliberately permits redelivery after lease expiry. Retryable failures return
to `PENDING` with capped exponential backoff; exhausted or non-retryable failures remain
`FAILED_TERMINAL` and queryable. `PUBLISHED` means transport acceptance, not consumer completion.

The narrow `creative_marketer_event_publisher` role can read Outbox rows across tenants and update
only delivery columns. It cannot insert events, mutate event content, read Inbox, or read identity,
approval, registry, and tenant business tables. It is non-owner, `NOSUPERUSER`, and `NOBYPASSRLS`.

Consumers validate the envelope, local schema digest, and payload before opening tenant work.
They reserve `(consumer_name, event_id)` with a unique insert in the same transaction as handler
state. Same ID/same digest is `ALREADY_PROCESSED`; same ID/different digest is
`EVENT_ID_CONFLICT`. Handler version is evidence, not part of the deduplication key. Platform
consumer execution remains deferred to a separately authorized control-plane path.
