# 18 — Human Knowledge Graph and Obsidian Bridge

## Authority and purpose

**Canonical Brain = Creative Marketer. Human Knowledge Graph = Projection. Obsidian = visualization/navigation adapter.** PostgreSQL domain records and immutable artifacts remain authoritative. The projection is disposable and rebuildable. Obsidian Markdown is never interpreted as a Product, Research, Creative, Governance, or AgentRuntime command.

```text
tenant-scoped canonical reads
          ↓
KnowledgeGraphProjector
          ↓
KnowledgeNode / KnowledgeEdge
          ↓
projection node cache + append-only revision ledger
          ↓
authenticated full/cursor API
          ↓
local Obsidian Bridge → Markdown Properties + wikilinks
```

## Graph contract

The provider-neutral contract includes `KnowledgeNode`, `KnowledgeEdge`, `KnowledgeNodeRef`, `KnowledgeRelationship`, `KnowledgeProjectionRevision`, and tombstoned `KnowledgeChange`. Node identity is `(node_type, canonical_id)`; display titles do not participate in identity. Obsidian syntax is confined to the local adapter.

Implemented node types are Brand, Product, ProductKnowledgeSnapshot, AgentDefinition, AgentVersion, AgentRun, ResearchSource, EvidenceSnapshot, ResearchSnapshot, ResearchFinding, CreativeConceptSet, CreativeConcept, CreativeConceptDecision, and Asset. ProductionPlan, ProductionShot, GenerationJob, FinalCreative, Publication, Experiment, and Insight remain extension types for their future owning contexts.

Relationships make the Product snapshot → Researcher run → Research snapshot/findings/evidence → Creative Strategist run → Concept set/concept/assets/decision chain navigable. ResearchFinding IDs are deterministic UUIDv5 identities derived from immutable ResearchSnapshot ID and finding key. Product claim support points to the exact ProductKnowledgeSnapshot; it is not represented as Research authority.

AgentVersion notes expose mission, version, logical model profile, declared capabilities, read scopes, output contract, runs, and graph-linked downstream artifacts. System instructions are deliberately omitted. AgentRun notes expose safe model route facts, token/cost totals, frozen input references, status, times, and result reference. Provider response IDs and provider payloads are omitted.

## Projection API and revisions

`GET /v1/knowledge/projection` returns the current full graph, edges, revision, and an opaque tenant-bound cursor. `GET /v1/knowledge/projection/changes?cursor=...&limit=...` returns at most 500 ordered upserts/tombstones and the next cursor. Authentication and tenant selection use the ordinary authoritative `ExecutionContext`; body/query tenant identity is never accepted.

On every read, deterministic projection is reconciled into `knowledge_projection.projection_nodes`. A changed digest appends one immutable `projection_changes` revision; disappearance appends a tombstone. Unchanged refreshes append nothing. This cache/ledger is forced-RLS tenant state, contains no authority, and can be dropped and rebuilt from cursor zero. A cursor is a traversal position, not authorization. Revision ordering is per database ledger; clients assume no semantic global event order.

## Local bridge

The independently runnable bridge requires `OBSIDIAN_VAULT_PATH`, `CM_API_BASE_URL`, `CM_TENANT_ID`, and an uncommitted `CM_API_TOKEN`:

```bash
make obsidian-sync
make obsidian-rebuild
```

The current development credential is the same explicit local authenticated identity used by the UI. Production must replace it with a user-scoped session or personal-access token carrying tenant membership and read-only projection scope; production admin credentials are forbidden.

The server never receives the vault path. The bridge resolves every target beneath the configured root and rejects absolute paths, `..`, and symlink escape. Atomic local cursor state lives at `.creative-marketer/state.json`; it contains node paths/titles and no token. Tombstones move managed notes to `.creative-marketer/archive/` rather than destroying user content.

Vault folders are `Products/`, `Agents/`, `Runs/`, `Research/`, `Research/Findings/`, `Research/Evidence/`, `Creative/`, and `Assets/`; `Production/` and `Insights/` are reserved until their domains exist. Root maps of content are `Creative Marketer.md`, `Agents.md`, `Products.md`, `Research.md`, and `Creative.md`.

Every managed artifact note has YAML Properties, an explicit generated region, and a persistent `## My Notes` section. Resync replaces generated content and managed Properties while retaining everything under `My Notes`. Arbitrary user Markdown is not uploaded or parsed. Filenames are type plus the full SHA-256 of stable type/ID identity, so display-name changes cannot break links.

`NEXT_PUBLIC_OBSIDIAN_VAULT_NAME` optionally enables Product Workspace `Open in Obsidian`. The browser computes the same deterministic Product filename and emits `obsidian://open`; only a vault display name and known projected path enter the URI. The URI is navigation, never authorization.

## Privacy

Projection construction uses explicit field allowlists plus a recursive final credential-shaped value/key filter. It excludes secrets, authorization/cookie headers, OAuth tokens, object keys and signed URLs, raw provider responses, provider response IDs, prompts/system instructions, hidden reasoning, raw HTML, and raw web objects. Evidence exports only sanitized structured extracted blocks, never raw HTML. Markdown escapes HTML/control syntax and protects generated markers. Forced RLS and explicit tenant predicates provide defense in depth; a tenant cannot project another tenant even with exact UUID knowledge.

## Known limitations

The local bridge is a manual CLI, not a daemon or packaged Obsidian plugin. Cursor records are retained without compaction in this phase. Full rebuild reconciles canonical state but does not recreate historical revision numbers. Vault conflicts across two simultaneously running bridge processes are not coordinated. Deep links currently appear only for Products. These constraints do not weaken one-way authority.
