from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from creative_marketer.approval_governance.application import (
    ApprovalUnitOfWorkFactory,
    DecideApproval,
)
from creative_marketer.approval_governance.domain import ApprovalConflict, HumanDecision
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.tool_execution.domain import (
    GatewayStatus,
    ToolInvocationRequest,
    TrustedAgentInvocation,
)

from .execution import PublicationExecutionAuthority, PublishingGateway


class PublishingGatewayFactory(Protocol):
    async def __call__(self) -> PublishingGateway: ...


@dataclass(slots=True)
class BindPublicationApproval:
    """Bind one explicit publication click to the exact R4 Tool Gateway action."""

    authority: PublicationExecutionAuthority
    gateway_factory: PublishingGatewayFactory
    approval_uow: ApprovalUnitOfWorkFactory

    async def __call__(self, context: ExecutionContext, draft_id: UUID) -> None:
        execution = await self.authority.prepare(context.tenant_id, draft_id)
        gateway = await self.gateway_factory()
        result = await gateway.request(
            TrustedAgentInvocation(execution.context, execution.requested_agent_definition_id),
            ToolInvocationRequest(
                "social.publish.submit",
                {"publication_draft_id": str(draft_id)},
                execution.operation_id,
            ),
        )
        if result.status is GatewayStatus.AWAITING_APPROVAL:
            if result.approval_request_id is None:
                raise RuntimeError("publication approval request binding is missing")
            with suppress(ApprovalConflict):
                await DecideApproval(self.approval_uow)(
                    context,
                    result.approval_request_id,
                    HumanDecision.APPROVE,
                    reason_code="publication_draft_approved",
                )
            return
        if result.status is GatewayStatus.IN_PROGRESS:
            return
        raise RuntimeError(result.reason_code or "publication governance binding failed")
