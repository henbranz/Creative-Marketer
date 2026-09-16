# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import WorkflowAlreadyStartedError

from creative_marketer.events.domain import DomainEvent, EventScopeKind
from creative_marketer.identity.application.authentication import ActorKind
from creative_marketer.infrastructure.temporal.client import TemporalMeasurementWorkflowStarter
from creative_marketer.workflow_orchestration.contracts import MeasurementWorkflowInput
from creative_marketer.workflow_orchestration.measurement_bridge import (
    StartPerformanceCollectionWorkflow,
)


def request() -> MeasurementWorkflowInput:
    return MeasurementWorkflowInput(str(uuid4()), str(uuid4()), str(uuid4()))


@pytest.mark.asyncio
async def test_measurement_starter_is_deterministic_and_duplicate_safe() -> None:
    class Client:
        def __init__(self):
            self.calls = []

        async def start_workflow(self, workflow, value, **kwargs):
            self.calls.append((workflow, value, kwargs))

    client, value = Client(), request()
    await TemporalMeasurementWorkflowStarter(client, "measurement-queue").start_measurement(value)
    assert client.calls[0][1] is value
    assert value.publication_id in client.calls[0][2]["id"]
    assert client.calls[0][2]["task_queue"] == "measurement-queue"

    class DuplicateClient:
        async def start_workflow(self, *_args, **_kwargs):
            raise WorkflowAlreadyStartedError("workflow", "PerformanceCollectionWorkflow")

        def get_workflow_handle(self, _workflow_id):
            return SimpleNamespace(describe=self.describe)

        async def describe(self):
            return SimpleNamespace(status=WorkflowExecutionStatus.COMPLETED)

    await TemporalMeasurementWorkflowStarter(DuplicateClient()).start_measurement(value)


@pytest.mark.asyncio
async def test_measurement_starter_rejects_unexpected_duplicate_state() -> None:
    class Client:
        async def start_workflow(self, *_args, **_kwargs):
            raise WorkflowAlreadyStartedError("workflow", "PerformanceCollectionWorkflow")

        def get_workflow_handle(self, _workflow_id):
            return SimpleNamespace(describe=self.describe)

        async def describe(self):
            return SimpleNamespace(status=WorkflowExecutionStatus.CANCELED)

    with pytest.raises(WorkflowAlreadyStartedError):
        await TemporalMeasurementWorkflowStarter(Client()).start_measurement(request())


@pytest.mark.asyncio
async def test_publication_fact_starts_measurement_without_publishing_coupling() -> None:
    tenant_id, publication_id = uuid4(), uuid4()
    event = DomainEvent(
        event_type="publishing.publication.published.v1",
        schema_version=1,
        scope_kind=EventScopeKind.TENANT,
        tenant_id=tenant_id,
        aggregate_type="publication",
        aggregate_id=publication_id,
        occurred_at=datetime.now(UTC),
        actor_kind=ActorKind.WORKLOAD,
        actor_id=uuid4(),
        correlation_id=uuid4(),
        payload={"publication_id": str(publication_id)},
        payload_schema_digest="sha256:" + "a" * 64,
    )
    client = SimpleNamespace(start_measurement=AsyncMock())
    bridge = StartPerformanceCollectionWorkflow(client)
    await bridge(event, SimpleNamespace())
    value = client.start_measurement.await_args.args[0]
    assert value.tenant_id == str(tenant_id)
    assert value.publication_id == str(publication_id)

    def altered(**changes):
        values = {field: getattr(event, field) for field in event.__slots__}
        values.update(changes)
        return SimpleNamespace(**values)

    for invalid in (
        altered(event_type="other.v1"),
        altered(scope_kind=EventScopeKind.PLATFORM),
        altered(aggregate_type="publication_draft"),
        altered(payload={}),
        altered(payload={"publication_id": str(uuid4())}),
    ):
        with pytest.raises(ValueError):
            await bridge(invalid, SimpleNamespace())
