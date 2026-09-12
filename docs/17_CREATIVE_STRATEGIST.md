# Creative Strategist

## Human graph projection

Concept sets, concepts, and append-only decisions are projected with direct links to the Product,
exact Product/Research snapshots, Creative Strategist AgentRun, supporting findings/evidence,
referenced Assets, and decisions. Tenant-visible rationale and production notes are allowed;
provider reasoning is not. Obsidian review notes remain personal navigation context and never change
Creative decision state.

## Purpose and boundary

Creative Strategist is the second governed AI capability. It turns an exact ProductKnowledgeSnapshot
V2, one exact current ResearchSnapshot V1, and the snapshot's Asset manifest into three to five
structured short-form vertical-video concepts. It does not browse, invoke tools, generate media,
publish, predict performance, or retain session memory.

```text
ProductSnapshot + ResearchSnapshot + Asset manifest
                         ↓
             Creative Strategist AgentRun
                         ↓
       CreativeConceptSet → CreativeConcept
                         ↓
            CreativeConceptDecision
                         ↓
                 future Producer
```

## Frozen context

`creative_strategy.v1` binds Product snapshot ID/digest/schema, Research snapshot
ID/semantic-digest/originating run, bounded Asset IDs, and the bounded strategy request. The digest is
deterministic and the references are immutable. Research findings are application-valid data, not
system authority; raw web evidence never enters this context. Only READY Asset metadata whose rights
include `generation_input` may be returned as an existing input. No Asset is required: missing shots
are first-class requirements.

Brief completeness below 80 fails with `CREATIVE_BRIEF_INCOMPLETE`. Missing, stale, expired, or
outdated Research fails with `CREATIVE_RESEARCH_REFRESH_REQUIRED` before provider execution.

## Output and validation

`creative.creative_concept_set.v1` is the canonical strict JSON Schema. Each concept has a structured
hook, 3–10 canonical scenes whose durations sum to 10–60 seconds, CTA, hypothesis, planned metric,
message points, Research references, Asset requirements, disclaimers, and production notes. The
validator rejects the whole result for unknown Research/Asset/claim references, insufficient rights,
missing disclaimers, prohibited normalized phrases, unsupported fields/enums, or exact duplicate
keys/titles/hooks/scene sequences. Phrase matching is only a deterministic safety layer; human and
future final-media compliance review remain required.

Product facts require deterministic ProductClaimRefs derived from the frozen Product snapshot.
Research supports creative strategy but never grants authority for Product facts. Concepts and sets
have semantic digests and immutable server-generated identities.

## Runtime and cost

The explicit `creative_strategist` capability uses the common AgentRuntime lifecycle, Agent Registry,
ModelRouter, ModelAttempt boundary, daily ledger, response-before-validation durability, recovery,
Audit, Outbox, and telemetry. The initial `creative_balanced` route uses the existing versioned
OpenAI/gpt-5.6-terra pricing snapshot, medium reasoning, 8,000 maximum output tokens, 16,000 total
tokens, one model call, zero tool calls, and a USD 0.20 run ceiling. The worst configured pricing case
is USD 0.112.

Existing `ResearcherWorkflow` history is unchanged. New Creative runs use the future-only thin
`AgentExecutionWorkflow`, and the event bridge resolves authoritative AgentRun type before selecting
the workflow. Temporal history contains locators and outcomes only.

## Review and Producer handoff

Generated content is immutable. OWNER/ADMIN decisions are append-only `SHORTLISTED`,
`APPROVED_FOR_PRODUCTION`, or `REJECTED`; MEMBER is read-only. Locking the Concept serializes
concurrent decisions and the last committed decision is current. Production approval emits a
reference-only event. It means the concept may become a ProductionPlan input; it does not authorize
provider spend, media generation, or publishing.

The Producer now consumes `ApprovedCreativeConcept`, translates provider-neutral scenes and Asset
requirements into an immutable ProductionPlan, and rechecks context freshness before inference.
This handoff grants planning authority only. A separate append-only ProductionPlan approval binds
media routes, pricing, currency, and maximum cost; deterministic workflow code must still pass every
paid side effect through Tool Gateway.
