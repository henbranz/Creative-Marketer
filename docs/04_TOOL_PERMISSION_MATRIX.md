# 04 — Tool & Permission Matrix

## Principle

Tools are capabilities granted to agents through policy. Possessing a tool definition does not imply permission to execute it.

## Phase 0 Tool Registry

The platform-global Tool Registry separates stable identity, immutable contract, and mutable
activation:

```text
ToolDefinition → ToolVersion[] → ToolActivation → ResolvedToolVersion
```

`ToolDefinition` reserves one provider-neutral, lowercase, dot-separated capability key.
`ToolVersion` snapshots JSON Schema 2020-12 input/output contracts and explicit risk,
side-effect, execution, credential-boundary, idempotency, and capability-tag declarations.
`ToolActivation` selects one immutable version and may point back to a historical version for
rollback. Disabled or archived definitions and definitions without activation do not resolve.

These layers remain deliberately separate:

```text
AgentVersion.allowed_tool_keys  declaration only
ToolDefinition                  known capability only
ToolPermission                  tenant policy with immutable revisions and activation
Permission Engine               authoritative deterministic decision
```

Unknown declarations remain `unknown`; they are never auto-created or treated as authorized.
Diagnostic reconciliation classifies allowed declarations as known-active, known-unavailable, or
unknown and preserves denied declarations, but produces no allow/deny decision.

The Tool Registry is not the Tool Gateway, and a ToolVersion is not an implementation adapter.
No callable, SDK type, endpoint, credential, routing decision, provider request, or execution
record belongs in the Registry.

### Contract classifications

- risk: `R0` through `R7`, stored as policy input only
- side effect: `READ_ONLY`, `INTERNAL_MUTATION`, or `EXTERNAL_MUTATION`
- execution: provider-neutral `INTERNAL`, `CONNECTOR`, or `PROVIDER`
- credential boundary: `NONE` or `CONNECTOR`; agents never receive credentials
- idempotency: `NOT_APPLICABLE`, `SUPPORTED`, or `REQUIRED`; enforcement is deferred

Input and output schemas are self-contained JSON Schema 2020-12 object contracts. Remote and
relative `$ref` values are rejected; local `#` references are allowed. Registration bounds schema
size, nesting, and node count and rejects credential-shaped values in defaults, examples, or other
schema content. Contract changes always create a new version.

## Baseline Matrix

| Capability | Orchestrator | Researcher | Creative | Producer | Marketer | Commerce | Intelligence |
|---|---:|---:|---:|---:|---:|---:|---:|
| Product metadata read | Scoped | Yes | Yes | Yes | Yes | Yes | Yes |
| Customer PII read | No | No | No | No | No | Scoped | Prefer aggregated |
| Web research | Request | Yes | No* | No | No* | No | No* |
| Research snapshot read | Yes | Yes | Yes | Yes | Yes | No/limited | Yes |
| Creative concept write | No | No | Yes | No | No | No | No |
| Asset library read | Limited | Limited | Yes | Yes | Yes | Limited | Metadata |
| Generate image/video | No | No | No | Yes | No | No | No |
| Social draft creation | No | No | No | No | Yes | No | No |
| Social publish | No | No | No | No | Policy | No | No |
| Social metrics read | No | Limited | Yes | Yes | Yes | No | Yes |
| Shopify order read | No | No | No | No | Aggregated only | Yes | Aggregated |
| Inventory read | Limited | No | Yes when relevant | Yes when relevant | Yes when relevant | Yes | Yes |
| Inventory mutation | No | No | No | No | No | Approval/policy | No |
| Refund/payment mutation | No | No | No | No | No | Explicit approval | No |
| Secrets | Never | Never | Never | Never | Never | Never | Never |
| Permission/admin mutation | Never | Never | Never | Never | Never | Never | Never |

`*` Prefer consuming Researcher outputs rather than duplicating open-web access.

## Data Scopes

Examples:

```text
tenant:{tenant_id}:product:{product_id}:read
tenant:{tenant_id}:creative:write
tenant:{tenant_id}:social:instagram:publish
tenant:{tenant_id}:commerce:orders:read
tenant:{tenant_id}:commerce:refund:request
```

No wildcard privileges for ordinary agents in production.

## Risk Policy

| Risk | Example | Default |
|---|---|---|
| R0 | read product metadata | automatic |
| R1 | generate insight candidate | automatic |
| R2 | generate video within budget | automatic or budget gated |
| R3 | draft Instagram post | automatic |
| R4 | publish Instagram/TikTok post | approval required in Phase 0 |
| R5 | change inventory/fulfillment state | approval required in Phase 0 |
| R6 | refund/payment-affecting action | explicit approval |
| R7 | credentials/permissions/admin | always denied for Agent execution |

The Phase-0 tenant policy may force approval for R0–R3 but cannot suppress the R4–R6 approval
baseline. This conservative rule can be revisited only with the Approval Engine and a separately
reviewed autonomy design.

Tool scope requirements are constructed by trusted application or future Tool Gateway code. Model
output, browser input, and ordinary tool arguments are never authoritative for required scopes.
An explicitly trusted unscoped marker is required for tools that genuinely need no resource scope.

## Tool Gateway Decision Inputs

A decision should consider:

- tenant
- authenticated actor
- agent identity/version
- tool
- resource
- action
- risk
- approval policy
- budget
- rate limit
- current workflow
- duplicate/idempotency state
- environment
- feature flags

Tenant, actor, agent-version, and run identity are supplied from trusted runtime state and are not accepted from prompts, model output, tool arguments, or browser-controlled fields. Risk level is an input to policy, not a complete authorization decision by itself.

## Deny by Default

Unknown agent, unknown tool, unknown resource scope, malformed input, missing approval, or ambiguous tenant must result in denial.

## Gateway enforcement order

The implemented order is: trusted context → active Agent resolution → active Tool resolution →
exact executor binding → strict input validation and canonical normalization → trusted resource
ownership/scope derivation → deterministic permission evaluation → obligation enforcement →
immutable ToolCall/approval binding → final locked authorization snapshot → idempotency attempt →
executor → strict output validation → atomic outcome evidence. Missing budget or credential
capability, unsupported obligations, stale versions, and unavailable exact executors fail closed.
