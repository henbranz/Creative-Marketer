# ADR-002 — Event-Driven Agent Collaboration

## Status
Accepted

## Decision
Agents do not call each other directly. Deterministic application workflows coordinate agent work through an Agent Runtime, durable workflows, typed domain events, and approved shared state.

An application workflow may invoke an Agent Runtime synchronously when the operation is bounded and does not require durable waiting. It validates and persists the result before recording resulting domain facts.

Commands express intent and may be rejected. Events are immutable facts that already occurred. Events must not be used as disguised commands, and not every internal function call becomes asynchronous.

Cross-context event propagation uses a transactional outbox. Consumers assume at-least-once delivery and use an inbox or equivalent idempotent deduplication.

## Alternatives

- direct agent-to-agent calls
- asynchronous events for every interaction
- one central agent that owns all coordination

## Rationale
This prevents agent privilege from becoming transitive while preserving simple synchronous execution where it is safe. Transactional events reduce dual-write failure and allow future consumers without turning the modular monolith into a distributed system prematurely.

## Consequences
Requires explicit command handlers, event schemas, outbox/inbox infrastructure, idempotent consumers, correlation/causation IDs, and context ownership. Application workflows remain visible dependencies and are responsible for deciding when synchronous or durable coordination is appropriate.
