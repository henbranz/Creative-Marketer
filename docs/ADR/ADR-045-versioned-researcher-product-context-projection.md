# ADR-045: Versioned Researcher Product context projection

Status: Accepted

## Context

Product Brain is the complete canonical source for many capabilities, not the input contract for
each one. Sending its entire compacted snapshot to Researcher couples market synthesis admission
to growth in Creative claim authority, creative direction and asset manifests. The rev-11 local
incident exhausted evidence headroom even though no Product research field had changed.

## Decision

AgentRuntime owns a deterministic `researcher.v2` view in `researcher_context.py`. Its explicit
allowlist follows Researcher's evidence-grounded competitor, positioning, pricing, messaging,
audience, offer/promotion and creative-pattern synthesis responsibilities. It is not an extraction,
summary, partial ProductKnowledgeSnapshot, or new authority source.

Included fields (only where present):

- Brand: name. BrandProfile: industry, brand positioning, target markets, primary language,
  competitors. Full brand description is omitted in favor of the existing semantic positioning
  field; voice/tone/visual treatment are Creative controls.
- Product: name, category, short description. ProductProfile: description, features, benefits,
  materials, price/currency, target audiences, problems solved, use cases, differentiators,
  purchase objections, shipping summary, seasonality and competitor product references.
- Brief: product purpose, emotional benefits, primary/secondary audiences, positioning,
  competitive alternatives, reasons to choose, current/priority channels, conversion goal,
  current offers, legal/safety constraints and geographic restrictions.
- Audience objects explicitly allow name, description, pains, desires, motivations and objections.

Intentionally excluded: Product/Brand allowed and prohibited claim lists, claim identities,
mandatory/prohibited messaging, required publication disclaimers, CTA preferences, creative style,
tone controls/references, assets/lineage/rights, internal IDs/slugs/lifecycle/provenance bookkeeping,
margin, SKU/variant inventory and landing-page locators. Research cannot authorize Product claims:
every factual Research finding still requires exact external-evidence citations. Creative continues
to derive exact claim identities and constraints from the **full bound canonical snapshot**; no
authority or claim-validation rule changes. Legal/geographic research scoping remains included.

Every included non-empty field value is copied whole. Empty members compact recursively; list
order and complete non-empty strings are preserved. Unknown future fields do not automatically
enter context. This limits unrelated growth, not all possible research growth: a large included
field may still require operator curation. It fails closed with safe section sizes rather than
substring truncation, arbitrary field dropping, a provider summary or silent budget expansion.

New runs freeze `input_context_kind=researcher.v2`, schema version 2, and a context digest covering
the original configuration, full Product snapshot digest, manifest, selected evidence identities,
projection version and projection digest. The existing Product snapshot ID/digest/refs remain
unchanged. Admission and database reconstruction use one builder. Historical V1 reconstructs
the original full compacted view and original digest formula. Unsupported/mismatched versions or
digests fail closed. Future allowlist changes require a new version and retained old decoder.
No migration, active AgentVersion activation or historical backfill is necessary.

Evidence fitting remains the longest canonical prefix of whole blocks under the conservative
UTF-8-byte bound, with at least one block required. An oversized first block cannot be bypassed.
The existing 16,000 total / 6,000 output route and USD 0.16 run cap do not change.

Before a run or budget reservation exists, admission failure records a tenant-scoped denial audit
with fixed reason, counts, byte bounds, projection version and allowlisted section sizes only.
The public error code remains unchanged. No Product/Evidence text, API secret or prompt is stored
in diagnostics. Audit failure cannot permit inference. A rejected admission does not bind its
idempotency key; no `agent.run.requested` event is emitted.

## Consequences

Product Brain remains complete and immutable; growing authority/assets cannot consume Researcher's
envelope. Deliberately omitted context can reduce nuance (for example, publication style), which
belongs in downstream Creative validation rather than Research admission. A versioned builder
preserves truthful replay and recovery while increasing the small amount of supported legacy code.
This does not change citation quality or guarantee that captured evidence is useful. Operators
still own source relevance and should review Research gaps before downstream creative work.
