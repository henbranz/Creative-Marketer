# ADR-044: Human claim edits rotate frozen Product authority

Status: Accepted

## Context

Creative authority is derived only from `ProductProfile.allowed_claims` and
`BrandProfile.allowed_claims` in an immutable ProductKnowledgeSnapshot. A complete
Brief or evidence-backed Research finding does not populate either allow-list.
The previous UI displayed Product claims but offered no narrow human editor;
mutable profile edits could leave the latest frozen snapshot unchanged.

## Decision

The Catalog owns a narrow authenticated `PUT /v1/products/{product_id}/claims`:
`scope` (`product` or `brand`), `allowed_claims`, and `expected_claims`. It accepts
human text, never caller-supplied claim IDs, actor IDs or tenant authority.
Only an authenticated human with active OWNER/ADMIN membership may use it.
Existing domain limits, trimming, duplicate and prohibited-claim checks apply.
Claim-reference matching in Creative remains exact; it is not made fuzzy.

The server locks the tenant-scoped parent Brand row, compares the expected
allow-list, and rejects stale edits with a content-free 409. It updates only
allowed claims and profile `updated_at`; unrelated profile/Brief fields are
preserved. The existing Brief/source revision is the shared knowledge revision:
increment it without rewriting Brief content, then append a V2 snapshot, snapshot
event and compact audit in the same transaction. A no-op does not advance it.
There is no new table, schema migration, backfill or automatic data repair.

Product changes affect one product. Brand claims retain the existing semantics:
they apply to **every product in the Brand**. Brand changes therefore rotate all
its products' source revisions and snapshots atomically, including archived
products. Human UI copy must warn about this scope. Full legacy profile updates
also rotate snapshots if allowed claims change; they cannot leave stale frozen
authority behind. Product creation and explicit snapshot creation use the same
Brand lock. Concurrent Brief writes retain optimistic revision conflict checks.

Canonical IDs remain derived from the frozen snapshot digest, section, ordinal
and text. Even unchanged claim text receives a new identity when snapshot
authority changes. No historical snapshot, AgentRun, ResearchSnapshot or cost
record is rewritten. The existing Research freshness comparison reports
`outdated` for a different Product snapshot digest (or `stale` if expired).
Creative readiness therefore requires refreshed Research; saving claims does
not start it or any other agent, provider, workflow or production approval.

The UI separates saved Approved Claims, editable unapproved drafts, and optional
verbatim candidates from stored Product features/materials and Brief purpose/
offers. Candidates are not AI-verified and are not extracted from Research or
competitor evidence. Add-to-draft and explicit Approve & save are separate
actions. Each scope retains its own original expected allow-list so saving one
cannot silently authorize overwriting a concurrent edit to the other.

## Consequences

The shared source revision may advance without a Brief text edit; consumers must
treat it as a knowledge revision, not a count of text changes. Historical IDs
remain resolvable in their original snapshots, not valid in new run authority.
Claims may remain empty; zero authority is a valid state, not permission to
invent factual claims. Human approval is a statement of responsibility, not an
independent verification of truth.

Brand locking and synchronous fan-out favor atomic safety over high write
throughput. If very large catalogs require batching, a later design must make
pending authority revisions explicitly block stale downstream execution; an
eventual update that leaves old snapshots usable is not an acceptable shortcut.
`expected_claims` is a compare-and-swap of the entire ordered allow-list, not a
per-claim revision ledger; an intervening edit that returns to the exact same
list is semantically equivalent. Durable per-claim verification is not introduced.
