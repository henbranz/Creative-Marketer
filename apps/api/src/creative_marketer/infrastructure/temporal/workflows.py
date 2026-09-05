from contextlib import suppress
from datetime import timedelta
from typing import cast

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from creative_marketer.infrastructure.temporal.configuration import (
        GENERATION_ACTIVITY_TIMEOUT,
        GENERATION_RETRY_POLICY,
        TOOL_ACTIVITY_TIMEOUT,
        TOOL_RETRY_POLICY,
    )
    from creative_marketer.workflow_orchestration.contracts import (
        GenerationPollResult,
        GenerationStartResult,
        GenerationState,
        GenerationWorkflowInput,
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
