# ADR-003 — Tool Gateway for External Actions

## Status
Accepted

## Decision
All external side effects and privileged connector operations pass through a Tool Gateway logical enforcement protocol. It is not required to be one deployable service.

The protocol separates these conceptual responsibilities:

1. policy decision
2. approval validation
3. invocation orchestration
4. idempotency
5. connector execution
6. credential resolution
7. audit
8. event recording

Ordinary internal domain/application reads and writes do not pass through the Tool Gateway. They remain subject to normal application authorization and auditing requirements.

## Alternatives

- allow each agent or integration to call providers directly
- force every internal operation through one gateway service
- place authorization only in prompts or connector code

## Rationale
Creates one enforceable contract for consequential capabilities while allowing policy, secrets, and connector execution to be isolated or scaled independently. Keeping internal domain operations outside the gateway avoids a universal bottleneck and unclear ownership.

## Consequences
Agents cannot use provider SDKs directly. Tool interfaces and canonical inputs become first-class versioned contracts. Every deployment topology must preserve the complete enforcement sequence and fail closed when a required component is unavailable.

## Phase 0 implementation clarification

The gateway uses the enforcement order documented in `docs/04_TOOL_PERMISSION_MATRIX.md`. It binds
execution to immutable governance IDs/digests and an exact-version runtime executor mapping. The
final authorization snapshot and idempotency attempt are durably committed before external I/O;
terminal ToolCall, idempotency, Audit, and Outbox evidence are committed atomically afterward.
Unknown post-start outcomes fail closed and cannot be automatically retried.
