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
