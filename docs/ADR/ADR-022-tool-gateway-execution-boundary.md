# ADR-022: Tool Gateway execution boundary

- Status: Accepted
- Date: 2026-09-05

## Context

Tool execution combines mutable governance configuration with external side effects. A retry or
version race can duplicate effects, bypass an approval, or execute code against the wrong contract.
Database transactions cannot include arbitrary provider I/O.

## Decision

A `ToolCall` represents one logical operation and owns its immutable `ActionBindingV1`. Runtime
executor bindings are keyed by the exact Tool definition and Tool version IDs; there is no fallback
to another version. The gateway commits the final locked authorization snapshot, idempotency lease,
and `EXECUTING` ToolCall before executor I/O. It classifies only an explicit pre-effect failure as
safe to retry. Any other exception after execution starts is an unknown external outcome. Terminal
ToolCall state, idempotency state, Audit, and Outbox evidence commit atomically. A confirmed external
effect whose output violates the registered output schema remains a succeeded operation but returns
no invalid output and is not retried.

## Consequences

Duplicate requests replay or report in-progress state without repeating an effect. Approval resume
and reconciliation operate on the same logical record. Executor adapters must make their effect
boundary explicit and return only opaque result references. Post-effect persistence failure requires
a separate recovery transaction; if even that cannot persist, the durable `EXECUTING` lease remains
an ambiguous state that blocks automatic retry. This adds state and adapter discipline in exchange
for auditable, fail-closed execution semantics.
