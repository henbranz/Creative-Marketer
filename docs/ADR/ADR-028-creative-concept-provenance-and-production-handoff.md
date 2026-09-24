# ADR-028: Creative concept provenance and production handoff

- Status: Accepted
- Date: 2026-09-12

## Decision

CreativeConceptSet and CreativeConcept content is immutable and binds exact Product, Research, and
Asset-manifest provenance. Product factual messages require a frozen ProductClaimRef; Research may
support an angle but cannot authorize Product truth. Human workflow decisions are separate,
append-only records. `APPROVED_FOR_PRODUCTION` is a Product workflow state, not a Governance Approval
Engine decision, and emits only stable identifiers and digests. A future Producer consumes the stable
approved Concept identity through an application read model rather than ORM or JSON array position.

## Consequences

Creative history remains reproducible and reviewable after Product or Research changes. Revisions
require a new run or an explicit future revision model. Producer and publishing spend still require
their own policy and Tool Gateway decisions. Deterministic prohibited-phrase checks reduce obvious
failures but do not replace semantic or final-media compliance review.

## Claim-binding clarification — 2026-09-23

Allowed-claim identities are exact snapshot-bound keys. No fuzzy/case/whitespace normalization,
claim-text substitution, automatic claim extraction from other Product fields, or Research-derived
Product authority is permitted. Empty authority excludes factual Product message points; it does
not grant an exemption from validation. The runtime explicitly communicates these semantics and
narrows an invocation-local copy of the canonical schema to the exact allowed keys. Canonical
output validation and whole-set domain rejection remain in force independently of provider schema
adherence. Existing AgentVersions, snapshots, failed runs, costs, approvals, and plans are unchanged.

Bounded claim-validation evidence belongs in tenant Audit, atomically with terminal failure and
known provider cost. Diagnostic categories distinguish absent authority, missing/unknown/formatted
IDs, claim text used as an ID, references on non-facts, and missing disclaimers. Arbitrary model
strings are redacted; known-shaped IDs, digests, ordinals, bounded lists, counts, and truncation
flags support diagnosis without storing unrestricted responses or sensitive Product prose.
