# ADR-047 — Versioned Creative output capacity and terminal-run continuation

## Status

Accepted

## Context

A controlled five-concept Creative Strategist invocation returned an authoritative OpenAI Response
with status `incomplete`, reason `MAX_OUTPUT_TOKENS`, and exactly 8,000 output tokens. ADR-046
correctly persisted that attempt as `FAILED_RESPONSE`, settled its known usage to actual cost, and
made it ineligible for ordinary recovery. The response proves that the historical 8K output limit,
not schema compatibility or transport ambiguity, is insufficient for this high-reasoning workload.

## Decision

The immutable `creative_balanced` route
`openai-gpt-5.6-sol-creative-2026-09-13` remains installed with its 8,000 output-token semantics.
New Creative AgentVersions use `creative_balanced_v2` and route
`openai-gpt-5.6-sol-creative-16k-2026-09-25`: OpenAI `gpt-5.6-sol`, high reasoning, unchanged
capabilities, 16,000 output tokens, and the same frozen USD 4/M input and USD 20/M output pricing.

The active immutable configuration allows one model call, zero tool calls, 40,000 total tokens,
USD 0.416 per run, and 20 runs/USD 8.32 daily. The conservative input allowance is 24,000 tokens.
The observed 21,324-token input bound therefore fits without truncation. Worst-case reservation is
exactly `(24,000 × 4 + 16,000 × 20) / 1,000,000 = USD 0.416`; the all-input alternative is only
USD 0.160.

An output-limited `FAILED_RESPONSE` stays terminal and never receives a recovery successor. A
dedicated operator transition may request a fresh normal Creative run only when the API verifies one
authoritative attempt with incomplete/MAX_OUTPUT_TOKENS metadata, known usage, zero unknown cost,
and retained response identity; a different active AgentVersion must resolve to a larger compatible
provider/model route. The transition reconstructs only request semantics from immutable provenance,
then reuses normal admission so current Product/Research snapshots and current budgets are bound.
A deterministic transition idempotency key prevents duplicate replacements. The live checkpoint
retains the preceding Creative identifier and does not change until returned tenant, Product, stage,
AgentVersion, profile, and non-recovery provenance are verified.

## Consequences

Historical runs remain reconstructable and all prior actual/unknown cost evidence remains unchanged.
The new reservation is independent. Operators can continue a bound live session without rerunning
Researcher, editing checkpoint JSON, or treating a paid terminal response as recoverable. An
unrelated active run, wrong response reason, unknown attempt, nonterminal run, same AgentVersion, or
tenant/Product mismatch fails closed. No provider call is performed by the transition command; the
worker remains the only execution boundary.
