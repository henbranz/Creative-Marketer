# ADR-020 — Approval Binding and Idempotent Operation Lifecycle

## Status

Accepted

## Context

Permission, approval, and duplicate-execution control can diverge if they independently describe
an operation. External timeouts also cannot reliably prove whether a side effect occurred.

## Decision

Approval and Idempotency consume the same immutable, versioned `ActionBindingV1`. Its SHA-256
digest identifies one exact governed logical operation, including tenant, Agent/Tool/permission
versions and configuration digests, scope, resource, environment, normalized input, risk, and
platform-generated idempotency key.

Approval request, human decision, and revocation are separate append-only records. Authority is
derived at decision time from a fresh `ExecutionContext`; R6 is owner-only. Expiry is derived from
trusted server time and risk. Validation fails closed on current permission denial or any binding,
expiry, denial, or revocation mismatch.

Idempotency reserves one row per `(tenant_id, tool_definition_id, idempotency_key)`. An execution
attempt owns the operation through an explicit attempt UUID and lease. Only a verified
pre-effect failure or reconciliation confirming no effect permits another attempt. Lease expiry
alone does not. Confirmed success is replayed by safe result reference; unknown outcomes require
explicit reconciliation.

All positive mutations and security-relevant state transitions append Audit evidence in the same
transaction. Raw normalized payloads, credentials, and provider output are not persisted in these
records.

## Alternatives Considered

- Separate approval and idempotency hashes: rejected because semantic drift can approve one action
  while reserving another.
- Mutable approval status: rejected because it destroys decision history.
- Automatic retry after lease expiry or timeout: rejected because an external effect may exist.
- Provider-native idempotency only: rejected because provider semantics and coverage vary.

## Consequences

- Future Tool Gateway must reconstruct and compare the exact binding immediately before execution.
- Approval is neither authorization nor a bearer execution credential.
- Idempotency reduces duplicate effects but does not promise distributed exactly-once execution.
- Operational reconciliation is required for ambiguous provider outcomes.
- Dual-control approval, ToolCall, outbox/inbox, provider adapters, and Temporal remain deferred.
