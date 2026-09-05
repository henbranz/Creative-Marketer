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
- agent_type
- display_name
- mission
- status

Ownership constraints:

- `scope_kind = platform` requires `tenant_id IS NULL`
- `scope_kind = tenant` requires `tenant_id IS NOT NULL`
- tenant resolution references a platform template explicitly; nullable ownership never creates implicit fallback

### AgentVersion
- id
- agent_definition_id
- prompt_version
- model_policy
- tool_policy_version
- memory_policy_version
- schema_version
- rollout_status

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
- name
- version
- input_schema
- output_schema
- risk_level
- side_effect_type

### ToolPermission
- tenant_id
- agent_definition_id
- tool_id
- scope
- policy

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
- requested_by_agent_run_id
- action_type
- risk_level
- payload_hash
- canonicalization_version
- tool_id and tool_version
- resource_type and resource_id
- environment
- policy_version
- idempotency_key
- status
- expires_at
- decided_by
- decided_at

## Product Digital Twin

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
- tenant_id
- event_type
- schema_version
- aggregate_type
- aggregate_id
- occurred_at
- actor_type
- actor_id
- correlation_id
- causation_id
- payload

### AuditRecord
- id
- tenant_id
- actor_type
- actor_id
- action
- resource_type
- resource_id
- correlation_id
- before_hash
- after_hash
- timestamp
- metadata

## Cross-Cutting Fields

Most tenant-owned entities should include:

- `tenant_id`
- `created_at`
- `updated_at`
- optional `created_by`
- version/revision where concurrent updates matter

Tenant-owned relationships must not reference a resource belonging to another tenant. Where practical, tenant-owned tables expose a unique `(tenant_id, id)` key and tenant-owned foreign keys include both columns. Application authorization remains mandatory; database constraints and RLS provide defense in depth.

Database timestamps use timezone-aware UTC values. Transaction boundaries follow application use cases. State changes and their cross-context domain events are committed atomically through a transactional outbox.
