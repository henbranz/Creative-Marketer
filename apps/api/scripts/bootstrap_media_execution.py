"""Deterministic policy principal for governed media execution."""

import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from creative_marketer.agent_governance.application import (
    ActivateAgentVersion,
    CreateAgentVersion,
    CreateTenantAgentDefinition,
    ListTenantAgentDefinitions,
    ResolveActiveAgentVersion,
)
from creative_marketer.agent_governance.domain import (
    AgentUnavailable,
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
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.infrastructure.database.agent_governance_uow import (
    SqlAlchemyAgentRegistryUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.permission_governance_uow import (
    SqlAlchemyPermissionUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.tool_governance_uow import (
    SqlAlchemyToolRegistryUnitOfWorkFactory,
)
from creative_marketer.permission_governance.application import (
    ActivateToolPermissionVersion,
    CreateToolPermission,
    CreateToolPermissionVersion,
)
from creative_marketer.permission_governance.domain import (
    PermissionEffect,
    ToolPermissionVersionConfiguration,
)
from creative_marketer.tool_governance.application import ResolveActiveTool
from creative_marketer_api.config import Settings

MEDIA_TOOL_KEYS = (
    "media.image.generate",
    "media.video.generate.start",
    "media.video.generate.status",
    "media.video.generate.import",
)


def media_execution_configuration() -> AgentVersionConfiguration:
    """Return a zero-model workload principal; this is never routed to AgentRuntime."""
    return AgentVersionConfiguration(
        display_name="Media Production Execution Workload",
        mission="Execute only exact human-approved generation jobs through Tool Gateway.",
        responsibilities=(
            "Reload production authority from PostgreSQL",
            "Execute exact approved image and video generation jobs",
            "Reconcile known provider operations without blind resubmission",
        ),
        system_instructions=(
            "Deterministic workload policy principal. Never invoke a model. Never accept media "
            "specifications, routes, credentials, or provider references from Temporal. Reload "
            "PostgreSQL authority and use only immutable media Tool contracts."
        ),
        prompt_revision="media_production_execution_v1",
        model_policy=ModelPolicy("media_execution", ("text",), 1),
        run_budget_policy=RunBudgetPolicy(0, 20, 0, Decimal("0"), "USD"),
        period_budget_policy=PeriodBudgetPolicy(BudgetPeriod.DAILY, None, Decimal("0"), "USD"),
        read_scopes=(),
        write_scopes=("production.media",),
        memory_scopes=(),
        allowed_tool_keys=MEDIA_TOOL_KEYS,
        denied_tool_keys=(),
        approval_policy_key="production.plan_review",
    )


async def run() -> None:
    settings = Settings()
    if settings.app_env not in {"development", "test"}:
        raise SystemExit("Media execution bootstrap is forbidden outside development/test")
    try:
        tenant_id = UUID(os.environ["BOOTSTRAP_TENANT_ID"])
        user_id = UUID(os.environ["BOOTSTRAP_USER_ID"])
    except (KeyError, ValueError) as error:
        raise SystemExit("BOOTSTRAP_TENANT_ID and BOOTSTRAP_USER_ID must be UUIDs") from error
    context = ExecutionContext(
        tenant_id,
        Actor(ActorKind.USER, user_id),
        user_id,
        MembershipRole.OWNER,
        MembershipStatus.ACTIVE,
        settings.app_env,
        AuthenticationAssurance(datetime.now(UTC), "dev-bootstrap", "explicit"),
    )
    sessions = create_session_factory(str(settings.database_url))
    registry = SqlAlchemyAgentRegistryUnitOfWorkFactory(sessions)
    existing = [
        value
        for value in await ListTenantAgentDefinitions(registry)(context)
        if value.agent_type == "media_execution_workload"
    ]
    if len(existing) > 1:
        raise SystemExit("Multiple tenant media execution workload definitions exist")
    definition = (
        existing[0]
        if existing
        else await CreateTenantAgentDefinition(registry)(
            context,
            agent_key="media_execution_workload",
            agent_type="media_execution_workload",
        )
    )
    desired = media_execution_configuration()
    try:
        active = await ResolveActiveAgentVersion(registry)(context, definition.id)
    except AgentUnavailable:
        active = None
    if active is None or active.configuration_digest != desired.configuration_digest:
        version = await CreateAgentVersion(registry)(context, definition.id, desired)
        await ActivateAgentVersion(registry)(context, definition.id, version.id)

    tools = SqlAlchemyToolRegistryUnitOfWorkFactory(sessions)
    permissions = SqlAlchemyPermissionUnitOfWorkFactory(sessions)
    policy = ToolPermissionVersionConfiguration(
        PermissionEffect.GRANT, ("production.media",), (settings.app_env,)
    )
    for key in MEDIA_TOOL_KEYS:
        tool = await ResolveActiveTool(tools)(key)
        async with permissions(context.tenant_context()) as uow:
            permission = await uow.permissions.get_for_subject(definition.id, tool.definition_id)
        if permission is not None:
            continue
        permission = await CreateToolPermission(permissions)(
            context, definition.id, tool.definition_id
        )
        permission_version = await CreateToolPermissionVersion(permissions)(
            context, permission.id, policy
        )
        await ActivateToolPermissionVersion(permissions)(
            context, permission.id, permission_version.id
        )
    print(f"Media execution workload active: {definition.id}")


if __name__ == "__main__":
    asyncio.run(run())
