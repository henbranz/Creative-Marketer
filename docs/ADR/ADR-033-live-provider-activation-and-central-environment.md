# ADR-033 — Live provider activation and central environment

Status: Accepted

## Context

The fake-provider path was complete, but live execution lacked a single configuration contract,
account preflight, explicit operator acknowledgement, and a bounded local acceptance budget.
OpenAI now officially documents GPT-6 Astra and the GPT Image 2.5 Sunburst snapshot.

## Decision

- New Producer runs resolve `production_deep` to `gpt-6-astra`, high reasoning, through the
  existing Responses adapter. Researcher and Creative Strategist stay on Terra.
- New image jobs resolve to `gpt-image-2.5-sunburst-2026-09-08`. Seedance model and pricing remain
  unchanged. Every migration creates new versioned route/configuration provenance; old records are
  immutable.
- `<repo>/.env` is the single ignored developer configuration source. `.env.example` is its safe,
  complete contract; it is never domain authority.
- A no-generation preflight checks exact application routes, OpenAI account model access, and the
  configured regional BytePlus origin. BytePlus authentication is deferred when no free endpoint
  exists.
- Live workers require `ALLOW_BILLABLE_MEDIA=true` and the exact `RUN_LIVE_E2E` acknowledgement.
  Media execution rechecks a cumulative per-Product `LIVE_E2E_MAX_USD` ceiling before provider I/O.
- Operator helpers advance existing governed APIs and pause for human decisions. They never bypass
  AgentRuntime, Tool Gateway, Production approval, or Assembly authority.

## Consequences

Local activation is intentionally multi-step and resumable. Account-region configuration is
explicit. Conservative reservations may overstate pending cost, while actual usage replaces them
when sufficiently authoritative. CI stays secret-free and cannot claim live-provider acceptance.
