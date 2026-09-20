# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,assignment,union-attr"

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

import creative_marketer_api.orchestration_temporal as module
from creative_marketer_api.orchestration_temporal import LazyOrchestrationWorkflowCoordinator


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "accelerated,expected",
    [(True, (0,)), (False, (3600, 21600, 86400, 259200, 604800))],
)
async def test_lazy_coordinator_connects_once_and_sends_ids_only(
    monkeypatch, accelerated, expected
) -> None:
    calls = []

    async def connect(address, *, namespace):
        calls.append((address, namespace))
        return SimpleNamespace()

    class Cycle:
        def __init__(self, _client, queue):
            self.queue, self.started, self.woken = queue, [], []

        async def start_cycle(self, value):
            self.started.append(value)

        async def wake_cycle(self, value):
            self.woken.append(value)

    class Publication:
        def __init__(self, _client, queue):
            self.queue, self.started = queue, []

        async def start_publication(self, value):
            self.started.append(value)

    class Measurement:
        def __init__(self, _client, queue):
            self.queue, self.started = queue, []

        async def start_measurement(self, value):
            self.started.append(value)

    monkeypatch.setattr(module, "connect_client", connect)
    monkeypatch.setattr(module, "TemporalCreativeCycleWorkflowStarter", Cycle)
    monkeypatch.setattr(module, "TemporalPublicationWorkflowStarter", Publication)
    monkeypatch.setattr(module, "TemporalMeasurementWorkflowStarter", Measurement)
    coordinator = LazyOrchestrationWorkflowCoordinator(
        "temporal:7233", "tenant", accelerated_demo=accelerated
    )
    tenant_id, cycle_id, draft_id, publication_id, correlation_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    await coordinator.start_cycle(tenant_id, cycle_id, correlation_id)
    await coordinator.wake_cycle(tenant_id, cycle_id)
    await coordinator.start_publication(tenant_id, draft_id, correlation_id)
    await coordinator.start_measurement(tenant_id, publication_id, correlation_id)

    assert calls == [("temporal:7233", "tenant")]
    assert coordinator._cycle.started[0].cycle_id == str(cycle_id)
    assert coordinator._cycle.woken[0].correlation_id == str(UUID(int=0))
    assert coordinator._publication.started[0].publication_draft_id == str(draft_id)
    assert coordinator._measurement.started[0].checkpoint_delays_seconds == expected
