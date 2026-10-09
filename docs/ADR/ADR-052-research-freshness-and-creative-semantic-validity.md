# ADR-052 — Research freshness and Creative semantic validity

Status: Accepted

## Context

ResearchSnapshot expiration means its authority is no longer current. It does not prove that an
approved CreativeConcept's referenced assertions changed. Treating every expiration as a mandatory
Creative Strategist rerun would spend model budget and replace human-approved work even when a
fresh Researcher snapshot contains the same relevant assertions.

The original CreativeConcept, ConceptSet, ResearchSnapshot, and approval are immutable historical
records. Producer must not pretend that the Concept was generated from a later snapshot, and it
must never proceed from stale Research without explicit current authority.

## Decision

Creative owns an append-only `CreativeConceptRevalidation` authority. An owner or administrator
must invoke the explicit deterministic transition after a fresh Researcher run succeeds. Refreshing
Research alone never creates a revalidation, reruns Creative, or starts Producer.

Revalidation v1 requires the latest ResearchSnapshot for the same tenant and Product to be
`current`, and the latest human decision to remain `APPROVED_FOR_PRODUCTION`. It requires the
current ProductKnowledgeSnapshot identity and digest to equal the Product authority frozen on the
ConceptSet. Every `PRODUCT_FACT` must still use an exact ProductClaimRef. A Product change therefore
returns `REQUIRES_RESTRATEGY`; claim identities are never rewritten.

For each distinct `supporting_research_refs.finding_key`, trusted code resolves the historical
finding and the same key in current Research. It computes a canonical assertion digest over exactly:

- key;
- category;
- statement;
- confidence;
- scope;
- implication.

Citations are excluded from assertion equality because refreshed evidence snapshots may have new
identities. The new finding must nevertheless be part of a normally validated, current
ResearchSnapshot with current evidence-manifest provenance. Every assertion digest must match.
Missing keys or any changed assertion field return `REQUIRES_RESTRATEGY`. V1 uses no model,
embedding, semantic search, normalization, or fuzzy equivalence.

A successful record binds the exact Concept/digest, original ConceptSet and Research authority,
current Research authority, unchanged Product authority, exact approval decision, assertion
digests, actor, result, reason codes, and semantic digest. Database triggers prohibit update and
delete. Repeating the same transition is idempotent for the same Concept/current Research/decision.

Producer retains its original path when the ConceptSet Research is current. When it is stale,
Producer requires one successful revalidation bound to the latest current Research and exact latest
approval. Provider context uses referenced findings from current Research by the original exact
finding keys. AgentRun and production-context provenance separately freeze:

- the original Creative Research snapshot/digest;
- the successful revalidation identity/digest;
- the current Research snapshot/digest;
- the unchanged Product snapshot/digest and human decision.

Failed revalidation cannot authorize Producer. A changed approval, stale/outdated target, missing
authority, or provenance mismatch fails closed.

## Consequences

Research expiration means “re-establish current authority,” not “regenerate creative.” Identical
referenced assertions can continue without another paid Creative inference, while material changes
require a new Creative run. Historical records and costs remain untouched. Operators can inspect
Research refresh history and the explicit revalidation result before admitting a Producer
contract-upgrade successor.
