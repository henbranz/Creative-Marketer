# ADR-050: OpenAI Contract Compiler and Three-Gate Validation

## Status

Accepted — 2026-09-26. This ADR clarifies and supersedes the provider-schema portion of ADR-043.
ADR-043 remains authoritative for response lifecycle and known-failure recovery.

## Context

The canonical Production Plan v2 schema expresses six conditional constraints as a strict base
object intersected with partial `anyOf` branches. The local validator previously treated only nodes
declaring `type: object` as objects, so branches containing `properties` without an explicit object
type escaped validation. OpenAI strict Structured Outputs rejected that representation after the
request crossed the provider boundary.

Canonical JSON Schema and a provider's accepted schema subset are separate contracts. Allowing each
runtime, diagnostic, and test path to normalize independently creates drift and can turn a static
defect into a billed failed run.

## Decision

One code-owned compiler, revision `openai-strict-2026-09-26.1`, produces the provider schema,
provider-schema digest, canonical contract key/version, and compiler revision. It is used by API
admission, worker defense, offline tests, frozen-run preflight, and the provider count gate.

The validator rejects every node using `properties`, `required`, or `additionalProperties` unless
it explicitly declares `type: object`. Strict objects require a properties mapping,
`additionalProperties: false`, unique required entries, and every property required. Existing
unsupported-keyword, format, root, depth, and property-count checks remain fail closed and
content-free.

For `production.production_plan` v2 only, the compiler applies a narrow provider-only equivalence
transform. It distributes the complete base `shot` object over four alternatives and the complete
base `segment` object over two alternatives. It verifies the exact expected fields, discriminator
order, and branch count before transforming. The canonical schema and domain validator remain
unchanged. `const` becomes a singleton `enum`; provider-side `uniqueItems` is omitted while canonical
post-response validation continues to enforce uniqueness.

Compatibility is established through three distinct gates:

1. local static compilation and structural validation for every registry-enumerated contract;
2. admission-time compilation of the exact resolved contract before budget reservation, AgentRun,
   outbox, or worker state exists, with worker validation retained as defense in depth;
3. an explicit operator-only provider gate using `POST /v1/responses/input_tokens` with synthetic
   content for all eight installed contract versions across the six model-backed agents. It never
   calls `/v1/responses` and never generates output.

Provider rejection at gate 2 uses `AGENT_PROVIDER_CONTRACT_UNSUPPORTED`, not an HTTP provider failure
code. Only identifiers, version, provider, and mismatch category enter the denial audit.

Compiler revision and digest are returned by all preflight tools. Persisting them on AgentRun or
ModelAttempt requires a migration and coordinated downgrade design, so it is a deliberate follow-up;
no partial persistence is introduced here.

## Consequences

- Static incompatibility cannot consume budget or create a pending/running lifecycle.
- Producer v2 preserves its canonical conditional semantics and historical identity.
- The provider count gate needs a credential and explicit approval but makes no generation request.
- A compiler-revision persistence ADR and migration are required before those fields become durable
  forensic provenance.
