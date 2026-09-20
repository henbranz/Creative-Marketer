from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from creative_marketer.agent_governance.application import ResolveActiveAgentVersion
from creative_marketer.infrastructure.database.agent_governance_uow import (
    SqlAlchemyAgentRegistryUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.permission_governance_uow import (
    SqlAlchemyPermissionUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.tool_execution_uow import (
    SqlAlchemyGatewayUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.tool_governance_uow import (
    SqlAlchemyToolRegistryUnitOfWorkFactory,
)
from creative_marketer.permission_governance.application import EvaluateToolPermission
from creative_marketer.publishing.execution import (
    PublicationExecutionAuthority,
    social_tool_bindings,
)
from creative_marketer.tool_execution.application import (
    ToolExecutionBindingRegistry,
    ToolGateway,
)
from creative_marketer.tool_governance.application import ResolveActiveTool


@dataclass(slots=True)
class PublishingGatewayFactory:
    sessions: async_sessionmaker[AsyncSession]
    authority: PublicationExecutionAuthority
    _gateway: ToolGateway | None = field(default=None, init=False)

    async def __call__(self) -> ToolGateway:
        if self._gateway is not None:
            return self._gateway
        tool_factory = SqlAlchemyToolRegistryUnitOfWorkFactory(self.sessions)
        tool_resolver = ResolveActiveTool(tool_factory)
        tools = {
            key: await tool_resolver(key)
            for key in (
                "social.publish.submit",
                "social.publish.status",
                "social.publish.cancel",
            )
        }
        agent_resolver = ResolveActiveAgentVersion(
            SqlAlchemyAgentRegistryUnitOfWorkFactory(self.sessions)
        )
        permission_factory = SqlAlchemyPermissionUnitOfWorkFactory(self.sessions)
        self._gateway = ToolGateway(
            agent_resolver,
            tool_resolver,
            EvaluateToolPermission(permission_factory, agent_resolver, tool_resolver),
            ToolExecutionBindingRegistry(social_tool_bindings(self.authority, tools)),
            SqlAlchemyGatewayUnitOfWorkFactory(self.sessions),
        )
        return self._gateway
