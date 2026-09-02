# ADR-005 — Replaceable Provider Adapters

## Status
Accepted

## Decision
Model, media, social, commerce, and other external vendors are accessed through provider interfaces/adapters. Providers are not assumed to be fully interchangeable.

Adapters expose capability metadata including supported operations, models/versions, aspect ratios or formats, synchronous/asynchronous behavior, moderation requirements, regions, cost metadata, rate limits, and feature limitations. Stable domain operations remain provider neutral; provider-specific extension data is confined to adapter/configuration boundaries.

Important invocations persist the selected provider, model/version, cost, external job reference, and input/output lineage.

## Rationale
Vendor quality, pricing, APIs, availability, safety requirements, and regional capabilities change rapidly. Explicit capability matching avoids both hard coupling and false lowest-common-denominator assumptions.

## Consequences
Provider selection must validate capabilities before execution. Provider-specific features may require discovery and extension fields but must not leak into core domain contracts. Switching providers can still require product and workflow decisions; the abstraction does not promise transparent substitution.
