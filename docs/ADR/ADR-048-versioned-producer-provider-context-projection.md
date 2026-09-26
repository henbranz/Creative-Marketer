# ADR-048 — Versioned Producer provider-context projection

Status: Accepted

## Context

Producer plans execution of an already approved CreativeConcept. `production_planning.v1` sent the
complete ProductKnowledgeSnapshot, every Research finding, and every Research gap in addition to
the complete approved concept. A live admission reconstruction measured a conservative 26,500-byte
input bound against the existing 20,000-token input allowance. The approved concept alone was not
the problem: unrelated Product and Research growth had become implicit Producer input.

Product and Research snapshots are complete canonical authorities shared by several capabilities;
they are not automatically Producer's provider contract. Producer must not re-run Creative strategy
or Research, and context pressure must not silently truncate an approved concept or source text.

## Decision

New runs use `production_planning.v2`. The full approved CreativeConcept remains the primary
provider-facing production instruction, including every scene, message, hook, audience, CTA,
hypothesis, production note, required Asset, disclaimer, Product claim reference, and supporting
Research reference.

AgentRuntime builds two independently versioned deterministic views:

- `producer_product.v1` includes Product/brand identity, Product description and materials for
  visual truth, only the exact allowed claim identities referenced by `PRODUCT_FACT` message
  points, and the complete prohibited-claim, mandatory/prohibited-message, disclaimer, legal/safety,
  and geographic constraints. Unknown and unrelated Product fields do not enter automatically.
- `producer_research.v1` resolves only `supporting_research_refs` from the exact frozen Research
  snapshot. It sends the referenced finding's key, category, statement, confidence, scope, and
  implication. Citations, unrelated findings, recommended sources, and unreferenced gaps are not
  provider input. Research explains approved rationale and never grants Product claim authority.

Missing Product claim or Research finding references fail before AgentRun creation. Values are
copied whole; there is no substring truncation, provider summary, pressure-based field dropping, or
budget expansion.

The AgentRun still freezes the complete Product and Research snapshot IDs/digests, approved
concept/set/decision provenance, selected Asset identities/digests/rights, and production request.
Its v2 context digest additionally binds the immutable AgentVersion configuration, original
production-context digest, both projection versions/digests, referenced Product claim identities,
and referenced Research finding identities/content digests. Provider projections are views, never
replacement authority records.

Admission and durable execution use the same builder. Reconstruction validates context kind,
version, full source digests, projection metadata, reference lists, and context digest. Historical
`production_planning.v1` runs retain the original complete Product/Research serialization and digest
formula. Unsupported or mismatched versions fail closed; no historical row is backfilled.

Post-response ProductionPlan validation is unchanged. It reconstructs the full immutable
ProductionPlanningContext at runtime, preserves exact scene order, rejects unauthorized Assets,
and retains the provider-neutral shot, segment, cost, and generation constraints.

If fixed v2 context still exceeds the route envelope, admission commits only bounded numeric Audit
diagnostics and creates no AgentRun, reservation, outbox event, ModelAttempt, or provider authority.

## Consequences

The approved Creative strategy and all deterministic production safeguards remain intact while
unrelated Product/Research growth no longer consumes Producer's envelope. The live incident fixture
moves from 26,500 to 16,129 conservative input bytes without changing route or spend policy.
Future projection field changes require a new projection/context version and retained historical
decoder. Operators must activate the immutable `producer_v4_context_v2` AgentVersion before a new
Producer v2 request.
