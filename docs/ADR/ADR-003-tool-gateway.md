# ADR-003 — Tool Gateway for External Actions

## Status
Accepted

## Decision
All external side effects and privileged connector actions pass through a Tool Gateway.

## Rationale
Centralizes authorization, approvals, validation, budgets, idempotency, credential handling, audit, and provider normalization.

## Consequences
Agents cannot use provider SDKs directly. Tool interfaces become first-class contracts.
