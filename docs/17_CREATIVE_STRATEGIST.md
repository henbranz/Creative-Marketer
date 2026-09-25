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

Brief completeness below 80 fails with `CREATIVE_BRIEF_INCOMPLETE`. The deterministic Catalog
scoring model allows the required default Brief questions to reach 82 without Advanced enrichment,
and AgentRuntime evaluates the frozen Product snapshot with that same scoring function. Missing,
stale, expired, or outdated Research fails with `CREATIVE_RESEARCH_REFRESH_REQUIRED` before
provider execution.

## Output and validation

`creative.creative_concept_set.v1` is the canonical strict JSON Schema. Each concept has a structured
hook, 3–10 canonical scenes whose durations sum to 10–60 seconds, CTA, hypothesis, planned metric,
message points, Research references, Asset requirements, disclaimers, and production notes. The
validator rejects the whole result for unknown Research/Asset/claim references, insufficient rights,
missing disclaimers, prohibited normalized phrases, unsupported fields/enums, or exact duplicate
keys/titles/hooks/scene sequences. Phrase matching is only a deterministic safety layer; human and
future final-media compliance review remain required.

The `asset_requirement` alternatives use a nested `anyOf` with disjoint `kind` constants
(`EXISTING_ASSET` and `MISSING_ASSET`). This preserves both exact branch contracts using the documented
nested composition form; whole-schema server acceptance remains unverified. `oneOf` is rejected by
the local provider schema preflight before provider authority.

Product facts require deterministic ProductClaimRefs derived from the frozen Product snapshot.
Research supports creative strategy but never grants authority for Product facts. Concepts and sets
have semantic digests and immutable server-generated identities.

### Exact claim binding and empty authority

Claim identities come only from `brand_profile.allowed_claims` and `profile.allowed_claims` in the
bound immutable snapshot. A Product description, Brief benefit, Research finding, or Asset does not
implicitly become an approved claim. IDs are snapshot-bound `sha256:` keys, not claim text, field
paths, array indices, or abbreviations. Matching is exact; case-folding/whitespace comparison is
diagnostic only and never grants authority.

The runtime's claim-binding task contract explicitly requires `PRODUCT_FACT` to copy an allowed key
verbatim and every other kind to use `product_claim_ref=null`. An empty allowed list means no
`PRODUCT_FACT` points; the model must surface this limitation and avoid unsupported assertions,
including assertions disguised as another message kind. Each invocation narrows a copy of the
canonical message-point schema to the frozen keys (fact/non-fact nested `anyOf`, or non-fact only
when empty). The input budget estimator uses that same bound schema. The checked-in canonical V1
schema and domain validator remain authoritative and unchanged in meaning; provider adherence is
not trusted. Claim-identity validation is not a semantic proof of factual truth; human review of
all creative text remains required.

On a claim-binding failure, the entire concept set is rejected. An append-only tenant Audit record
`creative.claim_validation.failed` commits with failure/cost settlement. It contains authoritative
run/attempt/snapshot identifiers, mismatch categories and concept/message ordinals, up to eight
allowed IDs and five mismatches, total counts/truncation flags, and an allowed-list digest.
Hash-shaped offending identities can be shown exactly; arbitrary reference strings are redacted
and represented by a digest. No full response, Product prose, prompt, or provider reasoning is
logged. The bound snapshot supplies the full allowed-ID list for authorized investigation when
truncated. Diagnostics remain under the 4096-byte Audit limit; Audit failure cannot permit success.
Historical runs are not backfilled or reclassified.

## Runtime and cost

The explicit `creative_strategist` capability uses the common AgentRuntime lifecycle, Agent Registry,
ModelRouter, ModelAttempt boundary, daily ledger, response-before-validation durability, recovery,
Audit, Outbox, and telemetry. The current `creative_balanced_v2` route uses OpenAI `gpt-5.6-sol`,
high reasoning, 16,000 maximum output tokens, 40,000 total tokens, one model call, zero tool calls,
and a USD 0.416 run ceiling. The versioned prices are USD 4/M input, USD 0.40/M eligible cached input,
and USD 20/M output; reservations conservatively price all input as uncached. Thus 24,000 input plus
16,000 output tokens costs at most USD 0.416; even 40,000 all-input tokens cost only USD 0.160. The
measured 21,324-token conservative Creative input bound plus 16,000 output tokens fits the 40,000
envelope without context truncation. The daily ceiling is 20 runs/USD 8.32.

The prior `creative_balanced` route remains installed with its immutable 8,000-output/32,000-total
policy so historical runs and legitimate exact recovery can resolve their frozen route and pricing.
Historical Terra runs likewise retain their frozen model, route, pricing, and cost provenance.
An authoritative `FAILED_RESPONSE` caused by `MAX_OUTPUT_TOKENS` is terminal and cannot use ordinary
recovery. After an operator explicitly requests a transition, the governed normal admission path may
create one fresh run on the active version; it never rewrites or recovery-links the terminal run.

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
