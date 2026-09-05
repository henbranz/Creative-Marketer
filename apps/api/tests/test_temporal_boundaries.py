# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,assignment,return-value"

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from temporalio.exceptions import ApplicationError

from creative_marketer.events.domain import DomainEvent, EventScopeKind
from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.infrastructure.temporal.activities import ToolGatewayWorkflowService
from creative_marketer.infrastructure.temporal.client import TemporalWorkflowSignalClient
from creative_marketer.infrastructure.temporal.worker import connect_client, main
from creative_marketer.tool_execution.domain import (
    GatewayResult,
    GatewayStatus,
    ToolInvocationRequest,
    TrustedAgentInvocation,
)
from creative_marketer.workflow_orchestration.contracts import (
    GenerationWorkflowInput,
    ToolWorkflowInput,
)
from creative_marketer.workflow_orchestration.signal_bridge import SignalApprovalWorkflow

pytestmark = pytest.mark.temporal


def valid_tool_input(**changes):
    values = {
        "tenant_id": str(uuid4()),
        "requested_agent_definition_id": str(uuid4()),
        "operation_id": "op_" + uuid4().hex,
        "tool_key": "fake.publish",
        "correlation_id": str(uuid4()),
        "request_ref": "tool-request://" + uuid4().hex,
    }
    values.update(changes)
    return ToolWorkflowInput(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant_id", "not-a-uuid"),
        ("requested_agent_definition_id", "not-a-uuid"),
        ("correlation_id", "not-a-uuid"),
        ("operation_id", "model-chosen"),
        ("tool_key", "INVALID KEY"),
        ("request_ref", "https://external.invalid/secret"),
        ("approval_timeout_seconds", 0),
        ("approval_fallback_poll_seconds", 0),
        ("schedule_delay_seconds", -1),
    ],
)
def test_tool_workflow_input_rejects_unbounded_or_untrusted_values(field, value):
    with pytest.raises(ValueError):
        valid_tool_input(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant_id", "not-a-uuid"),
        ("correlation_id", "not-a-uuid"),
        ("operation_id", "bad"),
        ("request_ref", "prompt://raw"),
        ("poll_interval_seconds", 0),
        ("maximum_generation_seconds", 0),
    ],
)
def test_generation_input_rejects_untrusted_values(field, value):
    values = {
        "tenant_id": str(uuid4()),
        "operation_id": "op_" + uuid4().hex,
        "correlation_id": str(uuid4()),
        "request_ref": "generation-request://" + uuid4().hex,
    }
    values[field] = value
    with pytest.raises(ValueError):
        GenerationWorkflowInput(**values)


def execution_context():
    user_id = uuid4()
    return ExecutionContext(
        tenant_id=uuid4(),
        actor=Actor(ActorKind.WORKLOAD, user_id),
        user_id=None,
        membership_role=None,
        membership_status=None,
        environment="test",
        authentication=AuthenticationAssurance(datetime.now(UTC), "test-workload", "internal"),
        correlation_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_gateway_activity_adapter_rejects_forged_locator_and_preserves_operation():
    request = valid_tool_input()
    context = execution_context()
    invocation = TrustedAgentInvocation(context, uuid4())
    tool_request = ToolInvocationRequest(
        request.tool_key, {"ref": request.request_ref}, request.operation_id
    )
    resolver = SimpleNamespace(resolve=AsyncMock(return_value=(invocation, tool_request)))
    gateway = SimpleNamespace(
        invoke=AsyncMock(return_value=GatewayResult(GatewayStatus.REPLAYED, request.operation_id))
    )
    adapter = ToolGatewayWorkflowService(gateway, resolver)
    with pytest.raises(ApplicationError) as error:
        await adapter.invoke(request)
    assert error.value.non_retryable
    gateway.invoke.assert_not_awaited()

    matching_context = ExecutionContext(
        tenant_id=uuid4(),
        actor=context.actor,
        user_id=None,
        membership_role=None,
        membership_status=None,
        environment="test",
        authentication=context.authentication,
        correlation_id=uuid4(),
    )
    matching_request = valid_tool_input(
        tenant_id=str(matching_context.tenant_id),
        requested_agent_definition_id=str(invocation.requested_agent_definition_id),
    )
    matching_tool_request = ToolInvocationRequest(
        matching_request.tool_key,
        {"ref": matching_request.request_ref},
        matching_request.operation_id,
    )
    matching_resolver = SimpleNamespace(
        resolve=AsyncMock(
            return_value=(
                TrustedAgentInvocation(matching_context, invocation.requested_agent_definition_id),
                matching_tool_request,
            )
        )
    )
    gateway.invoke.return_value = GatewayResult(
        GatewayStatus.EXECUTED, matching_request.operation_id, result_ref="result://safe"
    )
    result = await ToolGatewayWorkflowService(gateway, matching_resolver).invoke(matching_request)
    assert result.operation_id == matching_request.operation_id
    assert result.result_ref == "result://safe"


@pytest.mark.asyncio
async def test_signal_client_and_transactional_handler_validate_authoritative_association():
    tenant_id, approval_id = uuid4(), uuid4()
    handle = SimpleNamespace(signal=AsyncMock())
    temporal_client = SimpleNamespace(get_workflow_handle=lambda workflow_id: handle)
    client = TemporalWorkflowSignalClient(temporal_client)
    await client.signal_approval_state_changed("tenant/t/operation/op_1")
    handle.signal.assert_awaited_once_with("approval_state_may_have_changed")

    event = DomainEvent(
        "governance.approval.granted.v1",
        1,
        EventScopeKind.TENANT,
        tenant_id,
        "approval_request",
        approval_id,
        datetime.now(UTC),
        ActorKind.USER,
        uuid4(),
        uuid4(),
        {
            "approval_request_id": str(approval_id),
            "action_digest": "sha256:" + "a" * 64,
            "decided_by_user_id": str(uuid4()),
            "reason_code": None,
        },
        "sha256:" + "b" * 64,
    )
    locator = SimpleNamespace(
        workflow_id_for_approval=AsyncMock(return_value="tenant/t/operation/op_1")
    )
    signaler = SimpleNamespace(signal_approval_state_changed=AsyncMock())
    handler = SignalApprovalWorkflow(locator, signaler)
    await handler(event, SimpleNamespace())
    signaler.signal_approval_state_changed.assert_awaited_once()

    missing = SignalApprovalWorkflow(
        SimpleNamespace(workflow_id_for_approval=AsyncMock(return_value=None)), signaler
    )
    with pytest.raises(RuntimeError):
        await missing(event, SimpleNamespace())
    with pytest.raises(ValueError):
        await handler(
            SimpleNamespace(event_type="other.event.v1", scope_kind=EventScopeKind.TENANT),
            SimpleNamespace(),
        )


@pytest.mark.asyncio
async def test_client_composition_uses_temporal_otel_interceptor():
    with patch(
        "creative_marketer.infrastructure.temporal.worker.Client.connect", new_callable=AsyncMock
    ) as connect:
        connect.return_value = SimpleNamespace()
        await connect_client("localhost:7233", namespace="test")
    assert connect.await_args is not None
    assert connect.await_args.kwargs["interceptors"]


def test_worker_main_fails_closed_without_workload_identity_composition(monkeypatch):
    monkeypatch.setenv("TEMPORAL_ADDRESS", "temporal:7233")
    with pytest.raises(SystemExit, match="workload identity"):
        main()
