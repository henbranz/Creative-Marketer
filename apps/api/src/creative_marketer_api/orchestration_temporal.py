from __future__ import annotations

import asyncio
from uuid import UUID

from creative_marketer.infrastructure.temporal.client import (
    TemporalCreativeCycleWorkflowStarter,
    TemporalMeasurementWorkflowStarter,
    TemporalPublicationWorkflowStarter,
)
from creative_marketer.infrastructure.temporal.configuration import ORCHESTRATION_TASK_QUEUE
from creative_marketer.infrastructure.temporal.worker import connect_client
from creative_marketer.workflow_orchestration.contracts import (
    CreativeCycleWorkflowInput,
    MeasurementWorkflowInput,
    PublicationWorkflowInput,
)


class LazyOrchestrationWorkflowCoordinator:
    """Lazy IDs-only Temporal adapter shared by the API orchestration service."""

    def __init__(self, address: str, namespace: str, *, accelerated_demo: bool) -> None:
        self._address = address
        self._namespace = namespace
        self._accelerated_demo = accelerated_demo
        self._cycle: TemporalCreativeCycleWorkflowStarter | None = None
        self._publication: TemporalPublicationWorkflowStarter | None = None
        self._measurement: TemporalMeasurementWorkflowStarter | None = None
        self._lock = asyncio.Lock()

    async def _values(
        self,
    ) -> tuple[
        TemporalCreativeCycleWorkflowStarter,
        TemporalPublicationWorkflowStarter,
        TemporalMeasurementWorkflowStarter,
    ]:
        if self._cycle is None or self._publication is None or self._measurement is None:
            async with self._lock:
                if self._cycle is None:
                    client = await connect_client(self._address, namespace=self._namespace)
                    self._cycle = TemporalCreativeCycleWorkflowStarter(
                        client, ORCHESTRATION_TASK_QUEUE
                    )
                    self._publication = TemporalPublicationWorkflowStarter(
                        client, ORCHESTRATION_TASK_QUEUE
                    )
                    self._measurement = TemporalMeasurementWorkflowStarter(
                        client, ORCHESTRATION_TASK_QUEUE
                    )
        assert self._cycle is not None
        assert self._publication is not None
        assert self._measurement is not None
        return self._cycle, self._publication, self._measurement

    async def start_cycle(self, tenant_id: UUID, cycle_id: UUID, correlation_id: UUID) -> None:
        cycle, _, _ = await self._values()
        await cycle.start_cycle(
            CreativeCycleWorkflowInput(str(tenant_id), str(cycle_id), str(correlation_id))
        )

    async def wake_cycle(self, tenant_id: UUID, cycle_id: UUID) -> None:
        cycle, _, _ = await self._values()
        await cycle.wake_cycle(
            CreativeCycleWorkflowInput(str(tenant_id), str(cycle_id), str(UUID(int=0)))
        )

    async def start_publication(
        self, tenant_id: UUID, draft_id: UUID, correlation_id: UUID
    ) -> None:
        _, publication, _ = await self._values()
        await publication.start_publication(
            PublicationWorkflowInput(str(tenant_id), str(draft_id), str(correlation_id))
        )

    async def start_measurement(
        self, tenant_id: UUID, publication_id: UUID, correlation_id: UUID
    ) -> None:
        _, _, measurement = await self._values()
        delays = (0,) if self._accelerated_demo else (3600, 21600, 86400, 259200, 604800)
        await measurement.start_measurement(
            MeasurementWorkflowInput(
                str(tenant_id),
                str(publication_id),
                str(correlation_id),
                checkpoint_delays_seconds=delays,
            )
        )
