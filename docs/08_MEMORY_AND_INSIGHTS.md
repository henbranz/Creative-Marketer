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

To learn beyond individual posts, model creative features such as:

- hook_type
- duration
- person_present
- voiceover
- music
- camera_motion
- shot_count
- product_visible_first_3_sec
- caption_length
- CTA
- UGC
- B-roll
- storytelling
- problem_solution
- price_visible
- discount
- lighting
- background
- aspect_ratio

Over time this supports feature-level learning rather than merely “video #123 worked.”

## Cross-Tenant Learning

Future aggregated learning may become a moat, but only when:
- privacy policy allows it
- data is aggregated/anonymized appropriately
- tenant contractual settings allow it
- no proprietary tenant data is leaked
- outputs pass k-anonymity/minimum cohort thresholds or equivalent privacy controls

Do not implement cross-tenant learning implicitly.
