from dataclasses import dataclass, field
from typing import Protocol, cast

from temporalio import activity
from temporalio.exceptions import ApplicationError

from creative_marketer.observability.ports import NullTelemetry, OperationalTelemetry
from creative_marketer.tool_execution.application import ToolGateway
from creative_marketer.tool_execution.domain import (
    GatewayResult,
    ToolInvocationRequest,
    TrustedAgentInvocation,
)
from creative_marketer.workflow_orchestration.contracts import (
    GenerationPollResult,
    GenerationStartResult,
    GenerationState,
    GenerationWorkflowInput,
    ToolActivityResult,
    ToolWorkflowInput,
)


class TrustedWorkflowToolRequestResolver(Protocol):
    """Resolve an orchestration locator into current trusted identity and request state."""

    async def resolve(
        self, request: ToolWorkflowInput
    ) -> tuple[TrustedAgentInvocation, ToolInvocationRequest]: ...


class GenerationApplicationService(Protocol):
    async def start(self, request: GenerationWorkflowInput) -> GenerationStartResult: ...

    async def poll(
        self, request: GenerationWorkflowInput, provider_job_ref: str
    ) -> GenerationPollResult: ...


@dataclass(slots=True)
class ToolGatewayWorkflowService:
    """Application-facing adapter; the existing Tool Gateway remains Temporal-unaware."""

    gateway: ToolGateway
    resolver: TrustedWorkflowToolRequestResolver

    async def invoke(self, request: ToolWorkflowInput) -> ToolActivityResult:
        invocation, tool_request = await self.resolver.resolve(request)
        if (
            str(invocation.initiating_context.tenant_id) != request.tenant_id
            or str(invocation.requested_agent_definition_id)
            != request.requested_agent_definition_id
            or tool_request.operation_id != request.operation_id
            or tool_request.tool_key != request.tool_key
        ):
            raise ApplicationError(
                "trusted workflow request does not match orchestration locator",
                type="WORKFLOW_CONTEXT_INVALID",
                non_retryable=True,
            )
        result = await self.gateway.invoke(invocation, tool_request)
        return _tool_result(result)


class WorkflowToolService(Protocol):
    async def invoke(self, request: ToolWorkflowInput) -> ToolActivityResult: ...


def _tool_result(result: GatewayResult) -> ToolActivityResult:
    return ToolActivityResult(
        status=result.status.value,
        operation_id=result.operation_id,
        approval_request_id=(
            str(result.approval_request_id) if result.approval_request_id is not None else None
        ),
        result_ref=result.result_ref,
        reason_code=result.reason_code,
    )


@dataclass(slots=True)
class TemporalActivities:
    tool_service: WorkflowToolService
    generation_service: GenerationApplicationService
    telemetry: OperationalTelemetry = field(default_factory=NullTelemetry)

    @activity.defn(name="workflow.invoke_tool")
    async def invoke_tool(self, request: ToolWorkflowInput) -> ToolActivityResult:
        info = activity.info()
        with self.telemetry.span(
            "temporal.activity.tool_gateway",
            {
                "temporal.workflow_id": info.workflow_id or "unknown",
                "temporal.run_id": info.workflow_run_id or "unknown",
                "correlation_id": request.correlation_id,
            },
        ) as span:
            if info.attempt > 1:
                self.telemetry.count("activity.retries", attributes={"activity": "tool_gateway"})
            try:
                return await self.tool_service.invoke(request)
            except ApplicationError:
                raise
            except Exception as error:
                span.record_error("TOOL_ACTIVITY_TRANSIENT")
                raise ApplicationError(
                    "tool activity transient failure", type="TRANSIENT"
                ) from error

    @activity.defn(name="workflow.start_generation")
    async def start_generation(self, request: GenerationWorkflowInput) -> GenerationStartResult:
        return cast(GenerationStartResult, await self._generation_call("start", request))

    @activity.defn(name="workflow.poll_generation")
    async def poll_generation(
        self, request: GenerationWorkflowInput, provider_job_ref: str
    ) -> GenerationPollResult:
        result = await self._generation_call("poll", request, provider_job_ref)
        assert isinstance(result, GenerationPollResult)
        if result.state is GenerationState.FAILED:
            raise ApplicationError(
                "generation provider returned a terminal failure",
                result.failure_code or "GENERATION_FAILED",
                type="GENERATION_TERMINAL_FAILURE",
                non_retryable=True,
            )
        return result

    async def _generation_call(
        self,
        operation: str,
        request: GenerationWorkflowInput,
        provider_job_ref: str | None = None,
    ) -> GenerationStartResult | GenerationPollResult:
        info = activity.info()
        with self.telemetry.span(
            f"temporal.activity.generation_{operation}",
            {
                "temporal.workflow_id": info.workflow_id or "unknown",
                "temporal.run_id": info.workflow_run_id or "unknown",
                "correlation_id": request.correlation_id,
            },
        ) as span:
            if info.attempt > 1:
                self.telemetry.count(
                    "activity.retries", attributes={"activity": f"generation_{operation}"}
                )
            try:
                if operation == "start":
                    return await self.generation_service.start(request)
                assert provider_job_ref is not None
                return await self.generation_service.poll(request, provider_job_ref)
            except ApplicationError:
                raise
            except Exception as error:
                span.record_error("GENERATION_ACTIVITY_TRANSIENT")
                raise ApplicationError(
                    "generation activity transient failure", type="TRANSIENT"
                ) from error
