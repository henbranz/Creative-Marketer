from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from creative_marketer.events.application import ConsumerUnitOfWork
from creative_marketer.events.domain import DomainEvent, EventScopeKind

from .contracts import AgentExecutionWorkflowInput, ResearcherWorkflowInput


class AgentExecutionWorkflowStarter(Protocol):
    async def start_agent(self, request: AgentExecutionWorkflowInput) -> None: ...


class AgentTypeResolver(Protocol):
    async def agent_type(self, tenant_id: UUID, run_id: UUID) -> str | None: ...


class ResearcherWorkflowStarter(Protocol):
    async def start_researcher(self, request: ResearcherWorkflowInput) -> None: ...


@dataclass(slots=True)
class StartAgentExecutionWorkflow:
    client: AgentExecutionWorkflowStarter
    resolver: AgentTypeResolver

    async def __call__(self, event: DomainEvent, _uow: ConsumerUnitOfWork) -> None:
        if (
            event.event_type != "agent.run.requested.v1"
            or event.scope_kind is not EventScopeKind.TENANT
            or event.tenant_id is None
        ):
            raise ValueError("unsupported Agent execution workflow event")
        run_id = event.payload.get("agent_run_id")
        if not isinstance(run_id, str) or str(event.aggregate_id) != run_id:
            raise ValueError("AgentRun event identity mismatch")
        if await self.resolver.agent_type(event.tenant_id, UUID(run_id)) != "creative_strategist":
            raise ValueError("AgentRun is not routed to the Creative Strategist capability")
        await self.client.start_agent(
            AgentExecutionWorkflowInput(str(event.tenant_id), run_id, str(event.correlation_id))
        )


@dataclass(slots=True)
class RouteAgentWorkflow:
    researcher: ResearcherWorkflowStarter
    agent: AgentExecutionWorkflowStarter
    resolver: AgentTypeResolver

    async def __call__(self, event: DomainEvent, _uow: ConsumerUnitOfWork) -> None:
        if event.scope_kind is not EventScopeKind.TENANT or event.tenant_id is None:
            raise ValueError("AgentRun event must be tenant scoped")
        run_id = event.payload.get("agent_run_id")
        if (
            event.event_type != "agent.run.requested.v1"
            or not isinstance(run_id, str)
            or str(event.aggregate_id) != run_id
        ):
            raise ValueError("unsupported AgentRun event")
        agent_type = await self.resolver.agent_type(event.tenant_id, UUID(run_id))
        if agent_type == "researcher":
            await self.researcher.start_researcher(
                ResearcherWorkflowInput(str(event.tenant_id), run_id, str(event.correlation_id))
            )
        elif agent_type == "creative_strategist":
            await self.agent.start_agent(
                AgentExecutionWorkflowInput(str(event.tenant_id), run_id, str(event.correlation_id))
            )
        else:
            raise ValueError("AgentRun capability is unavailable")
