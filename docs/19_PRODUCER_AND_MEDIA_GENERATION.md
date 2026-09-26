# Producer and governed media generation

## Status and verified provider snapshot

Phase 3 introduces the provider-neutral `production` bounded context. As verified on 2026-09-13,
the current `production_deep` route uses OpenAI `gpt-5.6-sol` with high reasoning and a
12,000-token output ceiling. Researcher also uses Sol at medium reasoning; Creative Strategist uses
Sol at high reasoning. The
current `production_image` route uses the exact documented
`gpt-image-2.5-sunburst-2026-09-08` snapshot. Historical AgentRuns and GenerationJobs retain their
older Terra/Astra and GPT Image 2 model and route fields.
The verified video route is BytePlus LAS Enhanced `dreamina-seedance-2-5-260628`, documented
2026-08-17 in `ap-southeast-1`.

## Boundary and flow

```text
Approved CreativeConcept
          ↓
Producer on GPT-5.6 Sol (one structured inference, zero tools)
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

New runs use `production_planning.v2` and the immutable `producer_v5_contract_v2` prompt revision.
The complete approved CreativeConcept remains provider input and is the primary production
authority. Product input is the explicit `producer_product.v1` allowlist: identity, bounded visual
truth, exact referenced allowed claims, and complete production/publication safety constraints.
Research input is `producer_research.v1`: only findings named by the concept's exact
`supporting_research_refs`, with no unrelated findings or gaps. Product/Research snapshots remain
complete immutable sources and their original IDs/digests stay frozen on AgentRun. Projection
versions/digests and referenced claim/finding identities are frozen separately. Historical
`production_planning.v1` runs reconstruct their original full-snapshot provider context. See
ADR-048.

Fixed v2 context that exceeds the unchanged input allowance fails before reservation or AgentRun
creation. Its Audit diagnostic contains numeric allowance/bounds, projection versions, section byte
sizes, and reference counts only—never Product text, Research text, prompts, or model content.

`production.production_plan.v2` is strict JSON Schema 2020-12. Disjoint structural variants prevent
contradictory shot source bindings and IMAGE/VIDEO duration shapes. Deterministic validation still
owns frozen scene order, ordinals, shot/segment relationships, Asset authorization, provider
neutrality, duration/count limits, pricing dimensions, and digest integrity. Rejections use stable
`PRODUCTION_PLAN_INVALID` plus bounded finite Audit diagnostics; raw model output is never retained.
Historical v1 contracts remain readable. See ADR-049.
The canonical v2 intersection remains unchanged. ADR-050 defines its provider-only equivalent as
four complete strict `shot` alternatives and two complete strict `segment` alternatives, compiled
and digest-addressed before run admission.

An eligible terminal v1 validation failure may be continued only through the explicit
`make live-producer-replace` operator path. It creates one new pending v2 AgentRun with immutable
lineage and current provenance; it does not call the provider. The original response usage/cost and
zero unknown cost remain unchanged.

Application code calculates cost. Approval is append-only and binds the plan digest, exact image and
video route versions, pricing versions, maximum cost, and currency. This product decision does not
replace Tool Gateway approval or current permission, rights, capability, connector, and budget checks.

## Provider routes and pricing

`production_deep` freezes route `openai-gpt-5.6-sol-production-2026-09-13` and pricing snapshot
`openai-gpt-5.6-sol-2026-09-13`: USD 4/M input, USD 0.40/M eligible cached input, and USD 20/M
output. Reservations conservatively price all input as uncached. The 32,000-token bound permits at
most 20,000 input plus 12,000 output tokens, or USD 0.32 per run and USD 6.40 across the configured
20-run daily ceiling.

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

`production_image` resolves to the exact `gpt-image-2.5-sunburst-2026-09-08` snapshot. Medium/high
intent maps deterministically to bounded
provider quality/size. Until an official stable usage-price surface is encoded, approval uses
conservative versioned per-image reservations and reconciles actual usage/cost when returned; the UI
must label this an estimate.

## Execution, recovery, and Assets

Tool contracts are `media.image.generate`, `media.video.generate.start`,
`media.video.generate.status`, and `media.video.generate.import`. Their only input is
`generation_job_id`; trusted code resolves immutable specs. Producer has `max_tool_calls=0`. The
explicit `make media-tools-bootstrap` command creates immutable versions and activations in
development/test. `make media-execution-bootstrap` then creates its tenant-scoped deterministic
policy principal and grants only those four contracts. Production promotion remains a
platform-controlled deployment operation.

The Producer Agent is planning-only and never receives these tools. A separate non-model-routed
`media_execution_workload` Agent Registry principal supplies the narrow Tool Gateway policy for the
deterministic production worker. Production reloads the human cycle/Producer initiator and current
membership separately from that execution principal, while the deployment workload identity is
recorded on each GenerationJob. This keeps human authority, policy identity, and execution identity
distinct.

Jobs move through `PENDING_APPROVAL`, `READY`, `STARTING`, `PROCESSING`, `IMPORTING`, then
`SUCCEEDED`, `FAILED`, or `OUTCOME_UNKNOWN`. A possibly accepted Seedance start whose response is
lost is unknown and is never automatically retried. A durable provider task ID resumes polling the
same task after restart. Temporal uses timers/activities and an intentional maximum of three days.
The transactional approval event is consumed through Inbox semantics; the handler reloads the
committed tenant-scoped jobs and starts the deterministic `MediaProductionWorkflow` with IDs only.

Successful temporary output locators trigger immediate bounded download, signature/MIME/size
validation, SHA-256 calculation, private ObjectStore import, and a normal READY generated Asset.
Generated media has conservative configurable rights. Multi-parent lineage supports Product Asset →
generated image → generated video. Final editing/compositing is owned by the deterministic FFmpeg
assembly boundary; social publishing remains out of scope.

Provider composition defaults disabled. Live smoke targets require an explicit flag and credential
and are never CI dependencies. Metrics use bounded dimensions without tenant/Plan/Job/Asset IDs.

## Activated worker authority

`creative_marketer_api.production_worker` is the independent media workload. Every activity carries
only Tenant, Plan, and Job identities. Immediately before each Tool operation,
`SqlAlchemyGenerationAuthority` reloads the Job, exact approved Plan and decision, current source
snapshots and Creative approval, route/pricing/capability binding, reservation, and every referenced
Asset's READY state, bytes, digest, and current generation rights. Any mismatch fails before a
provider call. The actual provider remains reachable only through the existing Tool Gateway,
current tenant permission, idempotency ledger, and audit boundary.

Image and video fake adapters use the identical path and create ordinary private generated Assets;
they are allowed only in development/test and are labeled `LOCAL DEMO`. Real adapters additionally
require `ALLOW_BILLABLE_MEDIA=true`. Staging/production also require deployment-issued workload
identity configuration. See the local runbook for exact no-cost and optional billed commands.
