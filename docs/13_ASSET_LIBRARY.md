# Asset Library

## Boundary and ownership

The Asset Library is part of the Catalog bounded context. PostgreSQL is authoritative for Asset
identity, tenant/Brand/Product association, lifecycle, rights, digest, and lineage. An S3-compatible
private object store owns binary bytes. The application layer defines the provider-neutral
`ObjectStore` port; boto3 is confined to `infrastructure/object_storage`.

The bootstrap applies bucket CORS where the provider supports the S3 API. Community MinIO exposes
CORS only as a server-wide setting, so local/CI set `MINIO_API_CORS_ALLOW_ORIGIN` to the same
explicit browser origin; deployments using MinIO must do likewise. The bootstrap accepts only the
provider's specific not-implemented response and still removes public bucket policy.

User uploads are authenticated first-party Catalog operations, not Agent Tool executions. Agents
may later consume READY manifest references subject to deterministic allowed-use policy, but they
cannot create grants, choose storage keys, weaken rights, or make Assets READY.

## Two-phase upload

1. OWNER/ADMIN creates PENDING metadata and receives a 10–15 minute presigned POST for one random
   staging key. The policy fixes MIME and kind-specific size (25 MiB image/PDF, 250 MiB video).
2. The browser uploads directly to private storage and asks the API to finalize.
3. Finalize atomically claims validation, streams bytes without loading the object in memory,
   checks head/stream size, recognizes allow-listed magic bytes, and computes SHA-256.
4. Valid bytes are promoted to a different random server-only key and metadata/audit/Outbox commit
   together as READY. Deterministic validation failure commits REJECTED evidence. Provider outage
   resets to PENDING so finalize can be retried.

Repeated finalize of READY or REJECTED returns the same state. A concurrent finalize receives a
conflict. Object promotion precedes the PostgreSQL READY transaction; a crash can leave a private
unreferenced final object, never a false READY row. A future bounded janitor may remove unreferenced
staging/orphan objects after a retention delay.

## Formats and metadata

Allowed formats are JPEG, PNG, WebP, MP4, WebM, and PDF. Filename and client MIME are hints only.
SVG and other active browser documents are rejected. PNG and extended WebP dimensions are captured
when available; other optional dimensions/duration remain null until a sandboxed media-probe
adapter is justified. ETag is never treated as content identity.

Rights are `confirmed`, `unknown`, or `restricted`. Non-confirmed media is restricted to
`internal_analysis`; confirmed media can explicitly allow analysis, generation input, organic
publishing, and/or paid advertising. The UI requires a clear attestation for its initial analysis
and generation flow.

## API and facts

Protected APIs create/list/detail/finalize/download/archive Assets. Responses never expose storage
keys. Signed downloads last 5–15 minutes. The bounded facts are `catalog.asset.ready.v1` and
`catalog.asset.archived.v1`; they contain IDs, safe classification, size, and digest, never names,
keys, grants, rights evidence text, or binary content. Audit uses the same Catalog transaction.

ProductKnowledgeSnapshot V2 includes at most 100 READY Product Assets in deterministic ID order.
Each entry contains Asset ID, kind, role, verified MIME, size, digest, rights/uses, and parent ID.
V1 remains readable and constructible; new workspace snapshots emit the V2 event contract.
