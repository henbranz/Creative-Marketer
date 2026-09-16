from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from creative_marketer.events.application import ConsumerUnitOfWork
from creative_marketer.events.domain import DomainEvent, EventScopeKind
from creative_marketer.workflow_orchestration.contracts import MeasurementWorkflowInput


class MeasurementWorkflowStarter(Protocol):
    async def start_measurement(self, request: MeasurementWorkflowInput) -> None: ...


@dataclass(slots=True)
class StartPerformanceCollectionWorkflow:
    """Translate the immutable publication fact into an independent collection schedule."""

    client: MeasurementWorkflowStarter

    async def __call__(self, event: DomainEvent, _uow: ConsumerUnitOfWork) -> None:
        publication_id = event.payload.get("publication_id")
        if (
            event.event_type != "publishing.publication.published.v1"
            or event.scope_kind is not EventScopeKind.TENANT
            or event.tenant_id is None
            or event.aggregate_type != "publication"
            or not isinstance(publication_id, str)
            or UUID(publication_id) != event.aggregate_id
        ):
            raise ValueError("unsupported Measurement workflow event")
        await self.client.start_measurement(
            MeasurementWorkflowInput(
                str(event.tenant_id), publication_id, str(event.correlation_id)
            )
        )
