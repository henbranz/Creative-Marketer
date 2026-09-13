# ADR-026: Agent Runtime and Model Provider Boundary

## Status

Superseded in part by ADR-034

## Context

Creative Marketer needs real model inference without allowing a provider SDK to become the system's
agent registry, authorization layer, tool runtime, workflow engine, or source of Product truth.
Research inputs include tenant-confidential Product data and adversarial web evidence. Every billed
run needs reproducible configuration, input, routing, usage, and outcome provenance.

## Decision

`agent_runtime` is an application-owned bounded context. The provider-neutral Agent Registry remains
the authoritative source of immutable AgentVersion configuration. An `AgentRun` freezes the exact
AgentVersion/configuration digest, ProductKnowledgeSnapshot, ResearchContextManifest, selected
evidence block identities, output contract, logical model profile, initiating User, correlation ID,
and reserved budget before background execution.

`ModelRouter` maps a logical profile to an exact provider, model, capabilities, reasoning effort,
output bound, route version, and pricing version. The initial route is `research_balanced` → OpenAI
Responses API → `gpt-5.6-terra`, medium reasoning, 6,000 maximum output tokens, using the
`openai-2026-09-11` USD price snapshot. No silent fallback is permitted. Provider adapters implement
the inward `ModelProvider` port; SDK types and credentials stay in infrastructure.

Researcher V1 makes at most one model call and declares no Tools, functions, hosted web search,
memory, handoffs, or chat session. Trusted Product context and untrusted evidence are separate
message sections. Structured output must satisfy the canonical JSON Schema and every factual
finding must cite an exact frozen `(EvidenceSnapshot ID, block index, block digest)` tuple. The
application validates both after the provider returns. Accepted output becomes one immutable
`ResearchSnapshot`; it never mutates Product Brain or creates an Insight.

AgentRuntime, rather than a provider SDK, owns a maximum-two-attempt transport policy. Only
retryable failures with no provider response or reported usage receive the second attempt;
refusals, malformed output, and post-response validation failures do not. The attempts are part of
one logical model call, and the SDK adapter disables its own retry multiplication.

Run and period budgets are enforced transactionally. Decimal cost uses the exact frozen price
snapshot and provider-reported token counts. User request, Audit, and Outbox event commit together;
an Inbox handler starts a deterministic Temporal workflow. Activities resolve the authoritative
AgentRun from PostgreSQL under a separate configured workload identity. Temporal history contains
IDs only.

## Consequences

Provider replacement and route changes do not rewrite AgentVersion semantics. Citation fabrication,
prompt-based tool escalation, cross-tenant access, and accidental duplicate active runs fail closed.
The first version intentionally gives up autonomous loops and provider fallback. Each logical model
call has a durable attempt with a pre-I/O checkpoint and a response-metadata checkpoint. A crash can
still require conservative operator recovery and may cause a later billed re-run; exactly-once
inference is not claimed. Recovery and uncertain-cost policy are defined by ADR-027. Production deployments must review the
provider's data handling and inject both provider and workload credentials through deployment
secrets.

The Terra route and pricing above are the truthful original decision. ADR-034 supersedes only the
current reasoning-model route policy; historical records remain immutable.
