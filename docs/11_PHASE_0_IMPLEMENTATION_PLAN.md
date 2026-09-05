# 11 — Phase 0 Implementation Plan

## Goal

Create a foundation strong enough that future agents/integrations can be added without architectural rewrites.

## Non-Goal

No Researcher, Creative Strategist, Producer, Marketer, Commerce Agent, or Intelligence Agent business implementation yet.

## Workstream 1 — Repository Foundation

- decide monorepo layout
- Python package management
- TypeScript package management
- formatting/linting
- test runners
- pre-commit hooks
- CI
- `.env.example`
- configuration loading
- development Docker compose if useful

Status: completed by TASK-001.

## Workstream 1.5 — Architecture Consolidation

- clarify dependency direction
- distinguish commands, synchronous application calls, and domain events
- define Tool Gateway as a logical enforcement protocol
- document Phase 0 security invariants
- establish contract ownership
- update required ADRs before persistence and governance implementation

Status: completed by TASK-001.5.

## Workstream 2 — Database Foundation

- PostgreSQL
- migration system
- UUID strategy
- timestamps
- soft-delete policy only where justified
- tenant ownership conventions
- transaction boundaries
- RLS strategy
- test database setup

## Workstream 3 — Identity & Tenancy

Implement:
- Tenant
- User
- Membership
- Role/policy baseline

Acceptance:
- tenant A cannot access tenant B
- background jobs carry tenant context explicitly
- no implicit “current tenant” global state

## Workstream 4 — Agent Registry

Implement schemas/services for:
- AgentDefinition
- AgentVersion
- model policy
- prompt version
- allowed/denied tools
- data scopes
- budgets

No LLM calls required.

## Workstream 5 — Tool Registry & Permission Engine

Implement:
- ToolDefinition
- risk level
- input/output schema references
- side-effect classification
- ToolPermission
- policy evaluation result with machine-readable denial reasons

Start deny-by-default.

## Workstream 6 — Approval Engine

Implement:
- ApprovalRequest
- payload hash binding
- expiration
- grant/deny
- actor identity
- immutable decision history

## Workstream 7 — Audit

Status: foundation completed by `TASK-004`; later governance/tool integrations remain pending.

Record:
- actor
- tenant
- action
- resource
- request/correlation
- timestamp
- state hashes
- safe metadata

Audit must avoid raw secrets.

## Workstream 8 — Event Contracts

Implement:
- event envelope
- schema versioning
- correlation/causation
- outbox pattern consideration
- idempotent consumer helper
- test events

Do not prematurely adopt Kafka. Start with the simplest durable event mechanism compatible with scale path.

## Workstream 9 — Tool Gateway Skeleton

A fake/local tool is enough to prove the boundary.

Request flow:
1. validate identity
2. load tool
3. authorize
4. check approval
5. validate input
6. check idempotency
7. execute adapter
8. audit
9. normalize result
10. emit events if required

## Workstream 10 — Observability

- structured logs
- correlation IDs
- trace IDs
- metrics baseline
- error taxonomy
- agent/tool cost fields even before real agents exist

## Workstream 10.5 — Temporal Adoption Spike

Keep ADR-004 proposed until a bounded spike demonstrates:

- pause/resume for human approval
- long-running media generation polling
- durable scheduled publishing
- crash/restart recovery
- retry and terminal failure behavior

Do not use Temporal for simple synchronous operations.

## Workstream 11 — Architectural Tests

Mandatory:
- tenant isolation
- denied tool
- approval required
- approval payload mismatch
- duplicate idempotency key
- malformed tool input
- secret-redaction test
- duplicate event consumer test
- audit emitted on denied/allowed action

## Suggested Task Order

1. Repository Foundation — completed by `TASK-001`
2. Architecture Consolidation — completed by `TASK-001.5`
3. Database + Multi-Tenancy
4. Actor/Auth Identity Boundary
5. Audit Foundation — completed by `TASK-004`
6. Agent Registry
7. Tool Registry
8. Permission Engine
9. Approval + Idempotency
10. Transactional Outbox/Inbox + Events
11. Tool Gateway
12. Observability
13. Temporal Adoption Spike
14. Architecture/Security Test Suite
15. Phase 0 Review

This sequence supersedes the earlier numeric task ordering. Detailed implementation task identifiers should be assigned without changing these dependencies. No business agent is implemented during Phase 0.

## Exit Criteria

Phase 0 is complete when a fake agent can request a fake side-effecting tool and the platform can:

- authenticate its tenant/identity
- determine permission
- require approval by policy
- validate input
- execute only through gateway
- execute idempotently
- record a complete audit trail
- emit a typed event
- trace the operation end-to-end

No external social/commerce provider is required to pass Phase 0.
