# 01 — System Constitution

## What the System Is

Creative Marketer is a platform that hosts autonomous/semi-autonomous domain agents under explicit governance.

## What the System Is Not

- not a collection of prompts connected by webhooks
- not a single super-agent with every credential
- not Google Sheets acting as a database
- not an LLM deciding security policy
- not a direct chain where one agent calls the next
- not a permanent commitment to any model or media vendor

## Core Platform Responsibilities

The platform owns:

- tenant isolation
- identity/authentication
- agent identity
- permissions
- approvals
- credentials/secrets
- data ownership
- event contracts
- workflow durability
- idempotency
- audit
- observability
- budgets/limits
- provider routing
- storage
- policy enforcement

## Agent Responsibilities

Agents own bounded cognitive work:

- research synthesis
- creative ideation
- script/concept creation
- production planning
- marketing experiment planning
- commerce exception reasoning
- insight generation

## Deterministic vs Agentic

Use deterministic code for:

- permission decisions
- scheduling
- retries
- webhooks
- inventory quantities
- order state transitions
- duplicate detection
- idempotency
- token storage
- schema validation
- risk classification rules
- billing
- audit logging

Use LLM/agent reasoning for:

- market interpretation
- synthesis
- ideation
- structured creative strategy
- semantic comparison
- extracting hypotheses
- explaining performance
- suggesting next experiments

## Invariants

1. No raw secret enters an LLM prompt.
2. No cross-tenant query succeeds without explicit platform-level authorization.
3. No external write occurs without Tool Gateway policy evaluation.
4. No high-risk action bypasses approval policy.
5. No external event is processed twice in a way that duplicates side effects.
6. No AI insight is considered permanent without provenance/scope.
7. No agent gets data merely because the platform has access to it.
8. No provider SDK leaks into domain modules.
9. Every external side effect is attributable to tenant, actor, agent/run, tool, and request.
10. Every autonomous workflow has a stop condition.

## Autonomy Modes

UI may expose simplified modes:

- **Assisted**
- **Semi-autonomous**
- **Autonomous**

Internally, autonomy must resolve into per-tool/per-risk policies rather than a single boolean.

## Source of Truth

- Product/order/agent/audit/insight state → PostgreSQL
- Binary assets → object storage
- Google Sheets / Excel → exports or synchronized operational views
- External platforms → authoritative only for platform-owned external objects (e.g., post status), synchronized into internal records
