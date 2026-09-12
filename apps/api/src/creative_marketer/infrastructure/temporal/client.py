from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.exceptions import WorkflowAlreadyStartedError

from creative_marketer.infrastructure.temporal.configuration import WORKFLOW_TASK_QUEUE
from creative_marketer.infrastructure.temporal.workflows import (
    AgentExecutionWorkflow,
    FinalCreativeAssemblyWorkflow,
    MediaProductionWorkflow,
    ResearcherWorkflow,
)
from creative_marketer.workflow_orchestration.contracts import (
    AgentExecutionWorkflowInput,
    FinalCreativeAssemblyWorkflowInput,
    MediaProductionWorkflowInput,
    ResearcherWorkflowInput,
    agent_execution_workflow_id,
    final_creative_assembly_workflow_id,
    media_production_workflow_id,
    researcher_workflow_id,
)


class TemporalWorkflowSignalClient:
    def __init__(self, client: Client) -> None:
        self._client = client

    async def signal_approval_state_changed(self, workflow_id: str) -> None:
        handle = self._client.get_workflow_handle(workflow_id)
        await handle.signal("approval_state_may_have_changed")


class TemporalResearcherWorkflowStarter:
    def __init__(self, client: Client, task_queue: str = WORKFLOW_TASK_QUEUE) -> None:
        self._client, self._task_queue = client, task_queue

    async def start_researcher(self, request: ResearcherWorkflowInput) -> None:
        workflow_id = researcher_workflow_id(request)
        try:
            await self._client.start_workflow(
                ResearcherWorkflow.run, request, id=workflow_id, task_queue=self._task_queue
            )
        except WorkflowAlreadyStartedError:
            handle = self._client.get_workflow_handle(workflow_id)
            description = await handle.describe()
            if description.status not in {
                WorkflowExecutionStatus.RUNNING,
                WorkflowExecutionStatus.COMPLETED,
                WorkflowExecutionStatus.FAILED,
            }:
                raise


class TemporalAgentExecutionWorkflowStarter:
    def __init__(self, client: Client, task_queue: str = WORKFLOW_TASK_QUEUE) -> None:
        self._client, self._task_queue = client, task_queue

    async def start_agent(self, request: AgentExecutionWorkflowInput) -> None:
        workflow_id = agent_execution_workflow_id(request)
        try:
            await self._client.start_workflow(
                AgentExecutionWorkflow.run,
                request,
                id=workflow_id,
                task_queue=self._task_queue,
            )
        except WorkflowAlreadyStartedError:
            handle = self._client.get_workflow_handle(workflow_id)
            description = await handle.describe()
            if description.status not in {
                WorkflowExecutionStatus.RUNNING,
                WorkflowExecutionStatus.COMPLETED,
                WorkflowExecutionStatus.FAILED,
            }:
                raise


class TemporalMediaProductionWorkflowStarter:
    def __init__(self, client: Client, task_queue: str = WORKFLOW_TASK_QUEUE) -> None:
        self._client, self._task_queue = client, task_queue

    async def start_media_production(self, request: MediaProductionWorkflowInput) -> None:
        workflow_id = media_production_workflow_id(request)
        try:
            await self._client.start_workflow(
                MediaProductionWorkflow.run,
                request,
                id=workflow_id,
                task_queue=self._task_queue,
            )
        except WorkflowAlreadyStartedError:
            handle = self._client.get_workflow_handle(workflow_id)
            description = await handle.describe()
            if description.status not in {
                WorkflowExecutionStatus.RUNNING,
                WorkflowExecutionStatus.COMPLETED,
                WorkflowExecutionStatus.FAILED,
            }:
                raise


class TemporalFinalCreativeAssemblyWorkflowStarter:
    def __init__(self, client: Client, task_queue: str) -> None:
        self._client, self._task_queue = client, task_queue

    async def start_assembly(self, request: FinalCreativeAssemblyWorkflowInput) -> None:
        workflow_id = final_creative_assembly_workflow_id(request)
        try:
            await self._client.start_workflow(
                FinalCreativeAssemblyWorkflow.run,
                request,
                id=workflow_id,
                task_queue=self._task_queue,
            )
        except WorkflowAlreadyStartedError:
            handle = self._client.get_workflow_handle(workflow_id)
            description = await handle.describe()
            if description.status not in {
                WorkflowExecutionStatus.RUNNING,
                WorkflowExecutionStatus.COMPLETED,
                WorkflowExecutionStatus.FAILED,
            }:
                raise
