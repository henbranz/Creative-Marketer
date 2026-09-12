import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

_OPERATION_PATTERN = re.compile(r"op_[0-9a-f]{32}")
_REFERENCE_PATTERN = re.compile(r"(?:tool-request|generation-request)://[0-9a-f]{32}")
_TOOL_KEY_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,99}")


class WorkflowState(StrEnum):
    STARTING = "STARTING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    GENERATING = "GENERATING"
    SCHEDULED = "SCHEDULED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    DENIED = "DENIED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class GenerationState(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


def _uuid(value: str, field_name: str) -> None:
    try:
        UUID(value)
    except (ValueError, TypeError) as error:
        raise ValueError(f"{field_name} must be a UUID") from error


@dataclass(frozen=True, slots=True)
class ToolWorkflowInput:
    tenant_id: str
    requested_agent_definition_id: str
    operation_id: str
    tool_key: str
    correlation_id: str
    request_ref: str
    approval_timeout_seconds: int = 86_400
    approval_fallback_poll_seconds: int = 300
    schedule_delay_seconds: int = 0

    def __post_init__(self) -> None:
        _uuid(self.tenant_id, "tenant_id")
        _uuid(self.requested_agent_definition_id, "requested_agent_definition_id")
        _uuid(self.correlation_id, "correlation_id")
        if _OPERATION_PATTERN.fullmatch(self.operation_id) is None:
            raise ValueError("operation_id must be a platform-generated op_<uuid> value")
        if _TOOL_KEY_PATTERN.fullmatch(self.tool_key) is None:
            raise ValueError("tool_key is invalid")
        if _REFERENCE_PATTERN.fullmatch(self.request_ref) is None:
            raise ValueError("request_ref must be an opaque internal request reference")
        if not 1 <= self.approval_timeout_seconds <= 604_800:
            raise ValueError("approval timeout must be between one second and seven days")
        if not 1 <= self.approval_fallback_poll_seconds <= 3_600:
            raise ValueError("approval fallback poll must be between one second and one hour")
        if not 0 <= self.schedule_delay_seconds <= 31_536_000:
            raise ValueError("schedule delay must be at most one year")


@dataclass(frozen=True, slots=True)
class GenerationWorkflowInput:
    tenant_id: str
    operation_id: str
    correlation_id: str
    request_ref: str
    poll_interval_seconds: int = 60
    maximum_generation_seconds: int = 21_600

    def __post_init__(self) -> None:
        _uuid(self.tenant_id, "tenant_id")
        _uuid(self.correlation_id, "correlation_id")
        if _OPERATION_PATTERN.fullmatch(self.operation_id) is None:
            raise ValueError("operation_id must be a platform-generated op_<uuid> value")
        if _REFERENCE_PATTERN.fullmatch(self.request_ref) is None:
            raise ValueError("request_ref must be an opaque internal request reference")
        if not 1 <= self.poll_interval_seconds <= 3_600:
            raise ValueError("poll interval must be between one second and one hour")
        if not 1 <= self.maximum_generation_seconds <= 604_800:
            raise ValueError("generation deadline must be at most seven days")


@dataclass(frozen=True, slots=True)
class ToolActivityResult:
    status: str
    operation_id: str
    approval_request_id: str | None = None
    result_ref: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class GenerationStartResult:
    provider_job_ref: str


@dataclass(frozen=True, slots=True)
class GenerationPollResult:
    state: GenerationState
    result_ref: str | None = None
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    state: WorkflowState
    operation_id: str
    result_ref: str | None = None
    approval_request_id: str | None = None
    reason_code: str | None = None


def tool_workflow_id(value: ToolWorkflowInput) -> str:
    return f"tenant/{value.tenant_id}/operation/{value.operation_id}"


def generation_workflow_id(value: GenerationWorkflowInput) -> str:
    return f"tenant/{value.tenant_id}/generation/{value.operation_id}"


@dataclass(frozen=True, slots=True)
class ResearcherWorkflowInput:
    tenant_id: str
    agent_run_id: str
    correlation_id: str

    def __post_init__(self) -> None:
        _uuid(self.tenant_id, "tenant_id")
        _uuid(self.agent_run_id, "agent_run_id")
        _uuid(self.correlation_id, "correlation_id")


@dataclass(frozen=True, slots=True)
class ResearcherActivityResult:
    agent_run_id: str
    status: str
    result_ref: str | None = None
    failure_code: str | None = None


def researcher_workflow_id(value: ResearcherWorkflowInput) -> str:
    return f"tenant/{value.tenant_id}/agent-run/{value.agent_run_id}"


@dataclass(frozen=True, slots=True)
class AgentExecutionWorkflowInput:
    tenant_id: str
    agent_run_id: str
    correlation_id: str

    def __post_init__(self) -> None:
        _uuid(self.tenant_id, "tenant_id")
        _uuid(self.agent_run_id, "agent_run_id")
        _uuid(self.correlation_id, "correlation_id")


@dataclass(frozen=True, slots=True)
class AgentExecutionActivityResult:
    agent_run_id: str
    status: str
    result_ref: str | None = None
    failure_code: str | None = None


def agent_execution_workflow_id(value: AgentExecutionWorkflowInput) -> str:
    return f"tenant/{value.tenant_id}/agent-run/{value.agent_run_id}"


@dataclass(frozen=True, slots=True)
class MediaProductionWorkflowInput:
    tenant_id: str
    production_plan_id: str
    image_job_ids: tuple[str, ...]
    video_job_ids: tuple[str, ...]
    correlation_id: str

    def __post_init__(self) -> None:
        _uuid(self.tenant_id, "tenant_id")
        _uuid(self.production_plan_id, "production_plan_id")
        _uuid(self.correlation_id, "correlation_id")
        for job_id in (*self.image_job_ids, *self.video_job_ids):
            _uuid(job_id, "generation_job_id")
        if len(self.image_job_ids) > 8 or len(self.video_job_ids) > 4:
            raise ValueError("production job set exceeds bounded plan limits")


@dataclass(frozen=True, slots=True)
class MediaProductionJobResult:
    generation_job_id: str
    status: str
    failure_code: str | None = None


def media_production_workflow_id(value: MediaProductionWorkflowInput) -> str:
    return f"tenant/{value.tenant_id}/production-plan/{value.production_plan_id}"


@dataclass(frozen=True, slots=True)
class FinalCreativeAssemblyWorkflowInput:
    tenant_id: str
    assembly_plan_id: str
    assembly_job_id: str
    correlation_id: str

    def __post_init__(self) -> None:
        _uuid(self.tenant_id, "tenant_id")
        _uuid(self.assembly_plan_id, "assembly_plan_id")
        _uuid(self.assembly_job_id, "assembly_job_id")
        _uuid(self.correlation_id, "correlation_id")


@dataclass(frozen=True, slots=True)
class FinalCreativeAssemblyResult:
    assembly_job_id: str
    final_creative_id: str | None
    status: str
    failure_code: str | None = None


def final_creative_assembly_workflow_id(value: FinalCreativeAssemblyWorkflowInput) -> str:
    return f"tenant/{value.tenant_id}/assembly-plan/{value.assembly_plan_id}"
