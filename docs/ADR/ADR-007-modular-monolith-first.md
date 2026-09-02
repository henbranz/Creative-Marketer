# ADR-007 — Modular Monolith First

## Status
Accepted

## Decision
Begin with strong domain modules in a modular monolith plus separate workers where justified. Do not create a microservice per agent.

## Rationale
Early service fragmentation increases operational complexity and makes schema/workflow evolution slower.

## Consequences
Module interfaces and dependency rules must be enforced so contexts remain extractable later.
