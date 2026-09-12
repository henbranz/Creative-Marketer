"""Composition helper that keeps delivery processes outside the Tool Gateway internals."""

from creative_marketer.permission_governance.application import (
    AgentResolver,
    EvaluateToolPermission,
    PermissionUnitOfWorkFactory,
    ToolResolver,
)
from creative_marketer.tool_execution.application import (
    GatewayUnitOfWorkFactory,
    ToolExecutionBinding,
    ToolExecutionBindingRegistry,
    ToolGateway,
)

from .execution import ProductionGateway


def compose_production_gateway(
    agent_resolver: AgentResolver,
    tool_resolver: ToolResolver,
    permission_uow: PermissionUnitOfWorkFactory,
    bindings: tuple[ToolExecutionBinding, ...],
    gateway_uow: GatewayUnitOfWorkFactory,
) -> ProductionGateway:
    """Build the existing internal gateway without exposing it to the delivery layer."""
    return ToolGateway(
        agent_resolver,
        tool_resolver,
        EvaluateToolPermission(permission_uow, agent_resolver, tool_resolver),
        ToolExecutionBindingRegistry(bindings),
        gateway_uow,
    )
