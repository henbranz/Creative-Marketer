# 03 — Agent Registry

## Orchestrator / Supervisor

**Mission:** coordinate goals and workflows while remaining non-privileged.

### Inputs
- user goals
- workflow state
- approved internal context
- agent capability registry

### Outputs
- task plans
- agent/workflow requests
- approval requests
- escalation reasons

### Must Not
- access raw credentials
- make direct social/commerce writes
- grant itself permissions

---

## Researcher

**Mission:** maintain current, evidence-backed market and creative research.

### Research Areas
- competitor brands/products
- organic creative patterns
- hooks/scripts
- shot styles
- UGC formats
- music/content patterns
- platform trends
- seasonal events
- holidays
- product opportunities
- audience language

### Output
`ResearchSnapshot`

Required properties:
- topic/scope
- sources
- evidence
- claims
- confidence
- created_at
- valid_until
- triggers for refresh

### Refresh Triggers
- TTL
- new product
- major campaign
- performance anomaly
- user request
- scheduled refresh
- material market event

Research snapshots should be cached/reused; do not run deep research before every operation.

---

## Creative Strategist

**Mission:** turn product/brand/research/performance context into testable creative concepts.

### Inputs
- Product Digital Twin
- brand identity
- audience
- current research
- prior performance
- current business goal
- available assets
- inventory/production constraints when relevant

### Output
`CreativeConcept`

Required:
- concept_id
- format
- hook
- narrative
- scenes/shots
- voiceover/script
- CTA
- target audience
- channel intent
- hypothesis
- success metric
- variations
- required assets

Every concept must be testable. “Make a nice video” is invalid.

---

## Producer

**Mission:** convert approved concepts into production plans and media assets.

### Responsibilities
- inspect Asset Library
- determine missing inputs
- request only missing source assets
- build provider-neutral production plan
- route generation through provider adapters
- create required aspect-ratio outputs
- perform technical/brand QA
- preserve lineage from concept to output

### Must Not
- publish content
- access commerce PII
- hold provider credentials

### Aspect Ratio Principle
Prefer a high-quality master asset plus intelligent reframe/crop/extend when suitable. Regenerate only when the composition requires it.

---

## Marketer

**Mission:** operate approved organic publishing experiments and maximize meaningful business outcomes.

### Responsibilities
- create experiment matrix
- vary hook/caption/CTA/time/channel/link
- schedule approved publications
- publish through social tools
- collect platform metrics
- maintain UTM/link lineage

### Optimization
Do not optimize solely for views/likes.

Optimization objective can include:
- CTR
- conversion rate
- revenue
- contribution margin
- inventory availability
- production capacity
- audience quality
- retention/repeat purchase later

---

## Commerce Operations Agent

**Mission:** reason about operational commerce work while deterministic services remain authoritative for order/inventory state.

### Responsibilities
- review new orders
- flag exceptions
- coordinate production requirements
- summarize fulfillment status
- prepare structured exports/views
- reconcile operational discrepancies
- propose actions that require approval

### Important
Shopify/internal commerce DB remain systems of record. Google Sheets is a synchronized operational view/export.

---

## Intelligence Agent

**Mission:** convert observations/events into bounded, falsifiable, evidence-backed insights.

### Inputs
- creative metadata/features
- post metrics
- traffic
- attribution
- orders/revenue
- inventory/availability
- user edits/rejections
- experiment definitions

### Output
`Insight`

Required:
- statement
- evidence
- sample size
- metric delta
- confidence
- scope
- provenance
- created_at
- valid_until
- validation status

### Must Not
Promote a single anecdote or correlation into global knowledge.

---

## Agent Versioning

Each agent version should preserve:

- prompt/system instruction version
- model policy
- tool policy
- schema version
- created_by
- created_at
- rollout status

Performance changes should be attributable to versions.

## Phase 0 Registry Control Plane

```text
AgentDefinition
      ↓
AgentVersion[] (immutable)
      ↓
AgentActivation (one mutable pointer)
      ↓
ResolveActiveAgentVersion
      ↓
ResolvedAgentVersion (still inert)
```

`AgentDefinition` owns stable UUID identity, explicit platform/tenant scope, canonical `agent_key`
and extensible `agent_type`, optional platform-template relationship, lifecycle, and creator
provenance. Behavior does not live on the definition. Platform keys are globally unique; tenant
keys are unique within a tenant. Archived definitions continue to reserve their key so historical
identity cannot be confused with a replacement.

Each immutable `AgentVersion` contains display name, mission, responsibilities, bounded system
instructions, prompt revision, provider-neutral model intent, precise run/period budgets, declared
read/write/memory scopes, declared allowed/denied tool keys, approval-policy reference, optional
language-neutral output-contract reference, schema version, creator provenance, and a canonical
configuration digest. Changes create the next integer version. Definition row locks serialize
concurrent version allocation; `(definition_id, version_number)` remains database-unique.

Model policy names a logical profile and required capabilities; it does not name an OpenAI,
Anthropic, Google, or other provider model. Budgets store cost as canonical decimal strings with a
three-letter currency, never floating-point numbers. They are declarations only and are not yet
enforced.

Scope/tool identifiers are canonical, unique, bounded keys with no global wildcard. A key appearing
in both allow and deny declarations is rejected. These declarations are input to later governance:

```text
AgentVersion allowed tools/scopes ≠ authorization
```

Unknown tool declarations grant nothing. Future authorization combines registry intent with Tool
Registry, tenant policy, Permission Engine, approvals, budgets, and trusted execution identity.

Activation is a separate row keyed by definition, with a composite foreign key proving the active
version belongs to that definition. Re-activating an older immutable version is rollback and emits
a new audit decision. Concurrent activations serialize on the definition and can leave only one
active pointer. Active definitions may become disabled or archived, and disabled definitions may
be archived. Disabled definitions can receive staged versions but cannot activate or resolve them.
Archived definitions are terminal; an explicit enable workflow is deferred.

Tenant resolution first uses a tenant activation. Without one, it follows only the definition's
explicit platform-template link and that template's explicit active version. Template activation
upgrades are followed dynamically; resolved values preserve tenant request and platform-template
provenance. Disabled/archived definitions, disabled templates, missing activations, and all fuzzy or
implicit fallback fail with `AgentUnavailable`.

Tenant mutations require a trusted human `ExecutionContext` and commit atomically with append-only
audit. Audit stores agent/version IDs and digests, never prompt or full configuration payloads.
Platform writes remain migration/internal-control-plane only, and no public mutation route exists.

```text
Agent Registry ≠ Agent Runtime
```

Resolved versions are inert configuration. TASK-005 does not execute prompts, choose providers,
call models, grant tools, store memory, or create AgentRun records.

## Definition Ownership

Agent ownership is explicit; nullable `tenant_id` alone must not distinguish platform and tenant definitions.

Each definition declares:

- `scope_kind`: `platform` or `tenant`
- `tenant_id`: forbidden for platform definitions and required for tenant definitions
- optional `platform_template_id`: the platform template from which a tenant definition derives

Database constraints enforce the valid combinations. Platform definitions are immutable templates, not ambient global records silently returned by tenant queries.

Resolution precedence is explicit:

1. an active tenant-owned definition/version selected by tenant policy
2. an explicitly referenced active platform template/version
3. denial when neither is configured

Tenant definitions never override a platform template merely by sharing an agent type or display name. Version selection and rollout remain historically traceable.
