import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from creative_marketer.agent_governance.application import (
    ActivateAgentVersion,
    CreateAgentVersion,
    CreateTenantAgentDefinition,
    ResolveActiveAgentVersion,
)
from creative_marketer.agent_governance.domain import (
    AgentDefinition,
    AgentVersionConfiguration,
    BudgetPeriod,
    ModelPolicy,
    PeriodBudgetPolicy,
    RunBudgetPolicy,
)
from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.application.use_cases import CreateTenant
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.infrastructure.database.agent_governance_uow import (
    SqlAlchemyAgentRegistryUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.permission_governance_schema import (
    tool_permission_versions,
    tool_permissions,
)
from creative_marketer.infrastructure.database.permission_governance_uow import (
    SqlAlchemyPermissionUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.tool_governance_uow import (
    SqlAlchemyToolRegistryUnitOfWorkFactory,
)
from creative_marketer.permission_governance.application import (
    ActivateToolPermissionVersion,
    ChangeToolPermissionLifecycle,
    CreateToolPermission,
    CreateToolPermissionVersion,
    EvaluateToolPermission,
)
from creative_marketer.permission_governance.domain import (
    Decision,
    PermissionEffect,
    PermissionGovernanceConflict,
    PermissionStatus,
    ScopeAccess,
    ScopeRequirement,
    ToolPermissionVersionConfiguration,
    TrustedScopeRequirements,
)
from creative_marketer.tool_governance.application import (
    ActivateToolVersion,
    CreateToolDefinition,
    CreateToolVersion,
    PlatformControlContext,
    ResolveActiveTool,
)
from creative_marketer.tool_governance.domain import (
    CredentialBoundary,
    ExecutionClass,
    IdempotencyRequirement,
    RiskLevel,
    SideEffectClass,
    ToolDefinition,
    ToolVersionConfiguration,
)
from creative_marketer.tool_governance.schema_validation import validate_contract_schema
from tests.integration.support import IdentityStack


def execution_context(
    tenant_id: UUID, role: MembershipRole = MembershipRole.OWNER
) -> ExecutionContext:
    user_id = uuid4()
    return ExecutionContext(
        tenant_id,
        Actor(ActorKind.USER, user_id),
        user_id,
        role,
        MembershipStatus.ACTIVE,
        "test",
        AuthenticationAssurance(datetime.now(UTC), "test", "verified"),
        uuid4(),
    )


def agent_config() -> AgentVersionConfiguration:
    return AgentVersionConfiguration(
        "Researcher",
        "Read products.",
        ("Read catalog",),
        "Use declared tools only.",
        "research.v1",
        ModelPolicy("research", ("structured.output",), 3),
        RunBudgetPolicy(3, 3, 1000, Decimal("1"), "USD"),
        PeriodBudgetPolicy(BudgetPeriod.MONTHLY, 50, Decimal("20"), "USD"),
        ("catalog.product",),
        (),
        (),
        ("catalog.product.read",),
        (),
        "agent.standard",
    )


def tool_config() -> ToolVersionConfiguration:
    schema = validate_contract_schema(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    )
    return ToolVersionConfiguration(
        "Read product",
        "Reads one product.",
        RiskLevel.R1,
        SideEffectClass.READ_ONLY,
        ExecutionClass.INTERNAL,
        CredentialBoundary.NONE,
        IdempotencyRequirement.NOT_APPLICABLE,
        schema,
        schema,
    )


async def setup_subject(
    identity_stack: IdentityStack,
    agent_factory: SqlAlchemyAgentRegistryUnitOfWorkFactory,
    tool_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
) -> tuple[ExecutionContext, AgentDefinition, ToolDefinition]:
    tenant = await CreateTenant(identity_stack.uow_factory)("Permission", f"permission-{uuid4()}")
    ctx = execution_context(tenant.id)
    definition = await CreateTenantAgentDefinition(agent_factory)(
        ctx, agent_key="researcher", agent_type="researcher"
    )
    agent_version = await CreateAgentVersion(agent_factory)(ctx, definition.id, agent_config())
    await ActivateAgentVersion(agent_factory)(ctx, definition.id, agent_version.id)
    control = PlatformControlContext(ActorKind.SYSTEM, uuid4(), "test", uuid4())
    tool_definition = await CreateToolDefinition(tool_factory)(
        control, tool_key="catalog.product.read", category="catalog"
    )
    tool_version = await CreateToolVersion(tool_factory)(control, tool_definition.id, tool_config())
    await ActivateToolVersion(tool_factory)(control, tool_definition.id, tool_version.id)
    return ctx, definition, tool_definition


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_policy_versions_rollback_evaluate_and_audit(
    admin_engine: AsyncEngine,
    identity_stack: IdentityStack,
    agent_registry_factory: SqlAlchemyAgentRegistryUnitOfWorkFactory,
    tool_control_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
    tool_runtime_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
    permission_factory: SqlAlchemyPermissionUnitOfWorkFactory,
) -> None:
    ctx, agent_definition, tool_definition = await setup_subject(
        identity_stack, agent_registry_factory, tool_control_factory
    )
    permission = await CreateToolPermission(permission_factory)(
        ctx, agent_definition.id, tool_definition.id
    )
    grant = ToolPermissionVersionConfiguration(
        PermissionEffect.GRANT, ("catalog.product",), ("test",)
    )
    v1 = await CreateToolPermissionVersion(permission_factory)(ctx, permission.id, grant)
    deny = ToolPermissionVersionConfiguration(PermissionEffect.DENY, (), ("test",))
    v2 = await CreateToolPermissionVersion(permission_factory)(ctx, permission.id, deny)
    await ActivateToolPermissionVersion(permission_factory)(ctx, permission.id, v2.id)
    evaluator = EvaluateToolPermission(
        permission_factory,
        ResolveActiveAgentVersion(agent_registry_factory),
        ResolveActiveTool(tool_runtime_factory),
    )
    scopes = TrustedScopeRequirements((ScopeRequirement("catalog.product", ScopeAccess.READ),))
    denied = await evaluator(ctx, agent_definition.id, tool_definition.tool_key, scopes)
    assert denied.decision is Decision.DENY
    await ActivateToolPermissionVersion(permission_factory)(ctx, permission.id, v1.id)
    allowed = await evaluator(ctx, agent_definition.id, tool_definition.tool_key, scopes)
    assert allowed.decision is Decision.ALLOW
    disabled = await ChangeToolPermissionLifecycle(permission_factory)(
        ctx, permission.id, PermissionStatus.DISABLED
    )
    assert disabled.status is PermissionStatus.DISABLED
    denied = await evaluator(ctx, agent_definition.id, tool_definition.tool_key, scopes)
    assert denied.decision is Decision.DENY
    await ChangeToolPermissionLifecycle(permission_factory)(
        ctx, permission.id, PermissionStatus.ARCHIVED
    )
    async with admin_engine.connect() as connection:
        actions = (
            (
                await connection.execute(
                    text(
                        "SELECT action FROM audit.audit_records "
                        "WHERE action LIKE 'governance.permission.%'"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert "governance.permission.allowed" in actions
    assert "governance.permission.denied" in actions
    assert "governance.permission.version.activated" in actions


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_policy_authority_concurrency_same_tenant_fk_rls_and_privileges(
    admin_engine: AsyncEngine,
    runtime_engine: AsyncEngine,
    identity_stack: IdentityStack,
    agent_registry_factory: SqlAlchemyAgentRegistryUnitOfWorkFactory,
    tool_control_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
    permission_factory: SqlAlchemyPermissionUnitOfWorkFactory,
) -> None:
    ctx, agent_definition, tool_definition = await setup_subject(
        identity_stack, agent_registry_factory, tool_control_factory
    )
    member = execution_context(ctx.tenant_id, MembershipRole.MEMBER)
    with pytest.raises(PermissionGovernanceConflict):
        await CreateToolPermission(permission_factory)(
            member, agent_definition.id, tool_definition.id
        )
    permission = await CreateToolPermission(permission_factory)(
        ctx, agent_definition.id, tool_definition.id
    )
    config = ToolPermissionVersionConfiguration(PermissionEffect.GRANT, (), ("test",))
    first, second = await asyncio.gather(
        CreateToolPermissionVersion(permission_factory)(ctx, permission.id, config),
        CreateToolPermissionVersion(permission_factory)(ctx, permission.id, config),
    )
    assert {first.version_number, second.version_number} == {1, 2}

    other = await CreateTenant(identity_stack.uow_factory)("Other", f"other-{uuid4()}")
    other_ctx = execution_context(other.id)
    other_agent = await CreateTenantAgentDefinition(agent_registry_factory)(
        other_ctx, agent_key="researcher", agent_type="researcher"
    )
    async with admin_engine.begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                insert(tool_permissions).values(
                    id=uuid4(),
                    tenant_id=ctx.tenant_id,
                    agent_definition_id=other_agent.id,
                    tool_definition_id=tool_definition.id,
                    status="active",
                    created_by_actor_kind="user",
                    created_by_actor_id=ctx.user_id,
                )
            )
    async with runtime_engine.connect() as connection:
        transaction = await connection.begin()
        await connection.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant, true)"),
            {"tenant": str(other.id)},
        )
        assert (
            await connection.scalar(
                select(tool_permissions.c.id).where(tool_permissions.c.id == permission.id)
            )
            is None
        )
        with pytest.raises(ProgrammingError):
            await connection.execute(update(tool_permission_versions).values(effect="DENY"))
        await transaction.rollback()

    async with admin_engine.begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                update(tool_permissions)
                .where(tool_permissions.c.id == permission.id)
                .values(agent_definition_id=uuid4())
            )
        await connection.rollback()
