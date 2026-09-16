# ADR-037 — Performance Facts Are Observed; Attribution Is Explicit

- Status: Accepted
- Date: 2026-09-16

## Context

Creative performance data arrives with provider-specific availability, cumulative semantics, retries, delays, and incomplete conversion signals. Treating absent values as zero, mutable rollups as facts, or correlation as attribution would produce confident but false intelligence. Customer PII and public tracking tokens would also spread into analytics, audit, and human knowledge surfaces without an early boundary.

## Decision

Introduce a deterministic measurement bounded context in the modular monolith. Provider-neutral collection normalizes available metrics into immutable `PerformanceObservation` facts with explicit semantics and content deduplication. Versioned backend formulas create derived values, and immutable `PerformanceSnapshot` records exact observation provenance. Currencies remain separate and spend-derived metrics remain unavailable until trustworthy spend exists.

Attribution requires an opaque, high-entropy `AttributionReference` bound to one exact Publication and fixed destination. Only its hash is stored. `ConversionObservation` is PII-free and idempotent by tenant/source/external id. V1 creates `AttributionResult(method=DIRECT_REFERENCE)` only for an exact matching reference; unmatched conversions remain unattributed and no heuristic attribution is permitted.

Collection uses a distinct `SocialMetricsProvider` read port. Phase 5 enables only a deterministic fake adapter. A finite Temporal schedule coordinates durable checkpoints using identifiers only, while PostgreSQL remains business truth. Publishing emits the eligibility fact but does not wait for measurement.

## Consequences

- A future Intelligence Agent receives traceable facts rather than mutable provider totals or inferred causality.
- Missing provider capability is visibly different from zero.
- Replays cannot duplicate facts, conversions, revenue, or snapshots.
- RLS, immutable triggers, PII minimization, and safe projections constrain data exposure.
- Exact attribution undercounts conversions without a reference by design; this is more truthful than heuristic over-attribution.
- Real social metrics and commerce adapters, spend ingestion, probabilistic attribution, cross-tenant benchmarks, and an analytics warehouse remain separate future decisions.

## Rejected alternatives

- Reuse the publishing connector for metrics: it couples write authority and credentials to a read concern.
- Store only mutable daily totals: it loses ingestion lineage and makes replay correctness unverifiable.
- Treat missing metrics as zero: it misstates provider capability and user performance.
- Last-click/time-window attribution without an exact reference: it asserts causality the system cannot prove.
- Compute ROAS without spend: it creates a commercially consequential false metric.
