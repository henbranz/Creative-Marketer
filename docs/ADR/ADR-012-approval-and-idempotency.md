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
