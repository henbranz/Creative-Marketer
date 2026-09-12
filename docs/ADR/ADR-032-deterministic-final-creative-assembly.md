# ADR-032 — Deterministic final creative assembly

Status: Accepted

## Context

Approved production inputs must become a technically valid, reviewable social video without giving
a model new creative authority or allowing opaque edits. Rendering handles untrusted binary media,
human text, private storage, retries, and long-running work. Publishing is not yet implemented.

## Decision

Final assembly is a separate deterministic bounded context, not an Agent. Producer output remains
the creative authority. An immutable AssemblyPlan binds exact ProductionPlan, CreativeConcept,
source Asset identities/digests, timeline, captions, overlays, audio policy, and versioned render
profile. FFmpeg/ffprobe are infrastructure behind a typed renderer port and run only in an isolated
worker image with packaged Noto fonts, argv execution without a shell, private temporary paths, no
network inputs, bounded I/O, and safe failure translation.

Assembly work is a durable Temporal workflow carrying identifiers only. The worker reloads and
revalidates canonical authority, tenant, source state, rights, and digest before rendering. Output
is validated and imported as a private immutable catalog Asset with role `final_creative` and
multi-parent lineage. FinalCreative binds the complete immutable provenance. Decisions are
append-only; `APPROVED_FOR_PUBLISHING` is a handoff to a future publishing domain and has no
external side effect. Obsidian is projection-only.

## Consequences

The system gains replay-safe, auditable rendering and replace-by-new-version semantics at the cost
of an additional schema, worker image, FFmpeg operational surface, and storage for historical
renders. Renderer/profile upgrades deliberately create new semantic identities. A future publishing
context must independently authorize destinations, credentials, schedules, and external effects.
