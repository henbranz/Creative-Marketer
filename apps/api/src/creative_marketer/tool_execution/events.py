from datetime import datetime

from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import DomainEvent, tenant_event
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.tool_execution.domain import ToolCall, ToolCallStatus


def tool_outcome_event(
    context: ExecutionContext,
    call: ToolCall,
    contracts: EventContractRegistry,
    occurred_at: datetime,
) -> DomainEvent:
    suffix = {
        ToolCallStatus.SUCCEEDED: "execution_succeeded",
        ToolCallStatus.FAILED_PRE_EFFECT: "execution_failed",
        ToolCallStatus.UNKNOWN_EXTERNAL_OUTCOME: "execution_outcome_unknown",
    }[call.status]
    event_type = f"governance.tool.{suffix}.v1"
    payload: dict[str, object] = {
        "tool_call_id": str(call.id),
        "tool_definition_id": str(call.binding.tool_definition_id),
        "tool_version_id": str(call.binding.tool_version_id),
        "operation_id": call.operation_id,
    }
    if call.status is ToolCallStatus.SUCCEEDED:
        payload.update(
            requested_agent_definition_id=str(call.binding.requested_agent_definition_id),
            agent_version_id=str(call.binding.agent_version_id),
            risk_level=call.binding.risk_level.value,
            result_ref=call.result_ref,
        )
    else:
        payload["error_code"] = call.error_code
    return tenant_event(
        context,
        event_type=event_type,
        schema_version=1,
        aggregate_type="tool_call",
        aggregate_id=call.id,
        occurred_at=occurred_at,
        payload=payload,
        payload_schema_digest=contracts.schema_digest(event_type),
    )
