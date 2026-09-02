# ADR-011 — Authentication and Authoritative Actor/Agent Identity

## Status
Accepted

## Decision
Authentication remains behind a vendor-neutral identity interface during Phase 0. External identities are keyed by issuer and subject; the platform resolves users, memberships, tenant access, roles, and policy from its own authoritative records.

Each request or job receives an immutable trusted runtime context containing the tenant, authenticated actor, agent definition/version when applicable, run identity, environment, correlation ID, and authentication assurance metadata needed by policy.

Identity supplied by prompts, model output, tool arguments, browser-controlled fields, or unverified headers is never authoritative. The platform injects agent/run identity after validating the active version and execution authorization. Service-to-service and worker calls require authenticated workload identity appropriate to the deployment environment.

## Alternatives

- trust tenant/user identifiers supplied by the browser or model
- couple domain/application modules directly to one authentication vendor
- let an orchestrator or agent assert its own identity and scopes

## Rationale
Authentication proves an external or workload identity; platform authorization determines what that identity may do in a tenant. Keeping those concerns separate prevents vendor claims or agent-generated input from becoming ambient authority.

## Tradeoffs
A vendor-neutral boundary adds mapping and lifecycle logic. Some provider-specific features require adapter metadata. Workload identity and step-up authentication depend on the later deployment and identity-provider choices.

## Consequences

- no final authentication vendor is selected by this ADR
- membership changes take effect through deterministic policy, not prompt context
- platform support/elevated access must use a separate audited path
- downstream services receive trusted runtime context, not raw browser claims
- tests cover forged tenant, actor, agent-version, and run identity
