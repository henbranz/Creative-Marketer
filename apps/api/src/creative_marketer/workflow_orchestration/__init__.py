"""Provider-neutral contracts used by durable workflow adapters."""

from creative_marketer.workflow_orchestration.contracts import (
    GenerationState,
    GenerationWorkflowInput,
    ToolActivityResult,
    ToolWorkflowInput,
    WorkflowResult,
    WorkflowState,
    generation_workflow_id,
    tool_workflow_id,
)
from creative_marketer.workflow_orchestration.signal_bridge import SignalApprovalWorkflow

__all__ = [
    "GenerationState",
    "GenerationWorkflowInput",
    "SignalApprovalWorkflow",
    "ToolActivityResult",
    "ToolWorkflowInput",
    "WorkflowResult",
    "WorkflowState",
    "generation_workflow_id",
    "tool_workflow_id",
]
