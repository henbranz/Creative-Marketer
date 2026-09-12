# ADR-031 — Media worker authority and local activation

Status: Accepted

## Context

An approved ProductionPlan can wait before execution. During that interval its source rights,
Creative approval, Product/Research freshness, route, pricing, capability, reservation, or tenant
policy can change. Temporal arguments and an initiating User identity are not durable authority.
Local development also needs a visible no-cost journey without creating a provider bypass.

## Decision

Credential-bearing media execution runs only in an independent workload-authenticated worker.
Every Tool operation begins with `SqlAlchemyGenerationAuthority` reloading the canonical Job,
approved Plan binding, current sources, current Asset bytes/rights/digests, exact route/pricing and
spend reservation. Workflow and Tool input contain only IDs. The initiating User is retained as
provenance; the worker identity is persisted and audited separately.

Providers—including deterministic fake providers—are reachable only through the existing Tool
Gateway, permission engine, idempotency, audit, and output validation/import path. Fake providers
are development/test-only. Real media additionally requires an explicit billable-media switch;
deployed environments require an issued workload identity. Obsidian and the UI remain read/control
adapters over canonical APIs and convey no execution authority.

## Consequences

Execution costs additional authoritative reads and explicit composition, but delayed jobs fail
closed under changed policy and cannot silently fall back to a different provider or price. Local
fixtures exercise the production seams without cost and are unmistakably labeled. Operational
reconciliation remains required for unknown external outcomes.
