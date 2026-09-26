# ADR-049 — Production Plan contract and semantic validation hardening

Status: Accepted

## Context

The first live Producer invocation using `production_planning.v2` completed at the provider but
failed during local output validation. The failed output was intentionally not retained, so its
exact invariant cannot be reconstructed. Audit of `production.production_plan.v1` found structural
states the schema allowed but the domain rejected: contradictory source-strategy bindings and
IMAGE/VIDEO duration combinations. Other rules depend on frozen scene, shot, and Asset authority
and cannot be expressed safely in a static JSON Schema.

Validation also priced IMAGE segments before all shot relationships were checked. An unknown or
incompatible shot reference could therefore escape as a Python `StopIteration`, `KeyError`,
`TypeError`, or `ValueError` and collapse into generic `MODEL_INVALID_OUTPUT`.

## Decision

Production output validation has three explicit layers:

1. `production.production_plan.v2` is the provider structural contract. Nested disjoint `anyOf`
   variants bind each source strategy to its allowed `existing_asset_id` and
   `image_generation_spec` shape. IMAGE segments require null duration; VIDEO segments require a
   4–30 second integer. The canonical schema retains uniqueness constraints even though the
   proven OpenAI normalization removes `uniqueItems` from provider transport and application
   validation re-enforces it.
2. Deterministic semantic validation preserves exact frozen Creative scene identity/order,
   contiguous ordinals, global shot identity, segment cross-references, media/strategy agreement,
   authorized frozen Asset references (including image-spec references), total duration,
   generation counts, provider neutrality, pricing dimensions, and semantic digest integrity.
   All relationships are checked before pricing dereferences them.
3. Rejections use stable `PRODUCTION_PLAN_INVALID` classification plus a finite code-owned
   invariant reason and bounded metadata. Audit metadata may include ordinals, counts, finite
   enums, contract version, schema validator name, path depth, and Asset UUIDs. It never includes
   prompts, raw provider JSON, Product/Research text, generated prose, or secrets.

Historical v1 schemas and AgentVersions remain readable and reconstructable. Producer capability
dispatch accepts v1 and v2 by the immutable AgentRun contract version; only newly admitted runs use
v2 and `producer_v5_contract_v2`. Context remains `production_planning.v2`; route, reasoning,
32K total tokens, 12K output tokens, USD 0.32/run, and USD 6.40/day do not change.

A terminal v1 Producer run with exactly one authoritative completed response, measured cost, zero
unknown cost, no materialized result, and historical `MODEL_INVALID_OUTPUT` may receive one
explicit successor. Owner/admin action creates a new pending v2 AgentRun with a new identity and
`recovery_of_run_id` lineage. Current Product/Research/Creative approval provenance must still
resolve, the hardened AgentVersion must be active, active-run admission and the unique lineage
constraint prevent concurrency/duplicates, and creation does not execute a provider call.

## Consequences

Strict structured output rejects more impossible states before domain construction, while frozen
authority remains deterministic and fail-closed. Future failures are diagnosable without retaining
arbitrary model content. The historical failed run, provider response identity, usage, actual cost,
and zero unknown cost remain immutable. Operators must bootstrap/activate the new Producer version,
create the explicit replacement while the worker is stopped, inspect it, and only then start one
controlled worker execution.
