# ADR-046 — Authoritative Returned Provider Responses

## Status

Accepted

## Context

AgentRuntime previously treated any provider exception after `PROVIDER_STARTED` and before a
`ModelInvocationResult` as transport-ambiguous. The OpenAI adapter raised on Responses with
`incomplete`, `failed`, or `cancelled` status before copying response ID, status, usage, or a bounded
reason into a domain value. Completed Responses containing a refusal or invalid JSON had the same
metadata-loss boundary. The runtime therefore persisted `UNKNOWN` and the full reserved cost even
though an authoritative provider Response object had been returned.

That classification conflated three different facts: no authoritative response, an authoritative
non-completed response, and a completed response whose application output is unusable.

## Decision

Provider adapters may attach an immutable, provider-neutral `ReturnedProviderResponse` to a bounded
failure. It contains only provider/model, optional opaque response ID, finite response status,
finite allowlisted reason, and optional validated usage. SDK objects, arbitrary provider messages,
raw bodies, prompts, headers, credentials, and Product/Research content never cross the adapter.
Unknown provider reason values map to `OTHER`.

AgentRuntime checkpoints this metadata as `RESPONSE_RECORDED` before terminal processing and never
retries that provider call. Terminal returned-response failures use `FAILED_RESPONSE`; only calls
without an authoritative response may become `UNKNOWN`. Completed responses rejected by application
validation continue to preserve completed-response evidence independently from the failed AgentRun.

When usage is available, cost is calculated only through the frozen route pricing and the run
reservation is settled to actual cost. When usage is absent, zero tokens are storage placeholders,
`usage_available=false` explicitly prevents zero-cost certainty, and the full run reservation moves
to the unknown-cost ledger. That cost uncertainty is operator-reconcilable, but it is not provider-
outcome ambiguity and does not make the run eligible for autonomous retry or recovery succession.

Database constraints and the immutable lifecycle trigger enforce these distinctions. Existing rows
are not reclassified: historical attempts require separate authoritative evidence and operator
action. Migration `20260925_0033` enriches only historical `RESPONSE_RECORDED` and `SUCCEEDED` rows
with `completed` and `usage_available=true`, because the pre-0033 runtime could create those states
only after a successful `ModelInvocationResult`. A strict temporary trigger allows exactly that
three-column enrichment while comparing every pre-existing row field for equality; the final guard
is installed before the migration commits. Historical `UNKNOWN`, `FAILED_NO_RESPONSE`,
`PROVIDER_STARTED`, and `CLAIMED` rows retain null response metadata.

## Consequences

Operators can distinguish output-budget exhaustion, safety filtering, provider failure/cancellation,
invalid returned output, and transport ambiguity without retaining sensitive provider payloads.
Returned failures cannot trigger the bounded transport retry or create a recovery successor. Missing
usage remains conservatively funded until reconciliation. The new attempt state and response metadata
require migration `20260925_0033`; downgrade fails closed if a `FAILED_RESPONSE` row exists.

This decision does not change provider routes, reasoning effort, token caps, schema normalization,
Creative claim authority, or any live historical run.
