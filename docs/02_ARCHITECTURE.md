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
  research-ingestion/     # only when trust-zone isolation is needed
  connector-executor/     # before credentialed integrations

packages/
  backend/
    src/creative_marketer/
      contexts/
      platform/
      bootstrap/
  contracts/
    openapi/
    events/
    tools/
  web-client/

migrations/
tests/
  architecture/
  integration/
  security/
  contract/
  e2e/

infra/
docs/
```

Exact structure may evolve after Phase 0 review. Avoid service-per-agent.

## Dependency Direction

```text
Delivery adapters → Application services/use cases → Domain
Infrastructure adapters ───────────────────────────────┘
                         implement outbound ports owned inward
```

Rules:

- domain code depends only on domain code and a small pure shared kernel
- application code depends on domain code and defines the outbound ports it needs
- infrastructure implements ports and depends inward
- delivery adapters, including FastAPI routes and worker entrypoints, call application use cases
- workers import reusable application packages, never FastAPI route modules
- cross-context access uses public application interfaces or versioned contracts
- one context does not write another context's tables directly
- provider and workflow SDK types never appear in domain contracts

Domain modules must not import FastAPI, SQLAlchemy/database drivers, the OpenAI SDK, provider SDKs, the Temporal SDK, or external service clients. Architecture tests should enforce these rules.

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

Provider abstraction does not imply full interchangeability. Each adapter exposes capability metadata including supported operations, models and versions, aspect ratios, synchronous/asynchronous behavior, moderation requirements, regions, cost metadata, rate limits, and feature limitations. Application services select only providers whose declared capabilities satisfy the request.

Important invocations persist the selected provider, model/version, relevant capability decision, cost, external job reference, and input/output lineage. Stable domain concepts remain provider neutral; narrowly scoped provider extension data may exist only at adapter/configuration boundaries.

## Workflow Orchestration

Use durable workflow orchestration for processes that:

- span minutes/days
- wait for user approval
- wait for external processing
- retry after failures
- schedule future work
- need reliable resumability

Temporal is the current recommended candidate.

ADR-004 remains proposed. Temporal is adopted only after a Phase 0 spike demonstrates:

- pause and durable resume for human approval
- long-running media-generation polling
- durable scheduled publishing
- crash/restart recovery
- retry and non-retryable error behavior

Simple synchronous operations must not use Temporal merely because it is available.

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

Deterministic application workflows may synchronously invoke a bounded Agent Runtime when that is the simplest safe operation. The workflow validates and persists the result before publishing any resulting domain facts. The agent does not directly invoke another business agent.

## Tool Gateway Boundary

The Tool Gateway is a logical enforcement protocol for external side effects and privileged connector operations. It may begin in the modular monolith and later be split across deployments without changing its contract.

Conceptual responsibilities are:

1. policy decision
2. approval validation
3. invocation orchestration
4. idempotency
5. connector execution
6. credential resolution
7. audit
8. event recording

Ordinary internal domain/application reads and writes do not pass through the Tool Gateway.

## Event Driven Collaboration

Agents publish/consume typed domain events rather than direct calls.

Commands express intent and may be rejected. Events describe immutable facts that have occurred; events must not be used as disguised commands. Not every in-process call should become asynchronous.

Cross-context facts are published transactionally with the state change, using a PostgreSQL outbox initially. Consumers assume at-least-once delivery and use an inbox or equivalent idempotent deduplication. Global event ordering is not assumed.

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

## Contract Ownership

- Backend API contracts are the source for generated OpenAPI.
- TypeScript API clients and DTO types are generated from that OpenAPI artifact.
- Event and tool schemas use versioned language-neutral JSON Schema or an equivalent canonical representation.
- Generated artifacts are reproducible and checked for drift in CI.
- Python and TypeScript DTOs are not maintained independently as parallel sources of truth.

## Explicitly Deferred Decisions

Phase 0 does not yet select or fully specify:

- a permanent authentication vendor
- a production cloud or deployment topology
- a dedicated analytics database
- a specialized vector database beyond the initial pgvector direction
- cross-tenant learning or aggregation
- final object-storage vendor and topology
- provider-specific commerce, social, research, or media integrations

These decisions require product, compliance, scale, or provider evidence not yet available. Their boundaries must still be preserved during Phase 0.

## Implemented Tool Gateway boundary (Phase 0)

Tool execution is an internal application boundary, not an HTTP execution endpoint. A trusted
agent invocation supplies only the initiating `ExecutionContext` and requested tenant Agent ID.
The gateway authoritatively resolves active Agent, Tool, and Permission versions, validates and
normalizes input, derives resource requirements, evaluates permission, enforces every obligation,
binds one immutable `ToolCall` to the exact versions, and only then invokes an exact-version
executor mapping. Executor I/O occurs after durable `EXECUTING` state commits and outside the
database transaction. Outcome state, idempotency state, Audit, and Outbox evidence commit together.
