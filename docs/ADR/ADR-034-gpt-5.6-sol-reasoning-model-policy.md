# ADR-034 — GPT-5.6 Sol reasoning-model policy

Status: Accepted

## Context

Research, creative scriptwriting, and production planning need a coherent current reasoning-model
policy while retaining different reasoning effort, input, output, and budget bounds. Terra and
Astra routes were previously activated and may exist in immutable AgentVersions, AgentRuns,
ModelAttempts, projections, and operator evidence. Media generation and deterministic final assembly
are separate capabilities and must not be coupled to a reasoning-model migration.

## Decision

- New Researcher runs resolve `research_balanced` to OpenAI `gpt-5.6-sol`, medium reasoning, using
  route `openai-gpt-5.6-sol-research-2026-09-13`.
- New Creative Strategist runs resolve `creative_balanced` to the same model, high reasoning, using
  route `openai-gpt-5.6-sol-creative-2026-09-13`.
- New Producer runs resolve `production_deep` to the same model, high reasoning, using route
  `openai-gpt-5.6-sol-production-2026-09-13`. Producer retains at most ten authorized image inputs
  and declares zero tools.
- All three routes freeze pricing snapshot `openai-gpt-5.6-sol-2026-09-13`: USD 4/M input, USD
  0.40/M eligible cached input, and USD 20/M output. Reservations price all input as uncached for a
  safe upper bound. With their independent bounds, worst-case run/daily ceilings are Researcher USD
  0.16/3.20, Creative Strategist USD 0.256/5.12, and Producer USD 0.32/6.40.
  These configured input bounds remain below the provider's long-context pricing threshold, so the
  standard rates apply to every permitted run.
- Existing logical profile keys remain stable so stored policy references do not become orphaned.
  Fresh bootstrap creates a new immutable AgentVersion/configuration digest and activates it;
  historical Terra/Astra versions and runs are never rewritten.
- Image generation remains `gpt-image-2.5-sunburst-2026-09-08`, video remains
  `dreamina-seedance-2-5-260628`, and final assembly remains deterministic FFmpeg execution.
- No fallback model is allowed. Provider access remains infrastructure-only, and every reasoning
  invocation continues to use strict structured output, bounded context, no model tools, and the
  existing durable provenance and recovery boundary.

## Consequences

Current reasoning behavior and pricing are easier to operate and explain, while per-capability
reasoning effort and budgets remain explicit. Route, pricing, prompt, bootstrap, UI, preflight, and
tests must evolve together. Historical UI and Obsidian projections display the model frozen on each
run, so old Terra/Astra records remain accurate. This ADR supersedes only the current reasoning-route
parts of ADR-026 and ADR-033; their other boundaries remain in force.
