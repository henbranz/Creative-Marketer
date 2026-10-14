# ADR-057: Motion-preserving assembly and immutable render revisions

Status: Accepted

## Context

The first live ModelArk video assembly produced a FinalCreative that looked like one still frame per
source video even though each source Asset contained motion. The assembly worker inherited Docker
Compose values that hardcoded both media providers to `fake`. That activated the fake-provider
placeholder renderer, whose deliberate optimization extracts one frame from each synthetic video
and packet-loops it. The normal renderer also relied on implicit input timestamps and concat-demuxer
stream copy, leaving avoidable risk for provider MP4 timestamp variation.

FinalCreative and its output Asset are immutable. Correcting a renderer must therefore not overwrite
the historical output or regenerate paid media.

## Decision

- Docker Compose passes the root media-provider selection into the assembly worker. Fake placeholder
  rendering is active only when both selectors are explicitly `fake`.
- VIDEO normalization uses `setpts=PTS-STARTPTS`, the pinned scale/crop policy, CFR conversion, and
  `yuv420p`. Audio uses `asetpts=PTS-STARTPTS` and the pinned 48 kHz resampling policy.
- Normalized VIDEO clips are ffprobed before concatenation. Codec, pixel format, dimensions, frame
  rates, start time, duration, and decoded frame count must satisfy bounded structural checks.
- Normalized clips are joined with FFmpeg's concat filter and deterministic encoding, not concat
  demuxer stream copy.
- A governed re-render creates a new monotonically numbered AssemblyPlan and AssemblyJob. It binds
  the triggering FinalCreative, reuses the exact source Asset IDs and digests, and emits the existing
  assembly-plan event. It never creates GenerationJobs or permits provider execution.
- One successor is allowed per triggering FinalCreative. A tenant-scoped partial unique index and
  transaction advisory lock make retries and concurrent requests idempotent.
- Renderer versions identify `assembly-motion-v2`; historical FinalCreatives remain unchanged.

## Consequences

Assembly can be retried safely after deterministic renderer defects without paying for or replacing
media. Correctness costs one deterministic concat-stage re-encode. Tests must include moving video
and compare decoded frames inside each VIDEO interval; constant-color fixtures are insufficient.
