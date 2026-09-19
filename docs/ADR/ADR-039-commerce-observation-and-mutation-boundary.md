# ADR-039 — Commerce Observation Is Fact; Commerce Mutation Is Approval-Bound

Status: Accepted

## Context

Commerce reads describe external state, while inventory/refund writes can change stock or move money. Giving one provider interface or an AI agent both authorities would make observation, intent, and side effect indistinguishable.

## Decision

Use separate `CommerceReadProvider` and `CommerceMutationProvider` ports. Persist normalized provider reads as immutable tenant facts. Persist AI output only as immutable internal proposals after deterministic validation.

Inventory `SET_AVAILABLE_TO` is R5. Refund is R6. Every mutation must use an immutable ToolVersion, current permission evaluation, exact human approval binding, Tool Gateway idempotency, workload identity, audit, and reconciliation. `OUTCOME_UNKNOWN` is never blindly retried. Credentials remain connector-infrastructure concerns and are absent from domain, Agent context, events, UI, Knowledge Graph, and Obsidian.

V1 uses only `FakeCommerceProvider`; real Shopify integration is deferred. Paid attributed orders enter the existing measurement application boundary. Refund revenue reversal is deferred until measurement owns an additive immutable reversal model; purchase history is never overwritten.

## Consequences

This adds explicit workflow state and reconciliation complexity. In exchange, the system preserves external truth, prevents duplicate financial actions, supports tenant-safe audit lineage, and allows a future provider connector without granting the agent commerce credentials or mutation authority.
