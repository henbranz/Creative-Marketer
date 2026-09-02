# 02 — Architecture

## Logical Architecture

```text
                          ┌────────────────────┐
                          │   Web / Mobile UI   │
                          └─────────┬──────────┘
                                    │
                          ┌─────────▼──────────┐
                          │      API Layer      │
                          └─────────┬──────────┘
                                    │
                   ┌────────────────┴────────────────┐
                   │         Application Layer        │
                   │ Commands / Queries / Workflows   │
                   └───────┬───────────────┬─────────┘
                           │               │
                  ┌────────▼───────┐ ┌────▼──────────┐
                  │ Agent Runtime   │ │ Workflow Engine│
                  └────────┬───────┘ └────┬──────────┘
                           │               │
                           └───────┬───────┘
                                   │
                          ┌────────▼────────┐
                          │   Tool Gateway   │
                          └──────┬─────┬────┘
                                 │     │
                  ┌──────────────┘     └──────────────┐
                  │                                   │
        ┌─────────▼────────┐                ┌─────────▼─────────┐
        │ Internal Services │                │ External Providers │
        │ DB/events/storage │                │ Social/AI/Shopify  │
        └───────────────────┘                └────────────────────┘
```

## Recommended Initial Shape

A **modular monolith** for domain/application/API with separate durable worker processes where needed.

Suggested future structure:

```text
apps/
  web/
  api/

services/
  worker/
  agent-runtime/

packages/
  domain/
  schemas/
  events/
  permissions/
  provider-interfaces/
  observability/

infra/
docs/
```

Exact structure may evolve after Phase 0 review. Avoid service-per-agent.

## Bounded Contexts

### Identity & Tenancy
Users, memberships, tenant settings, roles.

### Agent Governance
Agent registry, versions, model policies, budgets.

### Tool Governance
Tool registry, permissions, risk, approvals, execution.

### Catalog
Products, SKUs, brand, claims, assets, audiences.

### Research
Research snapshots, sources, evidence, competitors, trends.

### Creative
Concepts, hypotheses, scripts, briefs, variants.

### Production
Production plans, asset requirements, generations, QA, provider jobs.

### Marketing
Experiments, channel posts, schedules, captions, links, UTMs, metrics.

### Commerce
Orders, line items, fulfillment, inventory, production requirements.

### Intelligence
Observations, insight candidates, validated insights, confidence, scope, expiry.

### Audit & Observability
Runs, tool calls, approvals, events, errors, cost, traces.

## Provider Abstraction

External providers must implement interfaces such as:

```text
ImageGenerationProvider
VideoGenerationProvider
VoiceProvider
MusicProvider
SocialPublishingProvider
CommerceProvider
ResearchSourceProvider
```

Provider-specific fields must be contained in adapter/config layers.

## Workflow Orchestration

Use durable workflow orchestration for processes that:

- span minutes/days
- wait for user approval
- wait for external processing
- retry after failures
- schedule future work
- need reliable resumability

Temporal is the current recommended candidate.

## Agent Orchestration

The Orchestrator is not root.

It may:

- decompose goals
- choose next worker
- assemble minimal context
- request workflows
- request approval
- react to failures

It may not:

- directly read secrets
- bypass Tool Gateway
- grant permissions
- perform privileged external writes by itself

## Event Driven Collaboration

Agents publish/consume typed domain events rather than direct calls.

Example:

```text
creative.concept.created.v1
production.asset.ready.v1
marketing.post.published.v1
commerce.order.created.v1
intelligence.insight.validated.v1
```

## Analytics Evolution

Start with PostgreSQL.

Move high-volume event/performance analytics to ClickHouse/BigQuery only when scale justifies the complexity.
