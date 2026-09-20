from typing import Any

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.exceptions import WorkflowAlreadyStartedError

from creative_marketer.infrastructure.temporal.configuration import (
    COMMERCE_TASK_QUEUE,
    PRODUCTION_TASK_QUEUE,
    WORKFLOW_TASK_QUEUE,
)
from creative_marketer.infrastructure.temporal.workflows import (
    AgentExecutionWorkflow,
    CommerceActionWorkflow,
    CommerceSyncWorkflow,
    CreativeCycleWorkflow,
    FinalCreativeAssemblyWorkflow,
    MediaProductionWorkflow,
    PerformanceCollectionWorkflow,
    PublicationWorkflow,
    ResearcherWorkflow,
)
from creative_marketer.workflow_orchestration.contracts import (
    AgentExecutionWorkflowInput,
    CommerceActionWorkflowInput,
    CommerceSyncWorkflowInput,
    CreativeCycleWorkflowInput,
    FinalCreativeAssemblyWorkflowInput,
    MeasurementWorkflowInput,
    MediaProductionWorkflowInput,
    PublicationWorkflowInput,
    ResearcherWorkflowInput,
    agent_execution_workflow_id,
    commerce_action_workflow_id,
    commerce_sync_workflow_id,
    creative_cycle_workflow_id,
    final_creative_assembly_workflow_id,
    measurement_workflow_id,
    media_production_workflow_id,
    publication_workflow_id,
    researcher_workflow_id,
)


class TemporalCreativeCycleWorkflowStarter:
    def __init__(self, client: Client, task_queue: str = WORKFLOW_TASK_QUEUE) -> None:
        self._client, self._task_queue = client, task_queue

    async def start_cycle(self, request: CreativeCycleWorkflowInput) -> None:
        workflow_id = creative_cycle_workflow_id(request)
        try:
            await self._client.start_workflow(
                CreativeCycleWorkflow.run,
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

    async def wake_cycle(self, request: CreativeCycleWorkflowInput) -> None:
        handle = self._client.get_workflow_handle(creative_cycle_workflow_id(request))
        await handle.signal("cycle_state_may_have_changed")


class TemporalCommerceWorkflowStarter:
    def __init__(self, client: Client, task_queue: str = COMMERCE_TASK_QUEUE) -> None:
        self._client, self._task_queue = client, task_queue

    async def start_sync(self, request: CommerceSyncWorkflowInput) -> None:
        await self._start(CommerceSyncWorkflow.run, request, commerce_sync_workflow_id(request))

    async def start_action(self, request: CommerceActionWorkflowInput) -> None:
        await self._start(CommerceActionWorkflow.run, request, commerce_action_workflow_id(request))

    async def signal_action(self, request: CommerceActionWorkflowInput) -> None:
        handle = self._client.get_workflow_handle(commerce_action_workflow_id(request))
        await handle.signal("approval_state_may_have_changed")

    async def _start(self, workflow_run: Any, request: Any, workflow_id: str) -> None:
        try:
            await self._client.start_workflow(
                workflow_run, request, id=workflow_id, task_queue=self._task_queue
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
    def __init__(self, client: Client, task_queue: str = PRODUCTION_TASK_QUEUE) -> None:
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


class TemporalPublicationWorkflowStarter:
    def __init__(self, client: Client, task_queue: str = WORKFLOW_TASK_QUEUE) -> None:
        self._client, self._task_queue = client, task_queue

    async def start_publication(self, request: PublicationWorkflowInput) -> None:
        workflow_id = publication_workflow_id(request)
        try:
            await self._client.start_workflow(
                PublicationWorkflow.run,
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

    async def cancel_publication(self, request: PublicationWorkflowInput) -> None:
        handle = self._client.get_workflow_handle(publication_workflow_id(request))
        await handle.signal(PublicationWorkflow.cancel_publication)


class TemporalMeasurementWorkflowStarter:
    def __init__(self, client: Client, task_queue: str = WORKFLOW_TASK_QUEUE) -> None:
        self._client, self._task_queue = client, task_queue

    async def start_measurement(self, request: MeasurementWorkflowInput) -> None:
        workflow_id = measurement_workflow_id(request)
        try:
            await self._client.start_workflow(
                PerformanceCollectionWorkflow.run,
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
