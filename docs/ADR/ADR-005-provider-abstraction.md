# ADR-005 — Replaceable Provider Adapters

## Status
Accepted

## Decision
Model, media, social, commerce, and other external vendors are accessed through provider interfaces/adapters.

## Rationale
Vendor quality, pricing, APIs, and availability change rapidly.

## Consequences
Provider-specific features may require capability discovery/extension fields but must not leak into core domain contracts.
