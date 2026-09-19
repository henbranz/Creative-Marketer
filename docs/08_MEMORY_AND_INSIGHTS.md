# 08 — Memory & Insights

## Memory Layers

```text
Platform Knowledge
      ↓
Tenant / Brand Knowledge
      ↓
Product Knowledge
      ↓
Campaign / Experiment Knowledge
      ↓
Agent Working Memory
```

Agents receive the minimum relevant layer(s).

Research evidence is durable provenance, not automatically trusted knowledge or Agent memory.
`ResearchContextManifest` selects at most one latest evidence reference per active Source and never
concatenates all page text. Future Researcher runs must further select only task-relevant blocks;
promotion into Product/Brand knowledge requires an explicit validation workflow. Embeddings and
vector/full-text indexing remain deferred until that retrieval contract exists.

## Working Memory

Short-lived context for a run/workflow. It is not automatically promoted to durable knowledge.

## Durable Knowledge

Durable memory must come from explicit structured entities:
- product data
- approved brand rules
- research snapshots
- experiment results
- validated insights
- user decisions

## Insight Object

A valid insight must contain:

```text
statement
evidence_refs
sample_size
metric
baseline
observed_delta
confidence
scope
provenance
created_at
valid_until
status
```

## Scope Examples

```text
Product X
Instagram
Organic video
Israel
Age 25–34
2026-Q3
```

A result in this scope must not silently become a rule for all products/channels/countries.

## Insight Lifecycle

```text
Observation
   ↓
Candidate
   ↓
Proposed
   ↓
Validated
   ↓
Active
   ↓
Expired / Invalidated / Superseded
```

## Revalidation

Insights should be revalidated when:
- TTL expires
- contradictory evidence appears
- sample grows materially
- platform behavior changes
- product positioning changes
- season changes
- user explicitly requests refresh

## User Feedback as Data

Store:
- concept rejection
- manual edits
- approval delay
- caption edits
- asset replacement
- campaign pause

But distinguish user preference from market-performance evidence.

## Creative Feature Extraction

`creative-features-v1` learns only from features already present in canonical artifacts, such as:

- hook_type
- duration
- voiceover
- shot_count
- caption_length
- CTA
- storytelling
- problem_solution
- price_visible
- discount
- aspect_ratio

Unknown features remain unknown. Pixel-level properties such as person presence, emotion, lighting,
camera motion, or UGC style are not inferred in V1. This supports traceable feature-level learning
without inventing labels.

Intelligence Agent output is not memory or Product truth. It creates immutable `CANDIDATE`
insights only. A human may propose one for testing or reject it; `VALIDATED` and `ACTIVE` require
future evidence rules. Synthetic candidates can never enter those states.

## Cross-Tenant Learning

Future aggregated learning may become a moat, but only when:
- privacy policy allows it
- data is aggregated/anonymized appropriately
- tenant contractual settings allow it
- no proprietary tenant data is leaked
- outputs pass k-anonymity/minimum cohort thresholds or equivalent privacy controls

Do not implement cross-tenant learning implicitly.

ResearchSnapshot is evidence-grounded Agent output, not memory, Product truth, or a validated
Insight. Researcher V1 has empty memory scopes and performs no retrieval or write into vector
memory. A later governed promotion workflow may convert reviewed findings into scoped, expiring
Insights; this phase deliberately does not.
