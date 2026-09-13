# ADR-030 — Production plan and media-provider boundary

Status: Accepted

## Context

Approved strategy must become executable media instructions without granting a model external
authority, coupling canonical data to provider JSON, hiding spend, or weakening Asset rights.

## Decision

- Producer performs one structured inference through logical `production_deep`, receives at most
  ten authorized visual inputs, and has no tools.
- `ProductionPlan` is immutable and provider-neutral. `GenerationSegment`, not Shot, is the provider
  unit and can cover contiguous shots.
- Images use `ImageProvider`; videos use `VideoProvider`. The current verified routes are OpenAI
  `gpt-image-2.5-sunburst-2026-09-08` and BytePlus `dreamina-seedance-2-5-260628`.
- All paid generation uses exact Tool Gateway contracts with only `generation_job_id`. Deterministic
  workflow code—not an agent—executes them.
- Human approval binds plan digest, route/pricing versions, maximum cost, and currency. Permission,
  pricing, capability, budget, and Asset rights are revalidated before I/O.
- Seedance pricing uses base rate × billed duration × conversion factor. Ambiguous starts become
  `OUTCOME_UNKNOWN` and are never automatically retried.
- Provider outputs are immediately validated/imported as private immutable Assets. Neutral,
  same-tenant Asset lineage permits multiple parents.
- Production is a canonical context; Knowledge Graph and Obsidian are disposable read projections.
- Final assembly/compositing and publishing remain separate future capabilities.

## Consequences

Explicit persistence and workflow complexity buy reviewable cost, recovery, and lineage evidence.
Provider changes do not rewrite plans but intentionally invalidate execution approval.

Provider availability changed after this ADR was first accepted. ADR-033 records the historical
Astra/Sunburst activation, and ADR-034 records the current Sol reasoning-model policy, without
rewriting immutable historical records.
