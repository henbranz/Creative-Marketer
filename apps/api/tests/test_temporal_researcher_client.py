# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

from types import SimpleNamespace
from uuid import uuid4

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.exceptions import WorkflowAlreadyStartedError

from creative_marketer.infrastructure.temporal.client import (
    TemporalResearcherWorkflowStarter,
)
from creative_marketer.workflow_orchestration.contracts import ResearcherWorkflowInput


def request() -> ResearcherWorkflowInput:
    return ResearcherWorkflowInput(str(uuid4()), str(uuid4()), str(uuid4()))


@pytest.mark.asyncio
async def test_researcher_starter_uses_deterministic_id_and_queue() -> None:
    class Client:
        def __init__(self):
            self.calls = []

        async def start_workflow(self, workflow, value, **kwargs):
            self.calls.append((workflow, value, kwargs))

    client = Client()
    value = request()
    await TemporalResearcherWorkflowStarter(client, "researcher-queue").start_researcher(value)
    assert client.calls[0][1] is value
    assert client.calls[0][2]["id"].endswith(value.agent_run_id)
    assert client.calls[0][2]["task_queue"] == "researcher-queue"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        WorkflowExecutionStatus.RUNNING,
        WorkflowExecutionStatus.COMPLETED,
        WorkflowExecutionStatus.FAILED,
    ],
)
async def test_duplicate_researcher_start_is_idempotent_for_known_terminal_states(status) -> None:
    class Handle:
        async def describe(self):
            return SimpleNamespace(status=status)

    class Client:
        async def start_workflow(self, *_args, **_kwargs):
            raise WorkflowAlreadyStartedError("workflow", "ResearcherWorkflow")

        def get_workflow_handle(self, _workflow_id):
            return Handle()

    await TemporalResearcherWorkflowStarter(Client()).start_researcher(request())


@pytest.mark.asyncio
async def test_duplicate_researcher_start_rejects_unexpected_state() -> None:
    class Handle:
        async def describe(self):
            return SimpleNamespace(status=WorkflowExecutionStatus.CANCELED)

    class Client:
        async def start_workflow(self, *_args, **_kwargs):
            raise WorkflowAlreadyStartedError("workflow", "ResearcherWorkflow")

        def get_workflow_handle(self, _workflow_id):
            return Handle()

    with pytest.raises(WorkflowAlreadyStartedError):
        await TemporalResearcherWorkflowStarter(Client()).start_researcher(request())
