# Creative Marketer

**AI Creative & Growth Operating System for product founders.**

Creative Marketer is designed as a production-grade, multi-tenant platform that closes the full operating loop for a product business:

**Research → Creative Strategy → Content Production → Publishing → Commerce → Measurement → Intelligence → Continuous Improvement**

The first product focus is the **Autonomous Creative Loop**: understand a product, research its market, generate testable creative concepts, produce media, publish to social channels, measure performance, and turn results into evidence-backed learning for the next cycle.

## Core Agents

- **Orchestrator / Supervisor** — coordinates work but does not hold business credentials.
- **Researcher** — monitors trends, competitors, creative patterns, seasonal opportunities, and market signals.
- **Creative Strategist** — produces structured concepts, scripts, hooks, shot plans, variants, and hypotheses.
- **Producer** — turns concepts into assets through pluggable media providers and requests only missing source assets.
- **Marketer** — schedules/publishes approved content and manages structured experiments.
- **Commerce Operations Agent** — handles order/fulfillment/inventory workflows through controlled commerce tools.
- **Intelligence Agent** — converts events and performance data into scoped, evidence-backed, expiring insights.

## Architectural Thesis

Agents are **workers inside the platform**, not the platform itself.

The durable core is:

- domain model
- event model
- permission model
- Tool Gateway
- audit trail
- data ownership
- workflow engine
- provider abstractions
- security boundaries

Every model, agent implementation, and external provider should be replaceable without rewriting the platform.

## Documentation

Read in this order:

1. [`AGENTS.md`](AGENTS.md)
2. [`docs/00_PRODUCT_VISION.md`](docs/00_PRODUCT_VISION.md)
3. [`docs/01_SYSTEM_CONSTITUTION.md`](docs/01_SYSTEM_CONSTITUTION.md)
4. [`docs/02_ARCHITECTURE.md`](docs/02_ARCHITECTURE.md)
5. [`docs/03_AGENT_REGISTRY.md`](docs/03_AGENT_REGISTRY.md)
6. [`docs/04_TOOL_PERMISSION_MATRIX.md`](docs/04_TOOL_PERMISSION_MATRIX.md)
7. [`docs/05_DATA_MODEL.md`](docs/05_DATA_MODEL.md)
8. [`docs/06_EVENT_MODEL.md`](docs/06_EVENT_MODEL.md)
9. [`docs/07_SECURITY.md`](docs/07_SECURITY.md)
10. [`docs/08_MEMORY_AND_INSIGHTS.md`](docs/08_MEMORY_AND_INSIGHTS.md)
11. [`docs/09_WORKFLOWS.md`](docs/09_WORKFLOWS.md)
12. [`docs/10_MVP_ROADMAP.md`](docs/10_MVP_ROADMAP.md)
13. [`docs/11_PHASE_0_IMPLEMENTATION_PLAN.md`](docs/11_PHASE_0_IMPLEMENTATION_PLAN.md)
14. [`docs/12_CODEX_BOOTSTRAP_PROMPT.md`](docs/12_CODEX_BOOTSTRAP_PROMPT.md)
15. [`docs/ADR/`](docs/ADR/)

## Initial Technology Direction

This is a starting recommendation, not an irreversible commitment:

- **Web:** Next.js + TypeScript
- **Backend:** Python + FastAPI
- **Primary DB:** PostgreSQL
- **Vector search:** pgvector initially
- **Cache / ephemeral coordination:** Redis where justified
- **Durable workflows:** Temporal
- **Agent runtime:** provider-abstracted; OpenAI Agents SDK is a candidate implementation
- **Object storage:** S3/GCS-compatible storage
- **Schemas:** Pydantic / JSON Schema / OpenAPI contracts
- **Observability:** OpenTelemetry + structured agent/tool tracing
- **Deployment:** Docker; cloud provider selected later
- **Secrets:** managed secrets service + KMS
- **Auth:** managed identity provider with strong tenant support

Avoid premature microservices. Begin with a modular monolith plus durable workers and clean extraction boundaries.

## Phase 0

No business agent should be implemented before the platform foundation exists.

Phase 0 establishes:

- tenancy
- users
- agent registry
- tool registry
- permission engine
- approval engine
- audit model
- event contracts
- DB conventions
- secrets boundaries
- test infrastructure
- observability
- architectural invariants

See [`docs/11_PHASE_0_IMPLEMENTATION_PLAN.md`](docs/11_PHASE_0_IMPLEMENTATION_PLAN.md).

## Development

See [`DEVELOPMENT.md`](DEVELOPMENT.md) for prerequisites, local startup, repository layout, and the complete quality-check command set.
