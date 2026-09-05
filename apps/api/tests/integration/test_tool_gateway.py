# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from creative_marketer.action_binding import NormalizedToolInput
from creative_marketer.agent_governance.application import ResolveActiveAgentVersion
from creative_marketer.approval_governance.application import DecideApproval
from creative_marketer.approval_governance.domain import HumanDecision
from creative_marketer.infrastructure.database.agent_governance_uow import (
    SqlAlchemyAgentRegistryUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.approval_uow import (
    SqlAlchemyApprovalUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.permission_governance_uow import (
    SqlAlchemyPermissionUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.tool_execution_schema import tool_calls
from creative_marketer.infrastructure.database.tool_execution_uow import (
    SqlAlchemyGatewayUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.tool_governance_uow import (
    SqlAlchemyToolRegistryUnitOfWorkFactory,
)
from creative_marketer.permission_governance.application import EvaluateToolPermission
from creative_marketer.permission_governance.domain import (
    ScopeAccess,
    ScopeRequirement,
    TrustedScopeRequirements,
)
from creative_marketer.tool_execution.application import (
    ResourceResolution,
    ToolExecutionBinding,
    ToolExecutionBindingRegistry,
    ToolGateway,
)
from creative_marketer.tool_execution.domain import (
    GatewayStatus,
    ToolExecutorResult,
    ToolInvocationRequest,
    TrustedAgentInvocation,
)
from creative_marketer.tool_governance.application import ResolveActiveTool
from tests.integration.support import IdentityStack
from tests.integration.test_approval_idempotency import approval_subject


class CatalogResourceResolver:
    async def __call__(self, context, tool, normalized):
        return ResourceResolution(
            TrustedScopeRequirements((ScopeRequirement("catalog.product", ScopeAccess.READ),))
        )


class FakeReadExecutor:
    def __init__(self):
        self.invocations = 0

    async def execute(self, execution_context, normalized):
        self.invocations += 1
        return ToolExecutorResult({}, "result://fake/read-1")


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_governed_approval_execution_replay_audit_outbox_and_rls(
    admin_engine: AsyncEngine,
    runtime_engine: AsyncEngine,
    identity_stack: IdentityStack,
    agent_registry_factory: SqlAlchemyAgentRegistryUnitOfWorkFactory,
    tool_control_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
    tool_runtime_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
    permission_factory: SqlAlchemyPermissionUnitOfWorkFactory,
    approval_factory: SqlAlchemyApprovalUnitOfWorkFactory,
    gateway_factory: SqlAlchemyGatewayUnitOfWorkFactory,
) -> None:
    ctx, decision, agent_definition, _ = await approval_subject(
        identity_stack,
        agent_registry_factory,
        tool_control_factory,
        tool_runtime_factory,
        permission_factory,
    )
    resolved_tool = await ResolveActiveTool(tool_runtime_factory)(decision.tool_key)
    executor = FakeReadExecutor()
    gateway = ToolGateway(
        ResolveActiveAgentVersion(agent_registry_factory),
        ResolveActiveTool(tool_runtime_factory),
        EvaluateToolPermission(
            permission_factory,
            ResolveActiveAgentVersion(agent_registry_factory),
            ResolveActiveTool(tool_runtime_factory),
        ),
        ToolExecutionBindingRegistry(
            (
                ToolExecutionBinding(
                    resolved_tool.definition_id,
                    resolved_tool.version_id,
                    NormalizedToolInput.from_trusted_value,
                    CatalogResourceResolver(),
                    executor,
                ),
            )
        ),
        gateway_factory,
    )
    invocation = TrustedAgentInvocation(ctx, agent_definition.id)
    request = ToolInvocationRequest(decision.tool_key, {}, "op_" + uuid4().hex)
    waiting = await gateway.invoke(invocation, request)
    assert waiting.status is GatewayStatus.AWAITING_APPROVAL and executor.invocations == 0
    await DecideApproval(approval_factory)(ctx, waiting.approval_request_id, HumanDecision.APPROVE)
    concurrent = await asyncio.gather(
        gateway.invoke(invocation, request), gateway.invoke(invocation, request)
    )
    executed = next(item for item in concurrent if item.status is GatewayStatus.EXECUTED)
    assert {item.status for item in concurrent}.issubset(
        {GatewayStatus.EXECUTED, GatewayStatus.IN_PROGRESS, GatewayStatus.REPLAYED}
    )
    assert executor.invocations == 1
    assert (await gateway.invoke(invocation, request)).status is GatewayStatus.REPLAYED
    assert executor.invocations == 1

    async with admin_engine.connect() as connection:
        call = (
            (
                await connection.execute(
                    select(tool_calls).where(tool_calls.c.id == executed.tool_call_id)
                )
            )
            .one()
            ._mapping
        )
        assert (
            call["status"] == "SUCCEEDED"
            and call["approval_request_id"] == waiting.approval_request_id
        )
        evidence = (
            (
                await connection.execute(
                    text("SELECT action FROM audit.audit_records WHERE tool_call_id = :call"),
                    {"call": executed.tool_call_id},
                )
            )
            .scalars()
            .all()
        )
        assert {
            "tool.invocation.awaiting_approval",
            "tool.execution.started",
            "tool.execution.succeeded",
        }.issubset(evidence)
        events = (
            (
                await connection.execute(
                    text(
                        "SELECT event_type FROM event_delivery.outbox_events "
                        "WHERE aggregate_id = :call"
                    ),
                    {"call": executed.tool_call_id},
                )
            )
            .scalars()
            .all()
        )
        assert events == ["governance.tool.execution_succeeded.v1"]

    other_tenant = uuid4()
    async with runtime_engine.connect() as connection:
        transaction = await connection.begin()
        await connection.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant, true)"),
            {"tenant": str(other_tenant)},
        )
        assert (
            await connection.scalar(
                select(tool_calls.c.id).where(tool_calls.c.id == executed.tool_call_id)
            )
            is None
        )
        with pytest.raises(ProgrammingError):
            await connection.execute(text("DELETE FROM tool_execution.tool_calls"))
        await transaction.rollback()
    protected_mutations: tuple[dict[str, object], ...] = (
        {"tenant_id": uuid4()},
        {"operation_id": "op_" + uuid4().hex},
        {"action_digest": "sha256:" + "b" * 64},
        {"normalized_input_digest": "sha256:" + "c" * 64},
        {"status": "READY"},
        {"result_ref": "result://attacker/rewrite"},
        {"error_code": "ATTACKER_REWRITE"},
    )
    async with admin_engine.connect() as connection:
        for values in protected_mutations:
            transaction = await connection.begin()
            with pytest.raises(DBAPIError):
                await connection.execute(
                    update(tool_calls)
                    .where(tool_calls.c.id == executed.tool_call_id)
                    .values(**values)
                )
            await transaction.rollback()
