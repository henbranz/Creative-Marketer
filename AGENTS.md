# AGENTS.md — Creative Marketer Project Constitution

## Mission

Creative Marketer is a production-grade, multi-tenant AI operating system for founders and teams that sell products.

The platform closes the loop:

**Research → Creative Strategy → Content Production → Publishing → Commerce → Measurement → Intelligence → Continuous Improvement**

This repository is not a throwaway prototype. Architectural decisions must favor security, clear boundaries, testability, extensibility, observability, and future SaaS scale.

## Non-Negotiable Principles

1. **Agents never receive raw credentials or API secrets.**
2. **Every external side effect goes through the Tool Gateway.**
3. **Every agent has an explicit identity, mission, permissions, tool set, memory scope, budget, and version.**
4. **PostgreSQL is the primary system of record.** Google Sheets, Excel, dashboards, and exports are views/integrations, not databases.
5. **Agents do not call each other directly.** Coordination happens through durable workflows, typed domain events, and approved shared state.
6. **Deterministic business logic stays deterministic.** Scheduling, permissions, inventory arithmetic, retries, idempotency, validation, billing, and security controls must not depend on LLM judgment.
7. **All AI/model/media vendors sit behind provider abstractions.**
8. **All consequential actions are auditable.**
9. **Research and external content are untrusted input.**
10. **Insights are not facts by default.** AI-generated insights require evidence, scope, confidence, timestamps, provenance, and expiration/revalidation.
11. **Human approval is a first-class primitive.**
12. **Multi-tenancy is designed from day one.**
13. **Use explicit typed contracts.**
14. **Prefer modular monolith boundaries before premature microservices.**
15. **Context minimization is mandatory.**
16. **PII is isolated from unrelated agents.**
17. **No hidden autonomous loops.** Every recurring/self-improving behavior has triggers, budgets, stop conditions, and auditability.
18. **Writes are idempotent where practical.**
19. **Security boundaries live in code, not prompts.**
20. **Repository documentation and ADRs are authoritative.**
21. **Do not optimize vanity metrics as the final objective.** Business outcomes should incorporate revenue, margin, conversion, inventory, production capacity, and risk.
22. **Do not silently turn correlations into global knowledge.** Every learning is scoped and falsifiable.
23. **Do not over-agentize.** If deterministic software is sufficient, do not use an LLM.

## Core Agents

- Orchestrator / Supervisor
- Researcher
- Creative Strategist
- Producer
- Marketer
- Commerce Operations Agent
- Intelligence Agent

Agents are domain workers. Authentication, scheduling, event handling, permissions, secrets, audit, provider routing, and persistence are platform services.

## Repository Reading Order

Before meaningful implementation work:

1. `AGENTS.md`
2. `docs/00_PRODUCT_VISION.md`
3. `docs/01_SYSTEM_CONSTITUTION.md`
4. Relevant documents under `docs/`
5. Relevant ADRs
6. Existing implementation
7. Existing tests

## Dependency Direction

Preferred direction:

`UI/API → Application Services/Workflows → Domain → Ports/Interfaces`

Infrastructure implements ports and may depend inward. Domain modules must not import provider SDKs, database drivers, web frameworks, or vendor-specific code.

## Required Agent Definition

Every agent must declare:

- `agent_id`
- `agent_type`
- `display_name`
- `mission`
- `responsibilities`
- `allowed_tools`
- `denied_tools`
- `read_scopes`
- `write_scopes`
- `memory_scopes`
- `approval_policy`
- `model_policy`
- `run_budget`
- `monthly_budget`
- `prompt_version`
- `events_consumed`
- `events_produced`

Outputs used downstream must be structured and schema validated.

## Tool Gateway Contract

Agents request tools; agents do not execute external APIs directly.

The Tool Gateway enforces:

- tenant identity
- user/actor identity
- agent identity
- tool authorization
- resource scope
- approval requirement
- input validation
- policy checks
- rate limits
- cost/budget limits
- idempotency
- audit
- safe secret retrieval
- external execution
- normalized response
- error classification

## Action Risk Levels

- **R0:** read-only internal data
- **R1:** analysis / ideation
- **R2:** bounded-cost content generation
- **R3:** draft external action
- **R4:** publish/send externally
- **R5:** mutate commerce/inventory state
- **R6:** refund/payment/financial impact
- **R7:** credentials, permissions, tenant/security administration

R6/R7 require explicit human approval by default. R4/R5 may be tenant-configurable but remain policy controlled and auditable.

## Architectural Change Discipline

For significant changes:

1. Identify bounded context.
2. Identify trust boundaries.
3. Identify data ownership.
4. Identify affected events/contracts.
5. Check existing ADRs.
6. Add/supersede ADR if architecture changes.
7. Preserve backward compatibility where reasonable.
8. Add architectural invariant tests.

## Minimum Security/Architecture Tests

- tenant isolation
- permission denial
- approval enforcement
- input schema validation
- idempotency
- webhook signature verification
- event compatibility
- retry safety
- duplicate-event handling
- secret non-exposure
- audit completeness
- agent context isolation
- prompt-injection boundary behavior
- PII access isolation
- cross-tenant object-storage isolation

## Phase 0 Guardrail

Phase 0 must not implement Researcher, Creative Strategist, Producer, Marketer, Commerce Agent, or Intelligence Agent.

Phase 0 covers:

- repository/config foundation
- tenant/user models
- database conventions
- agent registry
- tool registry
- permission engine
- approval primitives
- audit model
- typed event contracts
- security primitives
- test foundation
- observability foundation

Do not skip Phase 0 to create a visually impressive demo.
