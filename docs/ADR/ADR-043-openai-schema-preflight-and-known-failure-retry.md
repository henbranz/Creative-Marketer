# ADR-043 — OpenAI Schema Preflight and Known-Failure Retry

## Status

Accepted

## Context

OpenAI strict Structured Outputs accepts a documented subset of JSON Schema. A Creative Strategist
schema used `oneOf`, which is outside the documented supported subset. A governed run reached the
provider boundary and was rejected with HTTP 400, but the original provider error metadata was not
retained: `oneOf` was a demonstrated compatibility defect, not a proven cause of that HTTP 400.
A subsequent `anyOf` request was also rejected. That rejection produced `FAILED_NO_RESPONSE`
evidence with no response, usage, actual cost, or unknown cost. Existing recovery supported only expired `RUNNING`
runs, so the immutable failed run could not receive an exact lineage successor.

## Decision

The OpenAI provider adapter owns deterministic compatibility validation for the exact structured-
output schema in a model invocation. AgentRuntime invokes this provider preflight after resolving
the capability invocation and before committing `PROVIDER_STARTED`. Unsupported composition
keywords, invalid roots, non-closed objects, optional declared properties, and unsupported formats
fail with the content-free `MODEL_PROVIDER_SCHEMA_UNSUPPORTED` code. The adapter also validates on
direct entry. Unsupported schemas are rejected before network I/O, except for the explicit,
provider-evidenced representation normalization below. Canonical contracts are never rewritten.

The 2026-09-23 count-only delta investigation established two independent A/B/A results:

- Full Creative schema with three string `const`-only nodes: rejected. Replace only those nodes
  with equivalent singleton `enum` nodes: accepted. Restore `const`: rejected again.
- Producer after that normalization: rejected. Remove only `uniqueItems`: accepted. Restore
  `uniqueItems`: rejected again.

Both failures returned HTTP 400 / `invalid_request_error` / `invalid_json_schema` /
`text.format.schema` / `BadRequestError` from `/v1/responses/input_tokens`. This is direct evidence
for these exact representations, not a blanket assertion that every possible typed `const` or
every documented nested union/reference shape is unsupported.

The provider boundary therefore deep-copies canonical schemas, replaces `const` with singleton
`enum`, omits `uniqueItems` from provider generation constraints, and then validates the normalized
result. A node containing both `const` and `enum` fails closed rather than overwriting one constraint.
Traversal visits schema positions only, never keyword-shaped literal values in enum/default data.
The raw provider-schema validator rejects unnormalized `const`/`uniqueItems`, preventing recurrence
through alternate entry points. Both the runtime preflight and direct Responses adapter entry use
the same normalization; the offline request gate examines the actual provider-facing representation.

Singleton enum is semantically equivalent to const. Omitting uniqueness makes only the provider's
generation constraint weaker: AgentRuntime still validates received output against the unchanged
canonical schema before capability persistence, and domain validators remain unchanged. Duplicate
values therefore cannot become accepted application output. No `$schema`, `$id`, `$defs`, `$ref`,
`anyOf`, format, nullability, or other constraints are removed.

All six current agent schemas subsequently passed the count endpoint using synthetic context.
This is not a generation-success guarantee or authorization to retry a live run. Safe request IDs,
the 16-request accounting, variants, and counts are recorded in
`docs/PHASE9_CREATIVE_400_INVESTIGATION.md`. No generation request was sent.

Canonical provider schemas use nested `anyOf` for disjoint alternatives. Application validation
continues to enforce full semantic constraints after a response. Open-ended Intelligence scope is
represented provider-side as a bounded list of required key/value dimension objects and normalized
back to the existing domain mapping.

The trusted CLI-only AgentRun `rerun` boundary accepts a second, disjoint eligibility class:
terminal `FAILED` runs with exactly one matching `FAILED_NO_RESPONSE` attempt, an allowlisted
known-no-response failure code, no provider response, zero usage/cost/uncertainty, no reconciliation,
and no successor. It revalidates tenant, immutable AgentVersion/configuration, exact historical
route/pricing, and fresh budget. It leaves the predecessor untouched, creates one lineage-linked
pending successor, emits the normal request event, and records
`agent.run.known_failure_retry_requested`. Database trigger policy independently enforces the same
zero-usage predecessor boundary.

## Consequences

Detected schema incompatibility becomes a free deterministic failure before provider authority.
Passing the local validator does not establish server acceptance; it is a partial deterministic
check, not an implementation of the complete OpenAI server validator. Known
zero-usage request defects can be retried without resetting live-session state or misusing unknown-
cost reconciliation. Outcome-unknown and response-recorded runs retain the conservative stranded
recovery path, and terminal runs with any response, usage, cost, uncertainty, reconciliation, or
existing successor remain ineligible.
