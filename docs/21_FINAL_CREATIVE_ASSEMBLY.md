# Final Creative Assembly

Final assembly is a deterministic bounded context between governed media production and a future
publishing context. It does not call a model and it does not reinterpret the approved creative or
ProductionPlan. The Producer remains the creative authority; assembly converts its exact scene,
shot, text, timing, and source bindings into one reviewable video.

## Canonical model

`AssemblyPlan` is an immutable, digest-bound timeline over an exact ProductionPlan,
CreativeConcept, source Assets, captions, overlays, audio policy, and versioned render profile.
Generated sources come only from successful GenerationJobs. Manual-capture shots require an
explicit READY video Asset binding. Existing, generated, and manual Assets must have confirmed
rights and `generation_input` use, and their digests are bound into the plan.

An `AssemblyJob` is the only mutable lifecycle record (`READY → RENDERING → IMPORTING → SUCCEEDED`
or `FAILED`). Temporal carries tenant/job locators only. The separate assembly worker reloads all
authority, rights, state, and bytes, verifies each digest, invokes FFmpeg using a typed argv without
a shell or network input, validates the output with ffprobe, and imports it as a private ordinary
Asset with role `final_creative`. Source-to-output lineage uses `ASSEMBLED_FROM` edges.

`FinalCreative` is immutable and binds Product, CreativeConcept, ProductionPlan, AssemblyPlan,
output Asset, renderer/version, profile/version, media facts, and semantic digests. A retry returns
the existing authoritative mapping. Reassembly creates a new plan, job, Asset, and FinalCreative;
history is retained. `FinalCreativeDecision` is append-only. `APPROVED_FOR_PUBLISHING` means only
that this exact render is accepted as a candidate for a future publisher—it never publishes.

## Render profile

`social_vertical_v1@1` produces MP4/H.264/yuv420p at 1080×1920 and 24 fps. Audio, when present, is
AAC stereo at 48 kHz and normalized to the profile target; silent output is valid. Typed ASS cues
use packaged Noto fonts for scripted captions, CTA, and required disclaimer overlays. The renderer
never receives an arbitrary filter graph or command string. Temporary workspaces are private,
bounded, and removed on success, error, and timeout.

## Security and privacy

Assembly tables use forced tenant RLS and composite tenant foreign keys. The runtime cannot mutate
immutable plan/final/decision records. Before rendering, the worker revalidates tenant ownership,
membership and tenant state, source lifecycle, rights, allowed use, size, and digest. It streams
sources into a private temporary directory and never exposes object keys, signed URLs, local paths,
commands, or raw FFmpeg stderr through HTTP, audit, events, logs, or Obsidian.

Knowledge projection adds AssemblyPlan, AssemblyJob, FinalCreative, and FinalCreativeDecision nodes.
Obsidian displays stable notes and provenance links only; it cannot bind sources, create plans, run
jobs, or decide a final.

## Operations

The assembly worker image pins its FFmpeg package layer and Noto font package independently from
the API/web images. Start Temporal, then `make assembly-worker`. A missing renderer fails startup.
Safe browser failure codes include `ASSEMBLY_NOT_READY`, `ASSEMBLY_SOURCE_RIGHTS_CHANGED`,
`ASSEMBLY_SOURCE_DIGEST_MISMATCH`, `ASSEMBLY_RENDER_FAILED`, `ASSEMBLY_RENDER_TIMEOUT`, and
`ASSEMBLY_OUTPUT_INVALID`.
