# Agent Runtime and Evidence-Grounded Researcher

## Boundary

```text
Agent Registry
      ↓ exact active AgentVersion
AgentRun (immutable provenance + reserved budget)
      ↓
Researcher Context Builder
      ↓ trusted Product / untrusted evidence separation
ModelRouter
      ↓ exact route + versioned pricing
ModelProvider
      ↓ structured output only
JSON Schema + Citation Validator
      ↓
Immutable ResearchSnapshot
```

Agent Registry configures intent and limits; it contains no provider/model name or credential.
AgentRuntime owns execution records, routing, budgets, context construction, normalized usage, cost,
and outcome persistence. The OpenAI Responses adapter is infrastructure-only. Researcher does not
use the Agents SDK's tools, hosted web search, memory, tracing, or orchestration facilities.

## Researcher V1 configuration

The explicit development bootstrap creates one tenant `researcher` definition with profile
`research_balanced`; capabilities `text`, `reasoning`, and `structured_output`; one turn; one model
call; zero tool calls; 12,000 total tokens; USD 0.15 per run; 20 runs/USD 3 per day; exact Catalog,
Research, and snapshot scopes; no memory; no Tool declarations; and output contract
`research.research_snapshot` version 1. It never runs automatically and refuses staging/production.

```bash
BOOTSTRAP_TENANT_ID=<uuid> BOOTSTRAP_USER_ID=<owner-user-uuid> make researcher-bootstrap
```

## Run lifecycle and durability

An active OWNER/ADMIN posts a bounded idempotency key. In one tenant-scoped transaction the API
resolves all inputs, reserves the period budget, writes `PENDING` AgentRun, Audit, and
`agent.run.requested.v1`, then returns HTTP 202. At most one PENDING/RUNNING Researcher run exists per
tenant/Product/requested definition.

The event consumer starts `tenant/<tenant>/agent-run/<run>` in Temporal. Duplicate event delivery
uses the same workflow ID. The Activity claims `PENDING → RUNNING` transactionally, records the
configured workload identity (not the initiating User), creates a leased `ModelAttempt`, and
reconstructs exact frozen inputs from
PostgreSQL, calls the provider, validates output, then commits `SUCCEEDED` plus exactly one
ResearchSnapshot or `FAILED` with a safe code. Terminal rows and snapshots are database-immutable.
The requested and resolved definitions must still be active at claim time, but an AgentVersion
activation change does not rewrite an already-authorized run.

Temporal history contains tenant, run, and correlation UUIDs only. No prompt, Product text,
evidence, output, provider response, or key enters workflow state. AgentRuntime owns one bounded
retry for rate-limit/timeout/connection outcomes that contain no response or usage; refusals,
invalid output, and all post-response failures are terminal. SDK automatic retries are disabled.
These transport attempts remain one logical model call and cannot become an autonomous reasoning
loop.

## Context and injection isolation

Selection is deterministic: no more than 20 latest active sources, 10 blocks per source, 120 total
blocks, or 120,000 evidence characters. Category, capture time, and stable IDs determine order.
Evidence older than 30 days is marked stale. Each selected block binds EvidenceSnapshot ID, Source
ID, block index/kind, a content digest, and stale flag. Product context is drawn only from the exact
ProductKnowledgeSnapshot. Assets and object keys are absent.

System instructions are separate from the user message. The user message labels Product context as
trusted and external evidence as untrusted quoted data. Evidence cannot add tools because the
request declares an empty tool list and no platform Tool Gateway call occurs. The context digest
binds configuration, Product, manifest, and selected block identities.

## Output, freshness, privacy

`research.research_snapshot.v1.json` limits findings, citations, gaps, suggestions, and string
lengths. Every finding has category, statement, model-assessed confidence, scope, implication, and
one or more exact citations. Unknowns belong in `research_gaps`; suggested sources are proposals and
are never fetched automatically. The semantic digest excludes row IDs, timestamps, and provider
metadata.

Snapshots expire after seven days. Reads derive `current`, `stale`, or `outdated`; `outdated` means
the latest Product snapshot or current Research manifest digest differs. This never mutates history.
Findings remain tenant-confidential state and are excluded from Audit, Events, telemetry, logs, and
Temporal history. Events carry IDs, digests, safe status, and numeric usage only.

## Routing, pricing, and operations

The initial exact route is `research_balanced` → `openai` → `gpt-5.6-terra`, route
`openai-gpt-5.6-terra-2026-09`, medium reasoning, 6,000 output tokens. Pricing snapshot
`openai-2026-09-11` is USD 2.00 per million input tokens and USD 12.00 per million output tokens.
Route capability/currency and worst-case cost are checked before reservation. Provider-reported
usage is checked after the call. Response identity, usage, and Decimal cost are durably recorded on
the attempt before local output validation, so a crash can be classified without storing content.
The same fields are persisted on terminal AgentRuns even when post-call output validation fails.

Enable real inference only with deployment-injected `MODEL_PROVIDER_BACKEND=openai`,
`OPENAI_API_KEY`, and a non-placeholder `AGENT_WORKLOAD_ID` in deployed environments. CI uses fake
providers and requires no key. The following manual fixture contains synthetic public data and
makes exactly one billed call:

```bash
RUN_OPENAI_SMOKE_TEST=1 OPENAI_API_KEY=<secret> make researcher-live-smoke
```

Safe OTel lifecycle spans and bounded metrics expose route, provider/model, status, counts, duration,
tokens, cost, and invalid-citation totals. They never contain tenant/product/source/run IDs or
content. Expired leases derive a read-only `recovery_required` operational state while preserving
the authoritative `RUNNING` row. Recovery is an explicit, trusted CLI-only operation; see
`16_AGENT_RUN_RECOVERY.md`. Exactly-once provider billing is not promised.
