# 05 — Data Model

This document defines conceptual entities. Phase 0 should convert these into explicit schemas with tenant ownership and audit fields.

## Identity / Tenancy

### Tenant
- id
- name
- status
- plan
- locale
- timezone
- created_at
- updated_at

### User
- id
- email
- status

### ExternalIdentity
- id
- user_id
- issuer
- opaque subject
- status
- created_at
- updated_at

`(issuer, subject)` is globally unique and is the only authentication lookup key. A User may own multiple external identities. Subjects retain exact case and are never inferred or linked from email.

### Membership
- tenant_id
- user_id
- role
- status
- created_at
- updated_at

The identity records use the `identity` PostgreSQL schema. Tenant slugs and normalized user emails are unique. Membership's `(tenant_id, user_id)` primary key allows one user to join many tenants while prohibiting duplicate relationships within one tenant. Permanent authentication provider selection remains deferred.

## Agent Governance

### AgentDefinition
- id
- scope_kind (`platform` or `tenant`)
- tenant_id (null only when `scope_kind = platform`)
- platform_template_id nullable
- agent_key
- agent_type
- status
- created_by_actor_kind / created_by_actor_id
- created_at / updated_at

Ownership constraints:

- `scope_kind = platform` requires `tenant_id IS NULL`
- `scope_kind = tenant` requires `tenant_id IS NOT NULL`
- tenant resolution references a platform template explicitly; nullable ownership never creates implicit fallback
- platform definitions cannot reference templates
- tenant templates reference only platform definitions, enforced by a database trigger
- platform `agent_key` is globally unique; tenant `agent_key` is unique per tenant
- archived definitions retain their stable key and cannot be resurrected

### AgentVersion
- id
- definition_id
- scope_kind / tenant_id (denormalized and ownership-checked for RLS)
- version_number
- display_name / mission / responsibilities
- bounded system_instructions
- prompt_revision
- model_policy
- run_budget_policy / period_budget_policy
- read_scopes / write_scopes / memory_scopes
- allowed_tool_keys / denied_tool_keys
- approval_policy_key
- output_contract_key / output_contract_version nullable
- configuration_schema_version
- configuration_digest
- created_by_actor_kind / created_by_actor_id / created_at

Versions are immutable. Runtime receives `SELECT` and `INSERT`, but no `UPDATE` or `DELETE`.
`UNIQUE (definition_id, version_number)` and definition-row locking provide monotonic concurrent
allocation. Nested provider-neutral policies are stored as JSONB behind typed contracts.

### AgentActivation
- definition_id (primary key)
- active_version_id
- scope_kind / tenant_id
- activated_by_actor_kind / activated_by_actor_id
- activated_at

The composite `(definition_id, active_version_id)` foreign key ensures the version belongs to the
definition. Activation changes never mutate a version and support audited rollback.

### AgentRun
- id
- tenant_id
- agent_version_id
- trigger_type
- trigger_id
- goal
- status
- started_at
- completed_at
- input_refs
- output_refs
- cost
- token_usage
- error_code

## Tool Governance

### ToolDefinition
- id
- tool_key (globally unique, stable, canonical, never reused)
- category
- status (`active`, `disabled`, `archived`)
- created_by_actor_kind / created_by_actor_id
- created_at / updated_at

Definitions are platform-global capability identities. Tenants cannot create them. Archived keys
remain reserved, stable identity fields are database-trigger protected, and archived is terminal.

### ToolVersion
- id
- definition_id / version_number
- display_name / description
- risk_level (`R0`–`R7`)
- side_effect_class
- execution_class
- credential_boundary
- idempotency_requirement
- input_schema / output_schema (immutable JSON Schema 2020-12 snapshots)
- input_schema_digest / output_schema_digest
- capability_tags
- configuration_schema_version / configuration_digest
- created_by_actor_kind / created_by_actor_id / created_at

Versions are immutable and unique by `(definition_id, version_number)`. Definition row locks
serialize monotonic allocation. Canonical SHA-256 digests cover schema snapshots and all semantic
classification fields. A schema or classification change creates a new version.

### ToolActivation
- definition_id (primary key)
- active_version_id
- activated_by_actor_kind / activated_by_actor_id
- activated_at

The composite definition/version foreign key proves ownership. Activation supports audited
forward rollout and rollback without rewriting history. Active resolution fails closed for
unknown, disabled, archived, unactivated, or internally inconsistent records.

### ToolPermission
- id
- tenant_id
- agent_definition_id
- tool_definition_id
- status
- created_at / created_by

### ToolPermissionVersion
- id / permission_id / tenant_id
- version_number
- effect (`GRANT` or `DENY`)
- allowed_scopes / allowed_environments
- approval_behavior
- policy_schema_version / configuration_digest
- created_at / created_by

### ToolPermissionActivation
- permission_id (primary key)
- tenant_id
- active_version_id
- activated_at / activated_by

`ToolPermission` is a stable tenant-owned relationship between a tenant AgentDefinition and a
platform ToolDefinition. `ToolPermissionVersion` stores immutable effect, scope, environment, and
approval-behavior revisions; `ToolPermissionActivation` selects one active revision and supports
audited rollback. The relationship is bound to the requested tenant AgentDefinition rather than a
resolved platform template. Agent tool keys and Registry existence remain declarations, not
permission.

### ToolCall
- id
- tenant_id
- agent_run_id
- tool_id
- requested_input
- normalized_input
- approval_id
- idempotency_key
- request_digest
- status
- external_outcome (`not_started`, `confirmed`, `unknown`, `reconciled`)
- external_reference
- result_ref
- started_at
- completed_at

### ApprovalRequest
- id
- tenant_id
- requested_by_actor_kind / requested_by_actor_id
- requested/resolved AgentDefinition IDs
- AgentVersion ID / configuration digest
- ToolDefinition ID / ToolVersion ID / configuration digest / tool key
- risk_level
- ToolPermission ID / version ID / configuration digest / engine version
- scope request digest
- resource_type / resource_id optional
- environment
- normalized input digest
- platform-generated idempotency key
- action digest
- canonicalization_version
- created_at
- expires_at

`ApprovalRequest` is immutable and stores no raw tool payload. Its `ActionBindingV1` digest is also
the idempotency request digest. Canonicalization version 1 is strict JSON with lexicographically
sorted object keys, UTF-8 serialization, compact separators, and only null, boolean, JSON-safe
integer, string, array, and string-keyed object values. Floats, out-of-range integers, bytes, dates,
custom objects, unordered collections, invalid Unicode, and credential-shaped fields or values are
rejected.

The runtime's narrowly scoped `UPDATE` privilege on request rows exists only so PostgreSQL permits
tenant-scoped `SELECT ... FOR UPDATE` serialization. A database trigger rejects every actual
update or delete, preserving immutability even if application code is bypassed.

### ApprovalDecision
- id / tenant_id / approval_request_id
- decision (`APPROVE` or `DENY`)
- decided_by_user_id / decided_by_actor_kind
- decided_at
- reason_code / safe_note optional

Exactly one immutable terminal decision may exist per request. Active owners/admins decide R0-R5;
R6 requires an active owner. R7 is denied before approval. Requests expire after seven days for
R0-R4, 24 hours for R5, and one hour for R6.

### ApprovalRevocation
- id / tenant_id / approval_request_id
- revoked_by_user_id / revoked_at / reason_code

Revocation is append-only and takes precedence over decision and expiry when state is derived.

### IdempotencyRecord
- id / tenant_id
- ToolDefinition ID / ToolVersion ID
- platform-generated idempotency key
- request digest (the ActionBinding digest)
- state / attempt count
- current attempt ID / lease expiry while executing
- safe result reference optional
- reconciliation outcome optional
- created_at / updated_at

The logical-operation uniqueness key is `(tenant_id, tool_definition_id, idempotency_key)`.
ToolVersion remains digest-bound, so reuse of a key for another version conflicts. An expired
execution lease does not permit automatic takeover. Unknown external outcomes require explicit
reconciliation before any retry.

## Product Digital Twin

Phase-1 TASK-001 supersedes the early conceptual shape below with separate current-state
`Brand`/`BrandProfile`, `Product`/`ProductProfile`, `ProductBrief`, and immutable
`ProductKnowledgeSnapshot` records. Structured Audience values and bounded lists use validated
JSONB, while money uses exact numeric types. See `docs/12_PRODUCT_BRAIN.md`.

### Brand
- id
- tenant_id
- name
- voice
- visual_guidelines
- prohibited_claims
- allowed_claims

### Product
- id
- tenant_id
- brand_id
- sku
- name
- description
- category
- features
- benefits
- materials
- price
- margin_data
- audience
- problems_solved
- shipping_info
- seasonality
- status

### Asset
- id
- tenant_id
- product_id nullable
- asset_type
- object_storage_key
- mime_type
- dimensions/duration
- source
- rights_metadata
- checksum
- created_at

## Research

### ResearchSnapshot
- id
- tenant_id
- product_id nullable
- topic
- scope
- created_at
- valid_until
- confidence
- status

### ResearchEvidence
- id
- snapshot_id
- source_url/source_ref
- source_type
- captured_at
- extracted_claim
- evidence_strength

## Creative

### CreativeConcept
- id
- tenant_id
- product_id
- research_snapshot_id
- agent_run_id
- format
- hook
- narrative
- script
- scenes
- CTA
- audience
- hypothesis
- success_metric
- status

### CreativeVariant
- id
- concept_id
- variant_dimensions
- status

## Production

### ProductionPlan
- id
- concept_id
- required_assets
- missing_assets
- provider_strategy
- status

### GeneratedAsset
- id
- production_plan_id
- asset_id
- provider
- provider_job_id
- generation_parameters
- parent_asset_ids
- qa_status

## Marketing

### Experiment
- id
- tenant_id
- product_id
- objective
- hypothesis
- start/end
- status

### Publication
- id
- experiment_id
- creative_asset_id
- channel
- account_id
- caption_version
- CTA
- destination_url
- utm_source
- utm_campaign
- utm_content
- scheduled_at
- published_at
- external_post_id
- status

### MetricObservation
- id
- tenant_id
- publication_id
- metric_name
- value
- measured_at
- window

## Commerce

### Order
- id
- tenant_id
- provider
- external_order_id
- status
- placed_at
- totals
- fulfillment_state

Customer PII should be isolated into dedicated tables/columns with stricter access policy.

### InventoryItem
- tenant_id
- sku
- on_hand
- allocated
- available
- production_required

## Intelligence

### Insight
- id
- tenant_id
- product_id nullable
- statement
- insight_type
- evidence_refs
- sample_size
- metric_delta
- confidence
- scope
- provenance
- status
- created_at
- valid_until
- invalidated_at
- invalidation_reason

## Audit / Events

### DomainEvent
- event_id
- event_type / schema_version
- scope_kind (`tenant` or `platform`)
- tenant_id
- aggregate_type
- aggregate_id
- occurred_at
- actor_kind
- actor_id
- agent_definition_id / agent_version_id / agent_run_id optional
- correlation_id
- causation_id
- payload
- payload_schema_digest
- event_digest
- canonicalization_version

### OutboxEvent delivery metadata
- bounded `traceparent` / `tracestate` captured at creation, excluded from semantic event digest
- publication_state (`PENDING`, `PUBLISHING`, `PUBLISHED`, `FAILED_TERMINAL`)
- attempt_count / next_attempt_at
- lease_owner / lease_expires_at
- published_at
- last_error_code / last_error_digest
- created_at / updated_at

The immutable DomainEvent columns and mutable delivery metadata share
`event_delivery.outbox_events`. Database triggers prevent all envelope changes. Tenant runtime may
insert only tenant-matching facts and cannot read, claim, update, or delete Outbox history. The
dedicated publisher can read across tenants and update only delivery columns.

### InboxReceipt
- consumer_name / event_id (composite primary key)
- event_digest / event_type
- scope_kind / tenant_id
- handler_version
- processed_at

Inbox receipts are immutable tenant safety state. Handler version is not part of deduplication.
Retention must not remove receipts before the maximum supported replay window.

### AuditRecord
- id
- scope_kind (`platform` or `tenant`)
- tenant_id (null only for platform scope)
- actor_kind
- actor_id
- action
- resource_type optional
- resource_id optional
- outcome (`success`, `denied`, `failed`, or `error`)
- reason_code optional
- correlation_id
- causation_id optional
- occurred_at
- environment
- policy/tool/agent/run references optional
- before_digest optional
- after_digest optional
- safe_metadata
- audit_schema_version

Security audit is stored in `audit.audit_records`, separately from domain events, logs, and
telemetry. Runtime code can append but cannot read or mutate audit history. Tenant records are
protected by forced RLS and must match transaction-local tenant context; platform records cannot
carry a tenant. Record UUIDs and timestamps provide identity and evidence time, not global event
ordering. Safe metadata is recursively redacted, size bounded, and revalidated immediately before
persistence. Canonical SHA-256 digests provide compact state evidence without storing full state.

## Cross-Cutting Fields

Most tenant-owned entities should include:

- `tenant_id`
- `created_at`
- `updated_at`
- optional `created_by`
- version/revision where concurrent updates matter

Tenant-owned relationships must not reference a resource belonging to another tenant. Where practical, tenant-owned tables expose a unique `(tenant_id, id)` key and tenant-owned foreign keys include both columns. Application authorization remains mandatory; database constraints and RLS provide defense in depth.

Database timestamps use timezone-aware UTC values. Transaction boundaries follow application use cases. State changes and their cross-context domain events are committed atomically through a transactional outbox.

## ToolCall

`tool_execution.tool_calls` represents one logical Tool operation. It stores the immutable action
binding (tenant, requested/resolved Agent version, Tool version, Permission version, scopes,
resource, environment, normalized-input digest, operation ID, and action digest) plus controlled
lifecycle fields. The operation is unique per tenant, Tool definition, and operation ID. Runtime
access is tenant-scoped with forced RLS; binding mutation and invalid lifecycle transitions are
rejected by a database trigger. Audit records have a nullable indexed `tool_call_id` linkage.
