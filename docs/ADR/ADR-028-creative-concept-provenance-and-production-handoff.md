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

