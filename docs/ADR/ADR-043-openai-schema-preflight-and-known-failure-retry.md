# ADR-043 — OpenAI Schema Preflight and Known-Failure Retry

## Status

Accepted

## Context

OpenAI strict Structured Outputs accepts a documented subset of JSON Schema. A Creative Strategist
schema used `oneOf`, so an otherwise valid governed run reached the paid provider boundary and was
rejected with HTTP 400. That rejection produced authoritative `FAILED_NO_RESPONSE` evidence with no
response, usage, actual cost, or unknown cost. Existing recovery supported only expired `RUNNING`
runs, so the immutable failed run could not receive an exact lineage successor.

## Decision

The OpenAI provider adapter owns deterministic compatibility validation for the exact structured-
output schema in a model invocation. AgentRuntime invokes this provider preflight after resolving
the capability invocation and before committing `PROVIDER_STARTED`. Unsupported composition
keywords, invalid roots, non-closed objects, optional declared properties, and unsupported formats
fail with the content-free `MODEL_PROVIDER_SCHEMA_UNSUPPORTED` code. The adapter also validates on
direct entry. Invalid schemas are never silently rewritten and never reach the network.

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

Schema incompatibility becomes a free deterministic failure before provider authority. Known
zero-usage request defects can be retried without resetting live-session state or misusing unknown-
cost reconciliation. Outcome-unknown and response-recorded runs retain the conservative stranded
recovery path, and terminal runs with any response, usage, cost, uncertainty, reconciliation, or
existing successor remain ineligible.
