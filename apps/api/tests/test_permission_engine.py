# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from creative_marketer.agent_governance.domain import (
    AgentUnavailable,
    AgentVersionConfiguration,
    BudgetPeriod,
    ModelPolicy,
    PeriodBudgetPolicy,
    ResolutionProvenance,
    ResolvedAgentVersion,
    RunBudgetPolicy,
)
from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.permission_governance.application import (
    CreateToolPermission,
    EvaluateToolPermission,
)
from creative_marketer.permission_governance.domain import (
    ApprovalBehavior,
    Decision,
    DecisionReason,
    Obligation,
    PermissionEffect,
    PermissionEngine,
    PermissionGovernanceConflict,
    ResolvedToolPermission,
    ScopeAccess,
    ScopeRequirement,
    ToolPermission,
    ToolPermissionActivation,
    ToolPermissionVersion,
    ToolPermissionVersionConfiguration,
    TrustedScopeRequirements,
)
from creative_marketer.tool_governance.domain import (
    CredentialBoundary,
    ExecutionClass,
    IdempotencyRequirement,
    ResolvedToolVersion,
    RiskLevel,
    SideEffectClass,
    ToolContractSchema,
    ToolUnavailable,
    canonical_json,
    sha256_digest,
)


def context(*, tenant_id=None, actor_kind=ActorKind.AGENT) -> ExecutionContext:
    user_id = uuid4()
    actor_id = user_id if actor_kind is ActorKind.USER else uuid4()
    return ExecutionContext(
        tenant_id=tenant_id or uuid4(),
        actor=Actor(actor_kind, actor_id),
        user_id=user_id,
        membership_role=MembershipRole.MEMBER,
        membership_status=MembershipStatus.ACTIVE,
        environment="test",
        authentication=AuthenticationAssurance(datetime.now(UTC), "oidc", "mfa"),
    )


def agent(ctx: ExecutionContext, *, tool_key="demo.catalog.read") -> ResolvedAgentVersion:
    configuration = AgentVersionConfiguration(
        display_name="Researcher",
        mission="Read catalog data.",
        responsibilities=("Read products",),
        system_instructions="Use only explicitly allowed tools.",
        prompt_revision="researcher.v1",
        model_policy=ModelPolicy("standard", ("structured.output",), 3),
        run_budget_policy=RunBudgetPolicy(3, 3, 1000, Decimal("1"), "USD"),
        period_budget_policy=PeriodBudgetPolicy(BudgetPeriod.MONTHLY, 50, Decimal("20"), "USD"),
        read_scopes=("catalog.product",),
        write_scopes=("catalog.draft",),
        memory_scopes=(),
        allowed_tool_keys=(tool_key,),
        denied_tool_keys=(),
        approval_policy_key="agent.standard",
    )
    requested = uuid4()
    return ResolvedAgentVersion(
        requested_tenant_definition_id=requested,
        resolved_definition_id=requested,
        version_id=uuid4(),
        version_number=1,
        tenant_id=ctx.tenant_id,
        agent_key="researcher",
        agent_type="researcher",
        provenance=ResolutionProvenance.TENANT,
        configuration_digest=configuration.configuration_digest,
        configuration=configuration,
    )


def tool(*, risk=RiskLevel.R1, tool_key="demo.catalog.read") -> ResolvedToolVersion:
    document = {"type": "object"}
    schema = ToolContractSchema(canonical_json(document), sha256_digest(document))
    return ResolvedToolVersion(
        definition_id=uuid4(),
        version_id=uuid4(),
        version_number=1,
        tool_key=tool_key,
        risk_level=risk,
        side_effect_class=SideEffectClass.READ_ONLY,
        execution_class=ExecutionClass.INTERNAL,
        credential_boundary=CredentialBoundary.NONE,
        idempotency_requirement=IdempotencyRequirement.NOT_APPLICABLE,
        input_schema=schema,
        output_schema=schema,
        capability_tags=(),
        configuration_digest=sha256_digest({"tool": tool_key, "risk": risk.value}),
    )


def policy(
    ctx: ExecutionContext,
    resolved_agent: ResolvedAgentVersion,
    resolved_tool: ResolvedToolVersion,
    **changes,
) -> ResolvedToolPermission:
    config = ToolPermissionVersionConfiguration(
        effect=PermissionEffect.GRANT,
        allowed_scopes=("catalog.product", "catalog.draft"),
        allowed_environments=("test",),
    )
    config = replace(config, **changes)
    return ResolvedToolPermission(
        permission_id=uuid4(),
        permission_version_id=uuid4(),
        version_number=1,
        tenant_id=ctx.tenant_id,
        agent_definition_id=resolved_agent.requested_tenant_definition_id,
        tool_definition_id=resolved_tool.definition_id,
        configuration_digest=config.configuration_digest,
        configuration=config,
    )


READ = TrustedScopeRequirements((ScopeRequirement("catalog.product", ScopeAccess.READ),))


@pytest.mark.parametrize(
    ("risk", "expected"),
    [
        (RiskLevel.R0, Decision.ALLOW),
        (RiskLevel.R1, Decision.ALLOW),
        (RiskLevel.R2, Decision.ALLOW),
        (RiskLevel.R3, Decision.ALLOW),
        (RiskLevel.R4, Decision.REQUIRES_APPROVAL),
        (RiskLevel.R5, Decision.REQUIRES_APPROVAL),
        (RiskLevel.R6, Decision.REQUIRES_APPROVAL),
        (RiskLevel.R7, Decision.DENY),
    ],
)
def test_risk_decision_table(risk: RiskLevel, expected: Decision) -> None:
    ctx = context()
    resolved_agent, resolved_tool = agent(ctx), tool(risk=risk)
    decision = PermissionEngine().evaluate(
        ctx, resolved_agent, resolved_tool, policy(ctx, resolved_agent, resolved_tool), READ
    )
    assert decision.decision is expected
    if expected is Decision.REQUIRES_APPROVAL:
        assert Obligation.REQUIRE_APPROVAL in decision.obligations
    if risk is RiskLevel.R7:
        assert decision.reason_code is DecisionReason.TOOL_RISK_FORBIDDEN


def test_positive_obligations_and_forced_approval() -> None:
    ctx = context()
    resolved_agent = agent(ctx)
    resolved_tool = replace(
        tool(),
        execution_class=ExecutionClass.PROVIDER,
        credential_boundary=CredentialBoundary.CONNECTOR,
        idempotency_requirement=IdempotencyRequirement.REQUIRED,
    )
    decision = PermissionEngine().evaluate(
        ctx,
        resolved_agent,
        resolved_tool,
        policy(ctx, resolved_agent, resolved_tool, approval_behavior=ApprovalBehavior.ALWAYS),
        READ,
    )
    assert decision.decision is Decision.REQUIRES_APPROVAL
    assert decision.reason_code is DecisionReason.APPROVAL_FORCED_BY_POLICY
    assert set(decision.obligations) == set(Obligation)


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        (
            lambda a, t, p, c: (
                replace(
                    a,
                    configuration=replace(
                        a.configuration, denied_tool_keys=(t.tool_key,), allowed_tool_keys=()
                    ),
                ),
                t,
                p,
                c,
            ),
            DecisionReason.AGENT_TOOL_EXPLICITLY_DENIED,
        ),
        (
            lambda a, t, p, c: (
                replace(
                    a, configuration=replace(a.configuration, allowed_tool_keys=("other.tool",))
                ),
                t,
                p,
                c,
            ),
            DecisionReason.AGENT_TOOL_NOT_DECLARED,
        ),
        (
            lambda a, t, p, c: (
                a,
                t,
                replace(p, configuration=replace(p.configuration, effect=PermissionEffect.DENY)),
                c,
            ),
            DecisionReason.PERMISSION_EXPLICITLY_DENIED,
        ),
        (
            lambda a, t, p, c: (
                a,
                t,
                replace(
                    p, configuration=replace(p.configuration, allowed_environments=("production",))
                ),
                c,
            ),
            DecisionReason.ENVIRONMENT_NOT_ALLOWED,
        ),
        (
            lambda a, t, p, c: (
                replace(a, configuration=replace(a.configuration, read_scopes=())),
                t,
                p,
                c,
            ),
            DecisionReason.AGENT_SCOPE_MISSING,
        ),
        (
            lambda a, t, p, c: (
                a,
                t,
                replace(
                    p, configuration=replace(p.configuration, allowed_scopes=("catalog.draft",))
                ),
                c,
            ),
            DecisionReason.PERMISSION_SCOPE_MISSING,
        ),
        (
            lambda a, t, p, c: (a, t, replace(p, tenant_id=uuid4()), c),
            DecisionReason.TENANT_MISMATCH,
        ),
        (
            lambda a, t, p, c: (a, t, replace(p, tool_definition_id=uuid4()), c),
            DecisionReason.SECURITY_CONTEXT_INVALID,
        ),
    ],
)
def test_negative_decision_precedence(mutator, reason: DecisionReason) -> None:
    ctx = context()
    resolved_agent, resolved_tool = agent(ctx), tool()
    resolved_policy = policy(ctx, resolved_agent, resolved_tool)
    resolved_agent, resolved_tool, resolved_policy, ctx = mutator(
        resolved_agent, resolved_tool, resolved_policy, ctx
    )
    decision = PermissionEngine().evaluate(
        ctx, resolved_agent, resolved_tool, resolved_policy, READ
    )
    assert decision.decision is Decision.DENY
    assert decision.reason_code is reason
    assert decision.obligations == ()


def test_write_scope_and_platform_template_use_requested_tenant_policy() -> None:
    ctx = context()
    resolved_agent = agent(ctx)
    template_id = uuid4()
    resolved_agent = replace(
        resolved_agent,
        resolved_definition_id=template_id,
        provenance=ResolutionProvenance.PLATFORM_TEMPLATE,
    )
    resolved_tool = tool()
    resolved_policy = policy(ctx, resolved_agent, resolved_tool)
    write = TrustedScopeRequirements((ScopeRequirement("catalog.draft", ScopeAccess.WRITE),))
    assert (
        PermissionEngine()
        .evaluate(ctx, resolved_agent, resolved_tool, resolved_policy, write)
        .decision
        is Decision.ALLOW
    )
    assert resolved_policy.agent_definition_id != template_id


def test_scope_contract_is_canonical_immutable_and_never_implicitly_empty() -> None:
    with pytest.raises(ValueError, match="explicitly"):
        TrustedScopeRequirements()
    unscoped = TrustedScopeRequirements(explicitly_unscoped=True)
    assert unscoped.digest.startswith("sha256:")
    with pytest.raises(ValueError):
        TrustedScopeRequirements(READ.requirements, explicitly_unscoped=True)
    with pytest.raises(ValueError):
        ScopeRequirement("tenant.*", ScopeAccess.READ)
    with pytest.raises(ValueError):
        ScopeRequirement("catalog.product", ScopeAccess.READ, resource_type="product")
    resource = ScopeRequirement("catalog.product", ScopeAccess.READ, "product", "p-1")
    assert resource.resource_id == "p-1"
    with pytest.raises(FrozenInstanceError):
        resource.resource_id = "p-2"  # type: ignore[misc]


def test_policy_configuration_digest_is_reproducible_and_validated() -> None:
    first = ToolPermissionVersionConfiguration(
        PermissionEffect.GRANT, ("catalog.product", "catalog.draft"), ("test", "staging")
    )
    reordered = ToolPermissionVersionConfiguration(
        PermissionEffect.GRANT,
        tuple(reversed(first.allowed_scopes)),
        tuple(reversed(first.allowed_environments)),
    )
    assert first == reordered
    assert first.configuration_digest == reordered.configuration_digest
    for invalid in [
        lambda: ToolPermissionVersionConfiguration(PermissionEffect.GRANT, (), ()),
        lambda: ToolPermissionVersionConfiguration(PermissionEffect.GRANT, (), ("local",)),
        lambda: ToolPermissionVersionConfiguration(PermissionEffect.GRANT, ("*",), ("test",)),
    ]:
        with pytest.raises(ValueError):
            invalid()


def test_policy_entities_require_trusted_user_provenance() -> None:
    with pytest.raises(ValueError, match="trusted user"):
        ToolPermission(uuid4(), uuid4(), uuid4(), "agent", uuid4())
    with pytest.raises(ValueError, match="trusted user"):
        ToolPermissionActivation(uuid4(), uuid4(), uuid4(), "agent", uuid4())


def test_decision_semantics_are_reproducible() -> None:
    ctx = context()
    resolved_agent, resolved_tool = agent(ctx), tool()
    resolved_policy = policy(ctx, resolved_agent, resolved_tool)
    first = PermissionEngine().evaluate(ctx, resolved_agent, resolved_tool, resolved_policy, READ)
    second = PermissionEngine().evaluate(ctx, resolved_agent, resolved_tool, resolved_policy, READ)
    assert (
        replace(first, decision_id=second.decision_id, evaluated_at=second.evaluated_at) == second
    )


class Audit:
    def __init__(self, fail=False):
        self.records, self.fail = [], fail

    async def append(self, record):
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.records.append(record)


class Permissions:
    def __init__(self, permission):
        self.permission = permission

    async def get_for_subject(self, agent_definition_id, tool_definition_id):
        return self.permission


class Versions:
    def __init__(self, version):
        self.version = version

    async def get(self, version_id):
        return self.version


class Activations:
    def __init__(self, activation):
        self.activation = activation

    async def get(self, permission_id):
        return self.activation


class Uow:
    def __init__(self, permission=None, version=None, activation=None, audit_fail=False):
        self.permissions, self.versions, self.activations = (
            Permissions(permission),
            Versions(version),
            Activations(activation),
        )
        self.audit, self.committed = Audit(audit_fail), False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def commit(self):
        self.committed = True


def orchestration(
    ctx,
    resolved_agent,
    resolved_tool,
    *,
    missing=False,
    audit_fail=False,
    agent_error=False,
    tool_error=False,
):
    permission = ToolPermission(
        ctx.tenant_id,
        resolved_agent.requested_tenant_definition_id,
        resolved_tool.definition_id,
        "user",
        ctx.user_id,
    )
    config = ToolPermissionVersionConfiguration(
        PermissionEffect.GRANT, ("catalog.product",), ("test",)
    )
    version = ToolPermissionVersion(permission.id, ctx.tenant_id, 1, config, "user", ctx.user_id)
    activation = type("Activation", (), {"active_version_id": version.id})()
    uow = Uow(None if missing else permission, version, activation, audit_fail)

    async def resolve_agent(context, definition_id):
        if agent_error:
            raise AgentUnavailable("no")
        return resolved_agent

    async def resolve_tool(tool_key):
        if tool_error:
            raise ToolUnavailable("no")
        return resolved_tool

    return EvaluateToolPermission(lambda tenant: uow, resolve_agent, resolve_tool), uow


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, DecisionReason.ALLOWED),
        ({"missing": True}, DecisionReason.PERMISSION_MISSING),
        ({"agent_error": True}, DecisionReason.AGENT_UNAVAILABLE),
        ({"tool_error": True}, DecisionReason.TOOL_UNAVAILABLE),
    ],
)
async def test_application_resolves_denies_and_audits(options, expected) -> None:
    ctx = context()
    resolved_agent, resolved_tool = agent(ctx), tool()
    service, uow = orchestration(ctx, resolved_agent, resolved_tool, **options)
    decision = await service(
        ctx, resolved_agent.requested_tenant_definition_id, resolved_tool.tool_key, READ
    )
    assert decision.reason_code is expected
    assert uow.committed
    assert len(uow.audit.records) == 1
    assert uow.audit.records[0].reason_code == expected.value


@pytest.mark.asyncio
async def test_positive_audit_failure_fails_closed_and_denial_stays_denial() -> None:
    ctx = context()
    resolved_agent, resolved_tool = agent(ctx), tool()
    service, _ = orchestration(ctx, resolved_agent, resolved_tool, audit_fail=True)
    denied = await service(
        ctx, resolved_agent.requested_tenant_definition_id, resolved_tool.tool_key, READ
    )
    assert denied.decision is Decision.DENY
    assert denied.reason_code is DecisionReason.AUDIT_FAILURE
    service, _ = orchestration(ctx, resolved_agent, resolved_tool, missing=True, audit_fail=True)
    denied = await service(
        ctx, resolved_agent.requested_tenant_definition_id, resolved_tool.tool_key, READ
    )
    assert denied.reason_code is DecisionReason.PERMISSION_MISSING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_context",
    [
        context(actor_kind=ActorKind.AGENT),
        replace(context(actor_kind=ActorKind.USER), membership_role=MembershipRole.MEMBER),
        replace(context(actor_kind=ActorKind.USER), membership_status=MembershipStatus.INACTIVE),
    ],
)
async def test_policy_mutation_rejects_non_admin_and_agent_self_escalation(
    bad_context: ExecutionContext,
) -> None:
    class NeverFactory:
        def __call__(self, tenant):
            raise AssertionError("unauthorized mutation must fail before persistence")

    with pytest.raises(PermissionGovernanceConflict, match="owner or admin"):
        await CreateToolPermission(NeverFactory())(bad_context, uuid4(), uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["tool_key", "agent_version"])
async def test_application_denies_resolver_identity_tampering(tamper: str) -> None:
    ctx = context()
    resolved_agent, resolved_tool = agent(ctx), tool()
    requested_agent_id = resolved_agent.requested_tenant_definition_id
    requested_tool_key = resolved_tool.tool_key
    if tamper == "tool_key":
        resolved_tool = replace(resolved_tool, tool_key="tampered.tool")
    else:
        resolved_agent = replace(resolved_agent, requested_tenant_definition_id=uuid4())
    service, uow = orchestration(ctx, resolved_agent, resolved_tool)
    decision = await service(ctx, requested_agent_id, requested_tool_key, READ)
    assert decision.decision is Decision.DENY
    assert decision.reason_code is DecisionReason.SECURITY_CONTEXT_INVALID
    assert uow.audit.records[0].reason_code == DecisionReason.SECURITY_CONTEXT_INVALID.value
