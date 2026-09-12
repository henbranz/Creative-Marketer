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
from creative_marketer.infrastructure.temporal.client import (
    TemporalFinalCreativeAssemblyWorkflowStarter,
)
from creative_marketer.workflow_orchestration.assembly_bridge import (
    StartFinalCreativeAssemblyWorkflow,
)
from creative_marketer.workflow_orchestration.contracts import (
    FinalCreativeAssemblyWorkflowInput,
)


def request() -> FinalCreativeAssemblyWorkflowInput:
    return FinalCreativeAssemblyWorkflowInput(
        str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())
    )


@pytest.mark.asyncio
async def test_assembly_starter_is_deterministic_and_duplicate_safe() -> None:
    class Client:
        def __init__(self):
            self.calls = []

        async def start_workflow(self, workflow, value, **kwargs):
            self.calls.append((workflow, value, kwargs))

    client, value = Client(), request()
    await TemporalFinalCreativeAssemblyWorkflowStarter(client, "assembly-queue").start_assembly(
        value
    )
    assert client.calls[0][1] is value
    assert value.assembly_plan_id in client.calls[0][2]["id"]
    assert client.calls[0][2]["task_queue"] == "assembly-queue"

    class DuplicateClient:
        async def start_workflow(self, *_args, **_kwargs):
            raise WorkflowAlreadyStartedError("workflow", "FinalCreativeAssemblyWorkflow")

        def get_workflow_handle(self, _workflow_id):
            return SimpleNamespace(
                describe=self.describe,
            )

        async def describe(self):
            return SimpleNamespace(status=WorkflowExecutionStatus.COMPLETED)

    await TemporalFinalCreativeAssemblyWorkflowStarter(
        DuplicateClient(), "assembly-queue"
    ).start_assembly(value)


@pytest.mark.asyncio
async def test_assembly_starter_rejects_unexpected_duplicate_state() -> None:
    class Client:
        async def start_workflow(self, *_args, **_kwargs):
            raise WorkflowAlreadyStartedError("workflow", "FinalCreativeAssemblyWorkflow")

        def get_workflow_handle(self, _workflow_id):
            return SimpleNamespace(describe=self.describe)

        async def describe(self):
            return SimpleNamespace(status=WorkflowExecutionStatus.CANCELED)

    with pytest.raises(WorkflowAlreadyStartedError):
        await TemporalFinalCreativeAssemblyWorkflowStarter(Client(), "queue").start_assembly(
            request()
        )


@pytest.mark.asyncio
async def test_plan_created_bridge_validates_identity_and_starts_workflow() -> None:
    tenant_id, plan_id, job_id = uuid4(), uuid4(), uuid4()
    event = DomainEvent(
        event_type="assembly.plan.created.v1",
        schema_version=1,
        scope_kind=EventScopeKind.TENANT,
        tenant_id=tenant_id,
        aggregate_type="assembly_plan",
        aggregate_id=plan_id,
        occurred_at=datetime.now(UTC),
        actor_kind=ActorKind.WORKLOAD,
        actor_id=uuid4(),
        correlation_id=uuid4(),
        payload={"assembly_plan_id": str(plan_id), "assembly_job_id": str(job_id)},
        payload_schema_digest="sha256:" + "a" * 64,
    )
    client = SimpleNamespace(start_assembly=AsyncMock())
    await StartFinalCreativeAssemblyWorkflow(client)(event, SimpleNamespace())
    value = client.start_assembly.await_args.args[0]
    assert value.tenant_id == str(tenant_id) and value.assembly_job_id == str(job_id)

    def altered(**changes):
        values = {field: getattr(event, field) for field in event.__slots__}
        values.update(changes)
        return SimpleNamespace(**values)

    for invalid in (
        altered(event_type="other.v1"),
        altered(scope_kind=EventScopeKind.PLATFORM),
        altered(payload={}),
        altered(payload={"assembly_plan_id": str(uuid4()), "assembly_job_id": str(job_id)}),
    ):
        with pytest.raises(ValueError):
            await StartFinalCreativeAssemblyWorkflow(client)(invalid, SimpleNamespace())
