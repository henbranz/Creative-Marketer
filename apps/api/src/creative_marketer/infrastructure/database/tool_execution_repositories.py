from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.action_binding import ActionBindingV1
from creative_marketer.infrastructure.database.tool_execution_schema import tool_calls
from creative_marketer.tool_execution.domain import ExternalOutcome, ToolCall, ToolCallStatus
from creative_marketer.tool_governance.domain import RiskLevel


def _tool_call(row: object) -> ToolCall:
    data = row._mapping  # type: ignore[attr-defined]
    binding = ActionBindingV1(
        tenant_id=data["tenant_id"],
        requested_agent_definition_id=data["requested_agent_definition_id"],
        resolved_agent_definition_id=data["resolved_agent_definition_id"],
        agent_version_id=data["agent_version_id"],
        agent_configuration_digest=data["agent_configuration_digest"],
        tool_definition_id=data["tool_definition_id"],
        tool_version_id=data["tool_version_id"],
        tool_configuration_digest=data["tool_configuration_digest"],
        tool_key=data["tool_key"],
        risk_level=RiskLevel(data["risk_level"]),
        permission_id=data["permission_id"],
        permission_version_id=data["permission_version_id"],
        permission_configuration_digest=data["permission_configuration_digest"],
        permission_engine_version=data["permission_engine_version"],
        scope_request_digest=data["scope_request_digest"],
        resource_type=data["resource_type"],
        resource_id=data["resource_id"],
        environment=data["environment"],
        normalized_input_digest=data["normalized_input_digest"],
        idempotency_key=data["operation_id"],
        canonicalization_version=data["canonicalization_version"],
    )
    if binding.action_digest != data["action_digest"]:
        raise ValueError("persisted ToolCall action digest mismatch")
    return ToolCall(
        id=data["id"],
        binding=binding,
        correlation_id=data["correlation_id"],
        status=ToolCallStatus(data["status"]),
        external_outcome=ExternalOutcome(data["external_outcome"]),
        approval_request_id=data["approval_request_id"],
        idempotency_record_id=data["idempotency_record_id"],
        attempt_id=data["attempt_id"],
        result_ref=data["result_ref"],
        error_code=data["error_code"],
        created_at=data["created_at"],
        started_at=data["started_at"],
        completed_at=data["completed_at"],
        updated_at=data["updated_at"],
    )


def _values(call: ToolCall) -> dict[str, object]:
    binding = call.binding
    return {
        "id": call.id,
        "tenant_id": binding.tenant_id,
        "requested_agent_definition_id": binding.requested_agent_definition_id,
        "resolved_agent_definition_id": binding.resolved_agent_definition_id,
        "agent_version_id": binding.agent_version_id,
        "agent_configuration_digest": binding.agent_configuration_digest,
        "tool_definition_id": binding.tool_definition_id,
        "tool_version_id": binding.tool_version_id,
        "tool_configuration_digest": binding.tool_configuration_digest,
        "tool_key": binding.tool_key,
        "risk_level": binding.risk_level.value,
        "permission_id": binding.permission_id,
        "permission_version_id": binding.permission_version_id,
        "permission_configuration_digest": binding.permission_configuration_digest,
        "permission_engine_version": binding.permission_engine_version,
        "scope_request_digest": binding.scope_request_digest,
        "resource_type": binding.resource_type,
        "resource_id": binding.resource_id,
        "environment": binding.environment,
        "normalized_input_digest": binding.normalized_input_digest,
        "operation_id": binding.idempotency_key,
        "action_digest": binding.action_digest,
        "canonicalization_version": binding.canonicalization_version,
        "approval_request_id": call.approval_request_id,
        "idempotency_record_id": call.idempotency_record_id,
        "attempt_id": call.attempt_id,
        "status": call.status.value,
        "external_outcome": call.external_outcome.value,
        "result_ref": call.result_ref,
        "error_code": call.error_code,
        "correlation_id": call.correlation_id,
        "created_at": call.created_at,
        "started_at": call.started_at,
        "completed_at": call.completed_at,
        "updated_at": call.updated_at,
    }


class SqlAlchemyToolCallRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve(self, call: ToolCall) -> tuple[ToolCall, bool]:
        statement = (
            insert(tool_calls)
            .values(**_values(call))
            .on_conflict_do_nothing(
                index_elements=[
                    tool_calls.c.tenant_id,
                    tool_calls.c.tool_definition_id,
                    tool_calls.c.operation_id,
                ]
            )
            .returning(tool_calls)
        )
        inserted = (await self._session.execute(statement)).first()
        if inserted is not None:
            return _tool_call(inserted), True
        row = (
            await self._session.execute(
                select(tool_calls).where(
                    tool_calls.c.tool_definition_id == call.binding.tool_definition_id,
                    tool_calls.c.operation_id == call.operation_id,
                )
            )
        ).first()
        if row is None:
            raise RuntimeError("conflicting ToolCall is not visible")
        return _tool_call(row), False

    async def get(self, call_id: UUID, *, for_update: bool = False) -> ToolCall | None:
        query = select(tool_calls).where(tool_calls.c.id == call_id)
        if for_update:
            query = query.with_for_update()
        row = (await self._session.execute(query)).first()
        return None if row is None else _tool_call(row)

    async def update(self, call: ToolCall) -> None:
        await self._session.execute(
            update(tool_calls)
            .where(tool_calls.c.id == call.id)
            .values(
                approval_request_id=call.approval_request_id,
                idempotency_record_id=call.idempotency_record_id,
                attempt_id=call.attempt_id,
                status=call.status.value,
                external_outcome=call.external_outcome.value,
                result_ref=call.result_ref,
                error_code=call.error_code,
                started_at=call.started_at,
                completed_at=call.completed_at,
                updated_at=call.updated_at,
            )
        )
