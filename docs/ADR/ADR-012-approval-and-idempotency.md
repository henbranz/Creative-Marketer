# ADR-012 — Approval Canonicalization and Idempotency

## Status
Accepted

## Decision
Approval binds to a versioned canonical representation of the exact normalized action. The binding includes at least tenant, requesting actor/agent/run, tool and version, resource, environment, policy version, normalized input digest, idempotency context, creation time, and expiration.

Approval decisions are immutable history. State transitions and single-use consumption, where required, are atomic. Payload mismatch, expiry, revocation, reuse, tenant mismatch, or material action change fails closed. Authorization, budget, connector state, and approval validity are rechecked immediately before execution.

Idempotency records are scoped at least by tenant, tool/version, and key and store the canonical request digest. The same key with a different digest is rejected. Execution state distinguishes:

- reserved/pending
- executing
- succeeded with a replayable normalized result reference
- failed before an external effect
- unknown external outcome
- reconciled

Unknown outcomes must be reconciled before retrying an operation that may duplicate a side effect.

## Alternatives

- approval of a human-readable description without payload binding
- idempotency key without request digest
- unconditional retry after timeouts
- treat provider-native idempotency as the only record

## Rationale
Canonical binding prevents approval replay and post-approval mutation. Platform idempotency protects against concurrent calls, retries, webhook duplication, and providers with incomplete idempotency support.

## Tradeoffs
Canonicalization must be versioned and stable across languages. Some provider failures cannot reveal whether the effect occurred, requiring reconciliation and user-visible pending states.

## Consequences

- tool schemas normalize input before approval and execution
- atomic uniqueness or execution leases prevent concurrent ownership
- provider-native idempotency keys are used where available but do not replace platform records
- audit records preserve decision and execution state without storing secrets
- approval and idempotency behavior is tested independently of LLM output

## Implementation Clarifications

Canonicalization version 1 uses a deliberately small, portable JSON profile: null, booleans,
integers within JavaScript's interoperable safe range, valid Unicode strings, arrays, and
string-keyed objects. Object keys are sorted by Unicode scalar value and serialized as compact UTF-8
JSON. Floats, out-of-range integers, bytes, dates, invalid Unicode, custom objects, unordered
collections, and credential-shaped content are rejected. The profile is not presented as full RFC
8785; a new version is required for any semantic change.

One immutable `ActionBindingV1` is the source for both `ApprovalRequest.action_digest` and
`IdempotencyRecord.request_digest`. It binds resolved configuration digests and immutable version
IDs for Agent, Tool, and ToolPermission, plus scope, resource, environment, normalized input, risk,
and a platform-generated `op_<uuid>` key.

Approval state is derived from append-only request, decision, and optional revocation rows. Its
precedence is revoked, denied, expired, approved, pending. Expiry is seven days for R0-R4, 24 hours
for R5, and one hour for R6. Active owners/admins decide R0-R5; R6 requires an active owner.

Idempotency uniqueness is `(tenant_id, tool_definition_id, idempotency_key)`. Tool version is
bound in the action digest. Execution attempts have explicit UUID ownership and bounded leases,
but lease expiry never authorizes takeover. Unknown external outcome blocks retry until explicit
reconciliation records either confirmed effect or confirmed no effect.
