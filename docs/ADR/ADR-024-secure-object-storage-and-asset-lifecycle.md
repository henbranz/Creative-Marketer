# ADR-024: Secure Object Storage and Asset Lifecycle

## Status

Accepted

## Context

Product media is tenant-confidential, too large for PostgreSQL, and must become deterministic input
for future agents without trusting browser declarations or granting agents storage authority.
S3-compatible storage is required without coupling Catalog policy to one vendor.

## Decision

Catalog owns an immutable-identity Asset aggregate in PostgreSQL; a private S3-compatible service
owns bytes behind an inward-owned `ObjectStore` port. Local/CI use pinned MinIO. Upload is two-phase:
a narrow presigned POST targets a random staging key, then deterministic server validation streams
and hashes bytes before promotion to a distinct server-only final key. READY identity is immutable
in both domain behavior and a database trigger. Access uses short-lived signed GET grants.

Asset metadata, audit, and Outbox facts commit atomically. Object-store/database atomicity is not
claimed: promotion can leave a private orphan if the database commit fails, handled by retry and a
future retention janitor. Storage is not part of general API readiness. Rights and explicit allowed
uses are durable Asset policy; only READY Product Assets appear in bounded Snapshot V2 manifests.

## Consequences

Presigned upload traffic bypasses the API, reducing memory and bandwidth pressure. A still-valid
upload grant cannot overwrite READY bytes because staging and final keys differ. Provider SDK and
credentials remain isolated in infrastructure/configuration. Finalize requires an additional read
and server-side copy, increasing storage I/O, and optional video/JPEG metadata stays incomplete
until sandboxed media probing is introduced. Lifecycle cleanup and malware scanning remain explicit
follow-on work before accepting higher-risk office/archive formats.
