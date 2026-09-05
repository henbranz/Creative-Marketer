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

## TASK-003 implementation notes

The implemented human-request flow is:

```text
Credential → AuthenticationPort → AuthenticatedPrincipal
→ ExternalIdentity → User → untrusted TenantSelector
→ Membership and Tenant verification → immutable ExecutionContext
→ TenantContext → transaction-scoped UnitOfWork/RLS
```

`ExternalIdentity` is a platform-scoped entity keyed only by the exact, case-sensitive `(issuer, opaque subject)` pair. Email is profile/communication data, never an implicit identity-linking key. Provider roles, groups, organizations, tenant claims, and arbitrary claims do not enter the principal or become platform authorization.

The browser may select a tenant but cannot confer tenant authority. The resolver temporarily uses that selection to scope an RLS transaction, then constructs trusted context only after the external identity, User, Membership, and Tenant are all present and active. PostgreSQL settings remain downstream context carriers rather than authentication credentials.

The development authenticator is configuration-gated to development/test and consumes a synthetic `issuer|subject` credential. Staging and production reject that configuration; without a production adapter, authentication fails closed. Production OIDC/vendor selection, account linking policy, JIT provisioning, workload credential verification, and Agent Runtime identity injection remain deferred.

`ActorKind` reserves workload, system, and agent identities, while TASK-003 constructs only authenticated User actors. Agent definition/version/run identifiers will be injected later by the trusted Agent Runtime and are never accepted from browser, prompt, model output, tool arguments, or arbitrary internal JSON.
