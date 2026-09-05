# 12 — Product Brain and Catalog Workspace

## Purpose

The Catalog bounded context owns the trusted commercial context that users provide before any
agent work begins:

```text
Tenant
  ↓
Brand + BrandProfile
  ↓
Product + ProductProfile + ProductBrief
  ↓
ProductKnowledgeSnapshot
  ↓
future Research and Agent context
```

Product Brain is the product-facing name for this structured context. `catalog` is the durable
domain and persistence boundary because it will also own product assets and catalog metadata.

## Current state and snapshots

Brand, Product, profiles, and Brief are editable current state. A Product Brief uses optimistic
revision checks but is not versioned on each form edit. An explicit snapshot operation captures a
complete immutable, schema-versioned representation at a meaningful boundary.

Snapshot canonicalization uses the repository's strict canonical JSON and SHA-256 convention.
The digest covers schema version, Brief source revision, Brand, BrandProfile, Product,
ProductProfile, ProductBrief, provenance, claims, and constraints. It excludes snapshot identity,
creator, and timestamps. Equivalent semantic state therefore yields the same digest; changed
semantic state yields another digest. Database triggers reject snapshot update and deletion.

Future Agent Runs receive a snapshot ID/digest rather than reading arbitrary ORM state. Research
and AI-inferred candidate knowledge must be stored separately and may not silently overwrite
user-provided facts.

## Knowledge and provenance

Important concepts are typed domain fields. Bounded arrays and nested Audience value objects use
JSONB only after domain and API validation. Audience contains a name, description, pains, desires,
motivations, and objections. Claims are separated into allowed claims, prohibited claims, and
required disclaimers. Money uses PostgreSQL `NUMERIC` and Python `Decimal`; API values serialize
as decimal strings.

Knowledge declares one of `USER_PROVIDED`, `IMPORTED`, `AI_INFERRED`, or `VALIDATED`. TASK-001
creates only `USER_PROVIDED` state. The enum establishes the future enrichment boundary without
the operational burden of field-level provenance for every scalar.

## Completeness

Completeness is deterministic and has five equally weighted sections:

- Product basics
- Audience
- Positioning
- Benefits and features
- Creative and claims

Each section contains two required checks. Every satisfied check contributes ten points. The
result includes the 0–100 score plus stable missing-section and missing-field identifiers. It is
guidance for readiness, not authorization and not an AI judgment.

## Authority, tenancy, and privacy

Catalog APIs resolve `ExecutionContext` through the authenticated principal and an untrusted
tenant selector. The database tenant setting is transaction-local. Active OWNER and ADMIN members
may mutate; MEMBER is read-only. Browser role headers, body tenant IDs, and route resource IDs do
not confer authority.

All six Catalog tables use forced RLS. Tenant-owned relationships use composite tenant foreign
keys. The runtime role has no delete permission and snapshots permit only select/insert. Normal
runtime lifecycle uses archive states.

Descriptions and Brief text are tenant-confidential. Audit records store resource IDs,
changed-field categories, revisions, completeness, and semantic digests. Catalog event payloads
contain only stable IDs, status, version, score, and digest references. Neither channel contains
Brand/Product text. Application telemetry uses route templates and bounded outcomes without
tenant, Brand, Product, or content metric dimensions.

## Atomic facts

The Catalog unit of work commits aggregate mutation, compact Audit evidence, and a canonical
Outbox event in one PostgreSQL transaction. Registered facts are:

- `catalog.brand.created.v1`
- `catalog.product.created.v1`
- `catalog.product.updated.v1`
- `catalog.product.brief_completed.v1`
- `catalog.product.snapshot_created.v1`

Brief completion is emitted only when required context crosses from incomplete to 100 percent.
Ordinary edits are audited but do not create per-keystroke events.

## API and UI

The protected API provides Brand lists/detail/mutations, Brand-scoped Product creation/listing,
Product workspace detail/update, Brief read/save/completeness, and snapshot create/latest. Backend
OpenAPI is generated into `packages/contracts/openapi.json`; TypeScript types are generated from
that artifact and checked for drift.

The Next.js Products workspace provides the application shell, real Brand/Product navigation,
Overview, structured Brief sections, save/error/read-only states, completeness guidance, and
intentional empty states for future workspace tabs. Production authentication remains dependent
on the deferred identity-provider adapter; local development uses the explicit Phase-0 adapter.

## Deferred

- binary asset storage and uploads
- Product research and evidence
- AI enrichment and agent execution
- production identity provider and tenant discovery UI
- full concurrent editing UX beyond Brief revision conflict
