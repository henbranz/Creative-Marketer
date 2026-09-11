from dataclasses import dataclass
from typing import Protocol

from creative_marketer.events.application import ConsumerUnitOfWork
from creative_marketer.events.domain import DomainEvent, EventScopeKind
from creative_marketer.workflow_orchestration.contracts import ResearcherWorkflowInput


class ResearcherWorkflowStarter(Protocol):
    async def start_researcher(self, request: ResearcherWorkflowInput) -> None: ...


@dataclass(slots=True)
class StartResearcherWorkflow:
    """Inbox handler: Temporal start succeeds before Inbox receipt commits."""

    client: ResearcherWorkflowStarter

    async def __call__(self, event: DomainEvent, _uow: ConsumerUnitOfWork) -> None:
        if (
            event.event_type != "agent.run.requested.v1"
            or event.scope_kind is not EventScopeKind.TENANT
            or event.tenant_id is None
        ):
            raise ValueError("unsupported Researcher workflow event")
        run_id = event.payload.get("agent_run_id")
        if not isinstance(run_id, str) or str(event.aggregate_id) != run_id:
            raise ValueError("AgentRun event identity mismatch")
        await self.client.start_researcher(
            ResearcherWorkflowInput(str(event.tenant_id), run_id, str(event.correlation_id))
        )
