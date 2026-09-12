# Producer and governed media generation

## Status and verified provider snapshot

Phase 3 introduces the provider-neutral `production` bounded context. The task-proposed names
`gpt-6-astra` and `gpt-image-2.5-sunburst-2026-09-08` were not present in official OpenAI model
documentation on 2026-09-12 and are therefore not represented as real models. The product-facing
role remains **Astra Producer**, while the pinned `production_deep` route uses the verified
`gpt-5.6-sol` model with high reasoning. The `production_image` route uses verified `gpt-image-2`.
The verified video route is BytePlus LAS Enhanced `dreamina-seedance-2-5-260628`, documented
2026-08-17 in `ap-southeast-1`.

## Boundary and flow

```text
Approved CreativeConcept
          ↓
Astra Producer (one structured inference, zero tools)
          ↓
Provider-neutral immutable ProductionPlan
          ↓
Append-only plan approval + route/pricing/max-cost binding
          ↓
MediaProductionWorkflow (IDs and statuses only)
     images first → dependent video segments
          ↓
Tool Gateway → BudgetGuard → credentialed provider adapter
          ↓
validated bytes → private immutable Asset + lineage
          ↓
Knowledge Graph → Obsidian
```

Production owns plans, scenes, shots, generation segments, decisions, jobs, spend evidence, and
execution state. Catalog owns Assets and the neutral many-to-many `asset_lineage` relation.
Production does not own Product, Research, Creative, Agent governance, storage, or credentials.

## Producer context and plan

Producer accepts only a Concept whose latest decision is `APPROVED_FOR_PRODUCTION`. Its ConceptSet
must still bind the current ProductKnowledgeSnapshot V2 and ResearchSnapshot; otherwise it fails
with `PRODUCTION_CREATIVE_REFRESH_REQUIRED` before inference.

`ProductionPlanningContext` freezes exact concept/set/snapshot digests, decision ID, request, and
selected Asset IDs/digests/rights. Selection is deterministic: READY, confirmed-rights images that
allow `generation_input`, ordered by Product hero, detail, lifestyle, logo, and brand reference then
Asset ID, capped at ten. Image bytes are materialized server-side only for the provider call. Object
keys, signed URLs, bytes, and provider prompts never enter Audit, events, logs, database context JSON,
Temporal, or knowledge projection.

`production.production_plan.v1` is strict JSON Schema 2020-12. It preserves concept scene order and
models provider-neutral shots with `USE_EXISTING_ASSET`, `GENERATE_IMAGE`, `GENERATE_VIDEO`, or
`MANUAL_CAPTURE`. A `GenerationSegment` is the execution unit and may combine contiguous shots.
V1 supports 9:16 short-form video, 10–60 second plans, at most eight generated images, and at most
four video segments. Seedance segments are 4–30 seconds.

Application code calculates cost. Approval is append-only and binds the plan digest, exact image and
video route versions, pricing versions, maximum cost, and currency. This product decision does not
replace Tool Gateway approval or current permission, rights, capability, connector, and budget checks.

## Provider routes and pricing

`production_video` resolves to Seedance 2.5 at fixed LAS create/status endpoints. It supports
480p/720p, 24 fps, 4–30 seconds, documented aspect ratios, audio-video generation, and bounded
multimodal references. V1 enforces provider maxima of 30 images, 10 videos, and 10 audio clips and
blocks direct real-face references unless material-library allowlisting is enabled. The provider is
disabled by default and its key exists only in infrastructure.

Versioned Seedance pricing is:

```text
0.303 USD × (output seconds + input-video seconds when present) × conversion factor
```

Factors: no input video 480p `0.6785`, 720p `1.525`; with input video 480p `0.406`, 720p `0.9125`.
Amounts use `Decimal` and round upward to six places. Failed/review-rejected jobs settle no generation
charge under the verified policy; successful status is authoritative for settlement.

`production_image` resolves to GPT Image 2. Medium/high intent maps deterministically to bounded
provider quality/size. Until an official stable usage-price surface is encoded, approval uses
conservative versioned per-image reservations and reconciles actual usage/cost when returned; the UI
must label this an estimate.

## Execution, recovery, and Assets

Tool contracts are `media.image.generate`, `media.video.generate.start`,
`media.video.generate.status`, and `media.video.generate.import`. Their only input is
`generation_job_id`; trusted code resolves immutable specs. Producer has `max_tool_calls=0`. The
explicit `make media-tools-bootstrap` command creates immutable versions and activations in
development/test; production promotion remains a platform-controlled deployment operation.

Jobs move through `PENDING_APPROVAL`, `READY`, `STARTING`, `PROCESSING`, `IMPORTING`, then
`SUCCEEDED`, `FAILED`, or `OUTCOME_UNKNOWN`. A possibly accepted Seedance start whose response is
lost is unknown and is never automatically retried. A durable provider task ID resumes polling the
same task after restart. Temporal uses timers/activities and an intentional maximum of three days.
The transactional approval event is consumed through Inbox semantics; the handler reloads the
committed tenant-scoped jobs and starts the deterministic `MediaProductionWorkflow` with IDs only.

Successful temporary output locators trigger immediate bounded download, signature/MIME/size
validation, SHA-256 calculation, private ObjectStore import, and a normal READY generated Asset.
Generated media has conservative configurable rights. Multi-parent lineage supports Product Asset →
generated image → generated video. Final editing/compositing and social publishing are out of scope.

Provider composition defaults disabled. Live smoke targets require an explicit flag and credential
and are never CI dependencies. Metrics use bounded dimensions without tenant/Plan/Job/Asset IDs.
