from contextlib import suppress
from datetime import timedelta
from typing import cast

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from creative_marketer.infrastructure.temporal.configuration import (
        GENERATION_ACTIVITY_TIMEOUT,
        GENERATION_RETRY_POLICY,
        RESEARCHER_ACTIVITY_TIMEOUT,
        RESEARCHER_RETRY_POLICY,
        TOOL_ACTIVITY_TIMEOUT,
        TOOL_RETRY_POLICY,
    )
    from creative_marketer.workflow_orchestration.contracts import (
        AgentExecutionActivityResult,
        AgentExecutionWorkflowInput,
        CommerceActionActivityResult,
        CommerceActionWorkflowInput,
        CommerceSyncActivityResult,
        CommerceSyncWorkflowInput,
        FinalCreativeAssemblyResult,
        FinalCreativeAssemblyWorkflowInput,
        GenerationPollResult,
        GenerationStartResult,
        GenerationState,
        GenerationWorkflowInput,
        MeasurementActivityInput,
        MeasurementActivityResult,
        MeasurementWorkflowInput,
        MediaProductionJobResult,
        MediaProductionWorkflowInput,
        PublicationWorkflowInput,
        PublicationWorkflowResult,
        ResearcherActivityResult,
        ResearcherWorkflowInput,
        ToolActivityResult,
        ToolWorkflowInput,
        WorkflowResult,
        WorkflowState,
    )


_EXECUTED = {"EXECUTED", "REPLAYED"}
_WAITING = {"AWAITING_APPROVAL", "IN_PROGRESS"}


def _tool_result(value: ToolActivityResult) -> WorkflowResult:
    if value.status in _EXECUTED:
        state = WorkflowState.COMPLETED
    elif value.status in _WAITING:
        state = WorkflowState.WAITING_APPROVAL
    elif value.status == "DENIED":
        state = WorkflowState.DENIED
    else:
        state = WorkflowState.FAILED
    return WorkflowResult(
        state,
        value.operation_id,
        value.result_ref,
        value.approval_request_id,
        value.reason_code,
    )


@workflow.defn(name="ApprovalBlockingWorkflow")
class ApprovalBlockingWorkflow:
    def __init__(self) -> None:
        self._state = WorkflowState.STARTING
        self._wakeups = 0

    @workflow.signal(name="approval_state_may_have_changed")
    def approval_state_may_have_changed(self) -> None:
        self._wakeups += 1

    @workflow.query(name="status")
    def status(self) -> str:
        return self._state.value

    @workflow.run
    async def run(self, request: ToolWorkflowInput) -> WorkflowResult:
        result = await self._invoke(request)
        if result.status not in _WAITING:
            final = _tool_result(result)
            self._state = final.state
            return final

        self._state = WorkflowState.WAITING_APPROVAL
        deadline = workflow.now() + timedelta(seconds=request.approval_timeout_seconds)
        observed_wakeups = 0
        while workflow.now() < deadline:
            remaining = deadline - workflow.now()
            wait_for = min(remaining, timedelta(seconds=request.approval_fallback_poll_seconds))

            def was_woken(observed: int = observed_wakeups) -> bool:
                return self._wakeups > observed

            with suppress(TimeoutError):
                await workflow.wait_condition(
                    was_woken,
                    timeout=wait_for,
                )
            observed_wakeups = self._wakeups
            self._state = WorkflowState.EXECUTING
            result = await self._invoke(request)
            if result.status not in _WAITING:
                final = _tool_result(result)
                self._state = final.state
                return final
            self._state = WorkflowState.WAITING_APPROVAL

        result = await self._invoke(request)
        final = _tool_result(result)
        if final.state is WorkflowState.WAITING_APPROVAL:
            final = WorkflowResult(
                WorkflowState.EXPIRED,
                request.operation_id,
                approval_request_id=result.approval_request_id,
                reason_code="APPROVAL_WAIT_EXPIRED",
            )
        self._state = final.state
        return final

    @staticmethod
    async def _invoke(request: ToolWorkflowInput) -> ToolActivityResult:
        return cast(
            ToolActivityResult,
            await workflow.execute_activity(
                "workflow.invoke_tool",
                request,
                result_type=ToolActivityResult,
                start_to_close_timeout=TOOL_ACTIVITY_TIMEOUT,
                schedule_to_close_timeout=timedelta(minutes=5),
                retry_policy=TOOL_RETRY_POLICY,
            ),
        )


@workflow.defn(name="MediaGenerationWorkflow")
class MediaGenerationWorkflow:
    def __init__(self) -> None:
        self._state = WorkflowState.STARTING

    @workflow.query(name="status")
    def status(self) -> str:
        return self._state.value

    @workflow.run
    async def run(self, request: GenerationWorkflowInput) -> WorkflowResult:
        self._state = WorkflowState.GENERATING
        started = await workflow.execute_activity(
            "workflow.start_generation",
            request,
            result_type=GenerationStartResult,
            start_to_close_timeout=GENERATION_ACTIVITY_TIMEOUT,
            schedule_to_close_timeout=timedelta(minutes=3),
            retry_policy=GENERATION_RETRY_POLICY,
        )
        deadline = workflow.now() + timedelta(seconds=request.maximum_generation_seconds)
        while workflow.now() < deadline:
            remaining = deadline - workflow.now()
            await workflow.sleep(min(remaining, timedelta(seconds=request.poll_interval_seconds)))
            try:
                result = await workflow.execute_activity(
                    "workflow.poll_generation",
                    args=[request, started.provider_job_ref],
                    result_type=GenerationPollResult,
                    start_to_close_timeout=GENERATION_ACTIVITY_TIMEOUT,
                    schedule_to_close_timeout=timedelta(minutes=3),
                    retry_policy=GENERATION_RETRY_POLICY,
                )
            except ActivityError as error:
                cause = error.cause
                if (
                    isinstance(cause, ApplicationError)
                    and cause.type == "GENERATION_TERMINAL_FAILURE"
                ):
                    self._state = WorkflowState.FAILED
                    return WorkflowResult(
                        self._state,
                        request.operation_id,
                        reason_code=(str(cause.details[0]) if cause.details else cause.type),
                    )
                raise
            if result.state is GenerationState.READY:
                self._state = WorkflowState.COMPLETED
                return WorkflowResult(
                    self._state, request.operation_id, result_ref=result.result_ref
                )
        self._state = WorkflowState.EXPIRED
        return WorkflowResult(
            self._state, request.operation_id, reason_code="GENERATION_DEADLINE_EXCEEDED"
        )


@workflow.defn(name="MediaProductionWorkflow")
class MediaProductionWorkflow:
    """IDs-only coordinator; images complete before dependent video jobs are attempted."""

    def __init__(self) -> None:
        self._state = WorkflowState.STARTING

    @workflow.query(name="status")
    def status(self) -> str:
        return self._state.value

    @workflow.run
    async def run(
        self, request: MediaProductionWorkflowInput
    ) -> tuple[MediaProductionJobResult, ...]:
        self._state = WorkflowState.GENERATING
        results: list[MediaProductionJobResult] = []
        for job_id in request.image_job_ids:
            result = await self._step(request, job_id)
            results.append(result)
            if result.status != "SUCCEEDED":
                self._state = WorkflowState.FAILED
                return tuple(results)
        deadline = workflow.now() + timedelta(days=3)
        for job_id in request.video_job_ids:
            result = await self._step(request, job_id)
            while result.status in {"PROCESSING", "IMPORTING"} and workflow.now() < deadline:
                await workflow.sleep(timedelta(seconds=10))
                result = await self._step(request, job_id)
            results.append(result)
            if result.status != "SUCCEEDED":
                self._state = (
                    WorkflowState.EXPIRED if workflow.now() >= deadline else WorkflowState.FAILED
                )
                return tuple(results)
        self._state = WorkflowState.COMPLETED
        return tuple(results)

    @staticmethod
    async def _step(request: MediaProductionWorkflowInput, job_id: str) -> MediaProductionJobResult:
        return cast(
            MediaProductionJobResult,
            await workflow.execute_activity(
                "workflow.execute_production_job",
                args=[
                    request.tenant_id,
                    request.production_plan_id,
                    job_id,
                    request.correlation_id,
                ],
                result_type=MediaProductionJobResult,
                start_to_close_timeout=timedelta(minutes=5),
                schedule_to_close_timeout=timedelta(minutes=10),
                retry_policy=GENERATION_RETRY_POLICY,
            ),
        )


@workflow.defn(name="FinalCreativeAssemblyWorkflow")
class FinalCreativeAssemblyWorkflow:
    """IDs-only deterministic assembly coordinator."""

    def __init__(self) -> None:
        self._state = WorkflowState.STARTING

    @workflow.query(name="status")
    def status(self) -> str:
        return self._state.value

    @workflow.run
    async def run(self, request: FinalCreativeAssemblyWorkflowInput) -> FinalCreativeAssemblyResult:
        self._state = WorkflowState.EXECUTING
        result = cast(
            FinalCreativeAssemblyResult,
            await workflow.execute_activity(
                "workflow.assemble_final_creative",
                request,
                result_type=FinalCreativeAssemblyResult,
                start_to_close_timeout=timedelta(minutes=20),
                schedule_to_close_timeout=timedelta(minutes=30),
                retry_policy=GENERATION_RETRY_POLICY,
            ),
        )
        self._state = (
            WorkflowState.COMPLETED if result.status == "SUCCEEDED" else WorkflowState.FAILED
        )
        return result


@workflow.defn(name="PublicationWorkflow")
class PublicationWorkflow:
    """IDs-only schedule, submit, and bounded reconciliation coordinator."""

    def __init__(self) -> None:
        self._state = WorkflowState.STARTING
        self._cancelled = False

    @workflow.signal(name="cancel_publication")
    def cancel_publication(self) -> None:
        self._cancelled = True

    @workflow.query(name="status")
    def status(self) -> str:
        return self._state.value

    @workflow.run
    async def run(self, request: PublicationWorkflowInput) -> PublicationWorkflowResult:
        if request.scheduled_at_epoch_seconds is not None:
            self._state = WorkflowState.SCHEDULED
            delay = request.scheduled_at_epoch_seconds - int(workflow.now().timestamp())
            if delay > 0:
                with suppress(TimeoutError):
                    await workflow.wait_condition(
                        lambda: self._cancelled, timeout=timedelta(seconds=delay)
                    )
        if self._cancelled:
            result = await self._activity("workflow.cancel_publication", request)
            self._state = WorkflowState.FAILED
            return result
        self._state = WorkflowState.EXECUTING
        result = await self._activity("workflow.submit_publication", request)
        if result.status not in {"SUBMITTED", "OUTCOME_UNKNOWN"}:
            self._state = (
                WorkflowState.COMPLETED if result.status == "PUBLISHED" else WorkflowState.FAILED
            )
            return result
        deadline = workflow.now() + timedelta(seconds=request.maximum_reconcile_seconds)
        while workflow.now() < deadline:
            await workflow.sleep(timedelta(seconds=request.reconcile_interval_seconds))
            result = await self._activity("workflow.reconcile_publication", request)
            if result.status not in {"SUBMITTED", "OUTCOME_UNKNOWN"}:
                self._state = (
                    WorkflowState.COMPLETED
                    if result.status == "PUBLISHED"
                    else WorkflowState.FAILED
                )
                return result
        self._state = WorkflowState.FAILED
        return PublicationWorkflowResult(
            request.publication_draft_id,
            "OUTCOME_UNKNOWN",
            failure_code="PUBLICATION_RECONCILIATION_DEADLINE_EXCEEDED",
        )

    @staticmethod
    async def _activity(name: str, request: PublicationWorkflowInput) -> PublicationWorkflowResult:
        return cast(
            PublicationWorkflowResult,
            await workflow.execute_activity(
                name,
                request,
                result_type=PublicationWorkflowResult,
                start_to_close_timeout=timedelta(minutes=2),
                schedule_to_close_timeout=timedelta(minutes=5),
                retry_policy=TOOL_RETRY_POLICY,
            ),
        )


@workflow.defn(name="ScheduledPublicationWorkflow")
class ScheduledPublicationWorkflow:
    def __init__(self) -> None:
        self._state = WorkflowState.STARTING
        self._approval_wakeups = 0

    @workflow.signal(name="approval_state_may_have_changed")
    def approval_state_may_have_changed(self) -> None:
        self._approval_wakeups += 1

    @workflow.query(name="status")
    def status(self) -> str:
        return self._state.value

    @workflow.run
    async def run(self, request: ToolWorkflowInput) -> WorkflowResult:
        prepared = await ApprovalBlockingWorkflow._invoke(request)
        if prepared.status != "AWAITING_APPROVAL":
            final = _tool_result(prepared)
            self._state = final.state
            return final
        self._state = WorkflowState.SCHEDULED
        await workflow.sleep(timedelta(seconds=request.schedule_delay_seconds))
        self._state = WorkflowState.EXECUTING
        current = await ApprovalBlockingWorkflow._invoke(request)
        final = _tool_result(current)
        self._state = final.state
        return final


@workflow.defn(name="PerformanceCollectionWorkflow")
class PerformanceCollectionWorkflow:
    """Finite, versioned publication measurement schedule with identifiers-only history."""

    def __init__(self) -> None:
        self._state = WorkflowState.STARTING

    @workflow.query(name="status")
    def status(self) -> str:
        return self._state.value

    @workflow.run
    async def run(self, request: MeasurementWorkflowInput) -> MeasurementActivityResult:
        self._state = WorkflowState.SCHEDULED
        result = MeasurementActivityResult(request.publication_id, None, "NO_DATA")
        previous_delay = 0
        for index, delay in enumerate(request.checkpoint_delays_seconds):
            if delay > previous_delay:
                await workflow.sleep(timedelta(seconds=delay - previous_delay))
            self._state = WorkflowState.EXECUTING
            activity_input = MeasurementActivityInput(
                request.tenant_id,
                request.publication_id,
                request.correlation_id,
                index,
            )
            result = cast(
                MeasurementActivityResult,
                await workflow.execute_activity(
                    "workflow.collect_performance",
                    activity_input,
                    result_type=MeasurementActivityResult,
                    start_to_close_timeout=timedelta(minutes=2),
                    schedule_to_close_timeout=timedelta(minutes=5),
                    retry_policy=TOOL_RETRY_POLICY,
                ),
            )
            if result.status == "FAILED":
                self._state = WorkflowState.FAILED
                return result
            previous_delay = delay
            self._state = WorkflowState.SCHEDULED
        self._state = WorkflowState.COMPLETED
        return result


@workflow.defn(name="ResearcherWorkflow")
class ResearcherWorkflow:
    def __init__(self) -> None:
        self._state = WorkflowState.STARTING

    @workflow.query(name="status")
    def status(self) -> str:
        return self._state.value

    @workflow.run
    async def run(self, request: ResearcherWorkflowInput) -> ResearcherActivityResult:
        self._state = WorkflowState.EXECUTING
        result = cast(
            ResearcherActivityResult,
            await workflow.execute_activity(
                "workflow.execute_researcher",
                request,
                result_type=ResearcherActivityResult,
                start_to_close_timeout=RESEARCHER_ACTIVITY_TIMEOUT,
                schedule_to_close_timeout=timedelta(minutes=15),
                retry_policy=RESEARCHER_RETRY_POLICY,
            ),
        )
        self._state = (
            WorkflowState.COMPLETED if result.status == "SUCCEEDED" else WorkflowState.FAILED
        )
        return result


@workflow.defn(name="AgentExecutionWorkflow")
class AgentExecutionWorkflow:
    """Future-only thin workflow; deployed Researcher history remains unchanged."""

    def __init__(self) -> None:
        self._state = WorkflowState.STARTING

    @workflow.query(name="status")
    def status(self) -> str:
        return self._state.value

    @workflow.run
    async def run(self, request: AgentExecutionWorkflowInput) -> AgentExecutionActivityResult:
        self._state = WorkflowState.EXECUTING
        result = cast(
            AgentExecutionActivityResult,
            await workflow.execute_activity(
                "workflow.execute_agent",
                request,
                result_type=AgentExecutionActivityResult,
                start_to_close_timeout=RESEARCHER_ACTIVITY_TIMEOUT,
                schedule_to_close_timeout=timedelta(minutes=15),
                retry_policy=RESEARCHER_RETRY_POLICY,
            ),
        )
        self._state = (
            WorkflowState.COMPLETED if result.status == "SUCCEEDED" else WorkflowState.FAILED
        )
        return result


@workflow.defn(name="CommerceSyncWorkflow")
class CommerceSyncWorkflow:
    """Finite IDs-only coordinator; each activity owns one cursor-bounded sync type."""

    def __init__(self) -> None:
        self._state = WorkflowState.STARTING

    @workflow.query(name="status")
    def status(self) -> str:
        return self._state.value

    @workflow.run
    async def run(
        self, request: CommerceSyncWorkflowInput
    ) -> tuple[CommerceSyncActivityResult, ...]:
        self._state = WorkflowState.EXECUTING
        results: list[CommerceSyncActivityResult] = []
        for sync_type in request.sync_types:
            cursor: str | None = None
            while True:
                result = cast(
                    CommerceSyncActivityResult,
                    await workflow.execute_activity(
                        "workflow.sync_commerce",
                        args=[request, sync_type, cursor],
                        result_type=CommerceSyncActivityResult,
                        start_to_close_timeout=timedelta(minutes=5),
                        schedule_to_close_timeout=timedelta(minutes=15),
                        retry_policy=RESEARCHER_RETRY_POLICY,
                    ),
                )
                results.append(result)
                if result.status != "SUCCEEDED":
                    self._state = WorkflowState.FAILED
                    return tuple(results)
                cursor = result.next_cursor
                if cursor is None:
                    break
        self._state = WorkflowState.COMPLETED
        return tuple(results)


@workflow.defn(name="CommerceActionWorkflow")
class CommerceActionWorkflow:
    """Approval-bound submission followed by finite reconciliation; never resubmits."""

    def __init__(self) -> None:
        self._state = WorkflowState.STARTING

    @workflow.query(name="status")
    def status(self) -> str:
        return self._state.value

    @workflow.run
    async def run(self, request: CommerceActionWorkflowInput) -> CommerceActionActivityResult:
        self._state = WorkflowState.EXECUTING
        result = await self._invoke("workflow.submit_commerce_action", request)
        if result.status != "OUTCOME_UNKNOWN":
            self._state = (
                WorkflowState.COMPLETED if result.status == "SUCCEEDED" else WorkflowState.FAILED
            )
            return result
        for _ in range(request.maximum_reconcile_attempts):
            await workflow.sleep(timedelta(seconds=request.reconcile_interval_seconds))
            result = await self._invoke("workflow.reconcile_commerce_action", request)
            if result.status != "OUTCOME_UNKNOWN":
                self._state = (
                    WorkflowState.COMPLETED
                    if result.status == "SUCCEEDED"
                    else WorkflowState.FAILED
                )
                return result
        self._state = WorkflowState.EXPIRED
        return result

    @staticmethod
    async def _invoke(
        activity_name: str, request: CommerceActionWorkflowInput
    ) -> CommerceActionActivityResult:
        return cast(
            CommerceActionActivityResult,
            await workflow.execute_activity(
                activity_name,
                request,
                result_type=CommerceActionActivityResult,
                start_to_close_timeout=timedelta(minutes=5),
                schedule_to_close_timeout=timedelta(minutes=10),
                retry_policy=TOOL_RETRY_POLICY,
            ),
        )
