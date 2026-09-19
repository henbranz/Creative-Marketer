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
from creative_marketer.tool_execution.application import (
    ToolExecutionBindingRegistry,
    ToolGateway,
)
from creative_marketer.tool_governance.application import ResolveActiveTool

from .execution import CommerceExecutionAuthority, commerce_tool_bindings
from .provider import CommerceMutationProvider, FakeCommerceProvider


@dataclass(slots=True)
class CommerceGatewayFactory:
    sessions: async_sessionmaker[AsyncSession]
    authority: CommerceExecutionAuthority
    provider: CommerceMutationProvider
    _gateway: ToolGateway | None = field(default=None, init=False)

    async def __call__(self) -> ToolGateway:
        if not isinstance(self.provider, FakeCommerceProvider):
            raise RuntimeError("Phase 7 Commerce composition permits only FakeCommerceProvider")
        if self._gateway is not None:
            return self._gateway
        tool_factory = SqlAlchemyToolRegistryUnitOfWorkFactory(self.sessions)
        tool_resolver = ResolveActiveTool(tool_factory)
        tools = {
            key: await tool_resolver(key)
            for key in (
                "commerce.inventory.adjust",
                "commerce.refund.submit",
                "commerce.operation.status",
            )
        }
        agent_factory = SqlAlchemyAgentRegistryUnitOfWorkFactory(self.sessions)
        agent_resolver = ResolveActiveAgentVersion(agent_factory)
        permission_factory = SqlAlchemyPermissionUnitOfWorkFactory(self.sessions)
        self._gateway = ToolGateway(
            agent_resolver,
            tool_resolver,
            EvaluateToolPermission(permission_factory, agent_resolver, tool_resolver),
            ToolExecutionBindingRegistry(
                commerce_tool_bindings(self.authority, self.provider, tools)
            ),
            SqlAlchemyGatewayUnitOfWorkFactory(self.sessions),
        )
        return self._gateway
