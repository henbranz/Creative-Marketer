from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.tool_execution.application import ToolGateway
from creative_marketer.tool_execution.domain import (
    GatewayStatus,
    ToolInvocationRequest,
    TrustedAgentInvocation,
)
from creative_marketer.workflow_orchestration.contracts import (
    CommerceActionWorkflowInput,
    CommerceSyncWorkflowInput,
)

from .domain import ActionType, CommerceNotFound, CommercePermissionDenied, SyncType


@dataclass(frozen=True, slots=True)
class CommerceActionView:
    proposal_id: UUID
    tool_key: str
    risk_level: str
    state: str
    operation_id: str | None
    request_ref: str | None
    approval_request_id: UUID | None
    result_ref: str | None
    safe_failure_code: str | None
    store: str
    sku_or_variant: str | None
    current_quantity: int | None
    exact_quantity: int | None
    order_reference: str | None
    exact_amount: str | None
    currency: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class CommerceSyncView:
    request_id: UUID
    connection_id: UUID
    status: str
    safe_failure_code: str | None = None


class CommerceGovernanceStore(Protocol):
    async def proposal_action(self, tenant_id: UUID, proposal_id: UUID) -> ActionType: ...
    async def action_view(self, tenant_id: UUID, proposal_id: UUID) -> CommerceActionView: ...
    async def commerce_agent_definition(self, tenant_id: UUID) -> UUID: ...
    async def proposal_for_approval(self, tenant_id: UUID, approval_id: UUID) -> UUID: ...
    async def latest_sync_view(
        self, tenant_id: UUID, connection_id: UUID
    ) -> CommerceSyncView | None: ...
    async def create_sync_request(
        self,
        context: ExecutionContext,
        connection_id: UUID,
        sync_types: tuple[SyncType, ...],
        idempotency_key: str,
    ) -> CommerceSyncView: ...
    async def fail_sync_start(self, tenant_id: UUID, request_id: UUID) -> None: ...


class CommerceWorkflowStarter(Protocol):
    async def start_action(self, request: CommerceActionWorkflowInput) -> None: ...
    async def signal_action(self, request: CommerceActionWorkflowInput) -> None: ...
    async def start_sync(self, request: CommerceSyncWorkflowInput) -> None: ...


class CommerceGatewayFactory(Protocol):
    async def __call__(self) -> ToolGateway: ...


def commerce_operation_id(tenant_id: UUID, proposal_id: UUID, tool_key: str) -> str:
    return (
        "op_"
        + uuid5(
            NAMESPACE_URL, f"creative-marketer:{tenant_id}:commerce:{proposal_id}:{tool_key}:v1"
        ).hex
    )


def _tool_key(action: ActionType) -> str:
    return (
        "commerce.inventory.adjust"
        if action is ActionType.INVENTORY_ADJUSTMENT
        else "commerce.refund.submit"
    )


@dataclass(slots=True)
class CommerceGovernedExecutionService:
    store: CommerceGovernanceStore
    gateway_factory: CommerceGatewayFactory
    workflows: CommerceWorkflowStarter

    async def request_action(
        self, context: ExecutionContext, proposal_id: UUID
    ) -> CommerceActionView:
        existing = await self.store.action_view(context.tenant_id, proposal_id)
        if existing.operation_id is not None:
            return existing
        action = await self.store.proposal_action(context.tenant_id, proposal_id)
        tool_key = _tool_key(action)
        agent_definition_id = await self.store.commerce_agent_definition(context.tenant_id)
        operation_id = commerce_operation_id(context.tenant_id, proposal_id, tool_key)
        gateway = await self.gateway_factory()
        result = await gateway.request(
            TrustedAgentInvocation(context, agent_definition_id),
            ToolInvocationRequest(
                tool_key,
                {"commerce_action_proposal_id": str(proposal_id)},
                operation_id,
            ),
        )
        if result.status not in {GatewayStatus.AWAITING_APPROVAL, GatewayStatus.IN_PROGRESS}:
            raise CommercePermissionDenied(result.reason_code or result.status.value)
        if result.tool_call_id is None:
            raise CommercePermissionDenied("durable ToolCall was not created")
        request_ref = f"tool-request://{result.tool_call_id.hex}"
        workflow_input = CommerceActionWorkflowInput(
            str(context.tenant_id),
            str(proposal_id),
            str(context.correlation_id),
            request_ref,
        )
        await self.workflows.start_action(workflow_input)
        return await self.store.action_view(context.tenant_id, proposal_id)

    async def wake_action(self, context: ExecutionContext, proposal_id: UUID) -> CommerceActionView:
        view = await self.store.action_view(context.tenant_id, proposal_id)
        if view.request_ref is None:
            raise CommerceNotFound("governed commerce request not found")
        await self.workflows.signal_action(
            CommerceActionWorkflowInput(
                str(context.tenant_id),
                str(proposal_id),
                str(context.correlation_id),
                view.request_ref,
            )
        )
        return view

    async def request_sync(
        self,
        context: ExecutionContext,
        connection_id: UUID,
        sync_types: tuple[SyncType, ...],
        idempotency_key: str,
    ) -> CommerceSyncView:
        value = await self.store.create_sync_request(
            context, connection_id, sync_types, idempotency_key
        )
        try:
            await self.workflows.start_sync(
                CommerceSyncWorkflowInput(
                    str(context.tenant_id),
                    str(value.request_id),
                    str(connection_id),
                    str(context.correlation_id),
                    tuple(item.value for item in sync_types),
                )
            )
        except Exception:
            await self.store.fail_sync_start(context.tenant_id, value.request_id)
            raise
        return value
