from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from creative_marketer.events.application import ConsumerUnitOfWork
from creative_marketer.events.domain import DomainEvent, EventScopeKind
from creative_marketer.workflow_orchestration.contracts import (
    FinalCreativeAssemblyWorkflowInput,
)


class AssemblyWorkflowStarter(Protocol):
    async def start_assembly(self, request: FinalCreativeAssemblyWorkflowInput) -> None: ...


@dataclass(slots=True)
class StartFinalCreativeAssemblyWorkflow:
    client: AssemblyWorkflowStarter

    async def __call__(self, event: DomainEvent, _uow: ConsumerUnitOfWork) -> None:
        if (
            event.event_type != "assembly.plan.created.v1"
            or event.scope_kind is not EventScopeKind.TENANT
            or event.tenant_id is None
            or event.aggregate_type != "assembly_plan"
        ):
            raise ValueError("unsupported Assembly workflow event")
        plan_id = event.payload.get("assembly_plan_id")
        job_id = event.payload.get("assembly_job_id")
        if (
            not isinstance(plan_id, str)
            or not isinstance(job_id, str)
            or UUID(plan_id) != event.aggregate_id
        ):
            raise ValueError("Assembly event identity mismatch")
        await self.client.start_assembly(
            FinalCreativeAssemblyWorkflowInput(
                str(event.tenant_id), plan_id, job_id, str(event.correlation_id)
            )
        )
