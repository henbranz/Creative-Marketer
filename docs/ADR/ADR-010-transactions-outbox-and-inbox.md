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
