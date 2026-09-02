# ADR-002 — Event-Driven Agent Collaboration

## Status
Accepted

## Decision
Agents do not call each other directly. Cross-domain collaboration uses durable workflows, typed events, and approved shared state.

## Rationale
Reduces coupling, improves replayability/auditability, and allows future agents to subscribe without modifying producers.

## Consequences
Requires event schemas, idempotent consumers, correlation/causation IDs, and explicit data ownership.
