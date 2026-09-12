# ADR-029: Human knowledge graph and Obsidian projection

- Status: Accepted
- Date: 2026-09-12

## Context

People need to inspect durable cross-agent provenance as a navigable graph. Making an external Markdown vault authoritative would bypass application validation, tenant identity, audit, approvals, and immutable artifact rules.

## Decision

Add a provider-neutral Knowledge Projection context that reads tenant-scoped public data from canonical bounded contexts and deterministically creates `KnowledgeNode` and `KnowledgeEdge` values. PostgreSQL domain state remains canonical. A disposable forced-RLS node cache and append-only revision ledger support bounded full and incremental authenticated reads.

Obsidian is the first local rendering adapter. Its bridge owns vault filesystem access, stable collision-safe filenames, Markdown/Properties/wikilinks, cursor persistence, generated-region replacement, user-note preservation, and safe archival of tombstones. The bridge is strictly Creative Marketer → Obsidian. No Markdown edit can mutate canonical state. Future feedback must create an authenticated candidate annotation through an application API.

Projection field allowlists and final privacy filtering exclude credentials, signed URLs/object keys, raw HTML/provider responses, prompts, system instructions, and hidden reasoning. The API derives tenant authority only from `ExecutionContext`. An `obsidian://open` URI is a deterministic navigation hint, not an authorization primitive.

## Consequences

Graph/read-model and filesystem concerns remain extractable without coupling domain contexts to Obsidian. Projection storage may be discarded and rebuilt. Incremental clients receive at-least-once-safe upserts/tombstones and must apply them idempotently. Revision history consumes PostgreSQL storage until a later retention/compaction ADR. A bidirectional knowledge workflow, daemon/plugin distribution, and external graph database are explicitly deferred.
