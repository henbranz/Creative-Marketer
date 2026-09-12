from dataclasses import dataclass, field
from typing import Protocol, cast
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from creative_marketer.agent_runtime.application import AgentRunService
from creative_marketer.observability.ports import NullTelemetry, OperationalTelemetry
from creative_marketer.tool_execution.application import ToolGateway
from creative_marketer.tool_execution.domain import (
    GatewayResult,
    ToolInvocationRequest,
    TrustedAgentInvocation,
)
from creative_marketer.workflow_orchestration.contracts import (
    AgentExecutionActivityResult,
    AgentExecutionWorkflowInput,
    FinalCreativeAssemblyResult,
    FinalCreativeAssemblyWorkflowInput,
    GenerationPollResult,
    GenerationStartResult,
    GenerationState,
    GenerationWorkflowInput,
    MediaProductionJobResult,
    ResearcherActivityResult,
    ResearcherWorkflowInput,
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


class ProductionJobExecutor(Protocol):
    """Application service that resolves state then invokes only governed Tool Gateway tools."""

    async def execute(
        self, tenant_id: UUID, plan_id: UUID, job_id: UUID
    ) -> MediaProductionJobResult: ...


class FinalAssemblyResult(Protocol):
    id: UUID


class FinalAssemblyExecutor(Protocol):
    async def execute(self, tenant_id: UUID, job_id: UUID) -> FinalAssemblyResult: ...


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
    agent_runtime: AgentRunService | None = None
    production_jobs: ProductionJobExecutor | None = None
    assembly_jobs: FinalAssemblyExecutor | None = None

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

    @activity.defn(name="workflow.execute_researcher")
    async def execute_researcher(
        self, request: ResearcherWorkflowInput
    ) -> ResearcherActivityResult:
        if self.agent_runtime is None:
            raise ApplicationError(
                "AgentRuntime is not composed", type="AGENT_RUNTIME_UNAVAILABLE", non_retryable=True
            )
        info = activity.info()
        with self.telemetry.span(
            "temporal.activity.researcher",
            {
                "temporal.workflow_id": info.workflow_id or "unknown",
                "correlation_id": request.correlation_id,
            },
        ) as span:
            try:
                run = await self.agent_runtime.execute(
                    UUID(request.tenant_id), UUID(request.agent_run_id)
                )
            except Exception as error:
                span.record_error("RESEARCHER_ACTIVITY_FAILURE")
                raise ApplicationError(
                    "Researcher activity failed", type="RESEARCHER_ACTIVITY_FAILURE"
                ) from error
            return ResearcherActivityResult(
                str(run.id), run.status.value, run.result_ref, run.failure_code
            )

    @activity.defn(name="workflow.execute_agent")
    async def execute_agent(
        self, request: AgentExecutionWorkflowInput
    ) -> AgentExecutionActivityResult:
        if self.agent_runtime is None:
            raise ApplicationError(
                "AgentRuntime is not composed",
                type="AGENT_RUNTIME_UNAVAILABLE",
                non_retryable=True,
            )
        try:
            run = await self.agent_runtime.execute(
                UUID(request.tenant_id), UUID(request.agent_run_id)
            )
        except Exception as error:
            raise ApplicationError(
                "Agent execution failed", type="AGENT_EXECUTION_FAILURE"
            ) from error
        return AgentExecutionActivityResult(
            str(run.id), run.status.value, run.result_ref, run.failure_code
        )

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

    @activity.defn(name="workflow.execute_production_job")
    async def execute_production_job(
        self, tenant_id: str, plan_id: str, job_id: str, correlation_id: str
    ) -> MediaProductionJobResult:
        if self.production_jobs is None:
            raise ApplicationError(
                "Production job executor is not composed",
                type="PRODUCTION_EXECUTOR_UNAVAILABLE",
                non_retryable=True,
            )
        # Correlation is carried for tracing only and never supplies authority.
        UUID(correlation_id)
        return await self.production_jobs.execute(UUID(tenant_id), UUID(plan_id), UUID(job_id))

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

    @activity.defn(name="workflow.assemble_final_creative")
    async def assemble_final_creative(
        self, request: FinalCreativeAssemblyWorkflowInput
    ) -> FinalCreativeAssemblyResult:
        if self.assembly_jobs is None:
            raise ApplicationError(
                "Assembly executor is not composed",
                type="ASSEMBLY_UNAVAILABLE",
                non_retryable=True,
            )
        try:
            value = await self.assembly_jobs.execute(
                UUID(request.tenant_id), UUID(request.assembly_job_id)
            )
            final_id = value.id
        except Exception as error:
            code = str(getattr(error, "code", "ASSEMBLY_RENDER_FAILED"))
            if code in {
                "ASSEMBLY_SOURCE_RIGHTS_CHANGED",
                "ASSEMBLY_SOURCE_DIGEST_MISMATCH",
                "ASSEMBLY_OUTPUT_INVALID",
            }:
                return FinalCreativeAssemblyResult(request.assembly_job_id, None, "FAILED", code)
            raise ApplicationError("assembly activity failed", type=code) from error
        return FinalCreativeAssemblyResult(request.assembly_job_id, str(final_id), "SUCCEEDED")
