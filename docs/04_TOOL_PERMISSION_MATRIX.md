# 04 — Tool & Permission Matrix

## Principle

Tools are capabilities granted to agents through policy. Possessing a tool definition does not imply permission to execute it.

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
| R4 | publish Instagram/TikTok post | tenant configurable |
| R5 | change inventory/fulfillment state | approval or strict policy |
| R6 | refund/payment-affecting action | explicit approval |
| R7 | credentials/permissions/admin | user/admin only |

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
