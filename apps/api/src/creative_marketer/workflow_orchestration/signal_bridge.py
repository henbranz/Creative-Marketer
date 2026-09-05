from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from creative_marketer.events.application import ConsumerUnitOfWork
from creative_marketer.events.domain import DomainEvent, EventScopeKind

APPROVAL_SIGNAL_EVENT_TYPES = frozenset(
    {
        "governance.approval.granted.v1",
        "governance.approval.denied.v1",
        "governance.approval.revoked.v1",
    }
)


class ApprovalWorkflowLocator(Protocol):
    async def workflow_id_for_approval(self, tenant_id: UUID, approval_id: UUID) -> str | None: ...


class WorkflowSignalClient(Protocol):
    async def signal_approval_state_changed(self, workflow_id: str) -> None: ...


@dataclass(slots=True)
class SignalApprovalWorkflow:
    """Inbox handler whose RPC must succeed before ProcessEvent commits its receipt."""

    locator: ApprovalWorkflowLocator
    client: WorkflowSignalClient

    async def __call__(self, event: DomainEvent, _uow: ConsumerUnitOfWork) -> None:
        if (
            event.event_type not in APPROVAL_SIGNAL_EVENT_TYPES
            or event.scope_kind is not EventScopeKind.TENANT
            or event.tenant_id is None
        ):
            raise ValueError("unsupported approval workflow signal event")
        raw_approval_id = event.payload.get("approval_request_id")
        if not isinstance(raw_approval_id, str):
            raise ValueError("approval event lacks approval_request_id")
        approval_id = UUID(raw_approval_id)
        if event.aggregate_type != "approval_request" or event.aggregate_id != approval_id:
            raise ValueError("approval event aggregate does not match payload")
        workflow_id = await self.locator.workflow_id_for_approval(event.tenant_id, approval_id)
        if workflow_id is None:
            raise RuntimeError("authoritative approval workflow association was not found")
        await self.client.signal_approval_state_changed(workflow_id)
