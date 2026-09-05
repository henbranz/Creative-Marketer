# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,assignment,return-value"

import asyncio
import base64
import os
import re
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer

from creative_marketer.approval_governance.domain import ApprovalDecision, HumanDecision
from creative_marketer.infrastructure.temporal.activities import (
    TemporalActivities,
    ToolGatewayWorkflowService,
)
from creative_marketer.infrastructure.temporal.configuration import WORKFLOW_TASK_QUEUE
from creative_marketer.infrastructure.temporal.worker import create_worker
from creative_marketer.infrastructure.temporal.workflows import (
    ApprovalBlockingWorkflow,
    MediaGenerationWorkflow,
    ScheduledPublicationWorkflow,
)
from creative_marketer.permission_governance.domain import Decision, Obligation
from creative_marketer.tool_execution.domain import ToolInvocationRequest, TrustedAgentInvocation
from creative_marketer.tool_governance.domain import RiskLevel
from creative_marketer.workflow_orchestration.contracts import (
    GenerationPollResult,
    GenerationStartResult,
    GenerationState,
    GenerationWorkflowInput,
    ToolActivityResult,
    ToolWorkflowInput,
    WorkflowState,
    generation_workflow_id,
    tool_workflow_id,
)
from tests.test_tool_gateway import (
    AGENT_ID,
    NOW,
    FakePublishExecutor,
    context,
    gateway,
    permission,
    tool,
)

pytestmark = pytest.mark.temporal


def op() -> str:
    return "op_" + uuid4().hex


def tool_input(*, schedule: int = 0, timeout: int = 600, fallback: int = 300):
    return ToolWorkflowInput(
        tenant_id=str(uuid4()),
        requested_agent_definition_id=str(uuid4()),
        operation_id=op(),
        tool_key="fake.publish",
        correlation_id=str(uuid4()),
        request_ref="tool-request://" + uuid4().hex,
        approval_timeout_seconds=timeout,
        approval_fallback_poll_seconds=fallback,
        schedule_delay_seconds=schedule,
    )


def generation_input(*, poll: int = 60, maximum: int = 600):
    return GenerationWorkflowInput(
        tenant_id=str(uuid4()),
        operation_id=op(),
        correlation_id=str(uuid4()),
        request_ref="generation-request://" + uuid4().hex,
        poll_interval_seconds=poll,
        maximum_generation_seconds=maximum,
    )


class FakeGatewayService:
    def __init__(self):
        self.approved = False
        self.revoked = False
        self.denied = False
        self.effects = 0
        self.calls = 0
        self.fail_after_effect_once = False
        self._effect_operations = set()

    async def invoke(self, request):
        self.calls += 1
        approval = str(uuid4())
        if self.denied:
            return ToolActivityResult("DENIED", request.operation_id, reason_code="POLICY_DENY")
        if self.revoked:
            return ToolActivityResult(
                "APPROVAL_INVALID",
                request.operation_id,
                approval_request_id=approval,
                reason_code="revoked",
            )
        if not self.approved:
            return ToolActivityResult(
                "AWAITING_APPROVAL", request.operation_id, approval_request_id=approval
            )
        if request.operation_id not in self._effect_operations:
            self._effect_operations.add(request.operation_id)
            self.effects += 1
            if self.fail_after_effect_once:
                self.fail_after_effect_once = False
                raise RuntimeError("response lost after effect")
            return ToolActivityResult(
                "EXECUTED", request.operation_id, result_ref="result://fake/publish"
            )
        return ToolActivityResult(
            "REPLAYED", request.operation_id, result_ref="result://fake/publish"
        )


class FakeGenerationService:
    def __init__(self, states=()):
        self.states = list(states)
        self.jobs = {}
        self.start_calls = 0
        self.poll_calls = 0
        self.transient_polls = 0

    async def start(self, request):
        self.start_calls += 1
        job = self.jobs.setdefault(request.operation_id, "provider-job://opaque-1")
        return GenerationStartResult(job)

    async def poll(self, request, provider_job_ref):
        assert self.jobs[request.operation_id] == provider_job_ref
        self.poll_calls += 1
        if self.transient_polls:
            self.transient_polls -= 1
            raise RuntimeError("temporary provider transport failure")
        state = self.states.pop(0) if self.states else GenerationState.PROCESSING
        if state is GenerationState.READY:
            return GenerationPollResult(state, "asset://opaque-1")
        if state is GenerationState.FAILED:
            return GenerationPollResult(state, failure_code="CONTENT_REJECTED")
        return GenerationPollResult(state)


async def wait_for_status(handle, expected: str) -> None:
    for _ in range(100):
        try:
            if await handle.query("status") == expected:
                return
        except Exception:
            pass
        await asyncio.sleep(0.01)
    raise AssertionError(f"workflow did not reach {expected}")


@pytest_asyncio.fixture
async def temporal_environment():
    async with await WorkflowEnvironment.start_time_skipping(
        test_server_existing_path=os.getenv("TEMPORAL_TEST_SERVER_PATH")
    ) as environment:
        yield environment


@pytest.mark.asyncio
async def test_approval_signal_is_only_a_wakeup_and_duplicates_are_safe(temporal_environment):
    gateway = FakeGatewayService()
    generation = FakeGenerationService()
    activities = TemporalActivities(gateway, generation)
    request = tool_input()
    async with create_worker(
        temporal_environment.client,
        activities,
        graceful_shutdown_timeout=timedelta(0),
    ):
        handle = await temporal_environment.client.start_workflow(
            ApprovalBlockingWorkflow.run,
            request,
            id=tool_workflow_id(request),
            task_queue=WORKFLOW_TASK_QUEUE,
        )
        await handle.signal(ApprovalBlockingWorkflow.approval_state_may_have_changed)
        await wait_for_status(handle, WorkflowState.WAITING_APPROVAL.value)
        await handle.signal(ApprovalBlockingWorkflow.approval_state_may_have_changed)
        await wait_for_status(handle, WorkflowState.WAITING_APPROVAL.value)
        assert gateway.effects == 0

        gateway.approved = True
        await handle.signal(ApprovalBlockingWorkflow.approval_state_may_have_changed)
        await handle.signal(ApprovalBlockingWorkflow.approval_state_may_have_changed)
        result = await handle.result()
        history = await handle.fetch_history()

    assert result.state is WorkflowState.COMPLETED
    assert gateway.effects == 1
    replay = await Replayer(workflows=[ApprovalBlockingWorkflow]).replay_workflow(history)
    assert replay.replay_failure is None


@pytest.mark.asyncio
async def test_approval_wait_survives_actual_worker_recreation(temporal_environment):
    gateway = FakeGatewayService()
    activities = TemporalActivities(gateway, FakeGenerationService())
    request = tool_input()
    async with create_worker(
        temporal_environment.client,
        activities,
        graceful_shutdown_timeout=timedelta(0),
        max_cached_workflows=0,
    ):
        handle = await temporal_environment.client.start_workflow(
            ApprovalBlockingWorkflow.run,
            request,
            id=tool_workflow_id(request),
            task_queue=WORKFLOW_TASK_QUEUE,
        )
        await wait_for_status(handle, WorkflowState.WAITING_APPROVAL.value)

    gateway.approved = True
    async with create_worker(temporal_environment.client, activities, max_cached_workflows=0):
        await handle.signal(ApprovalBlockingWorkflow.approval_state_may_have_changed)
        result = await handle.result()
    assert result.state is WorkflowState.COMPLETED
    assert gateway.effects == 1


@pytest.mark.asyncio
async def test_approval_fallback_and_expiry_use_durable_time(temporal_environment):
    gateway = FakeGatewayService()
    activities = TemporalActivities(gateway, FakeGenerationService())
    fallback_request = tool_input(timeout=600, fallback=60)
    async with create_worker(
        temporal_environment.client,
        activities,
        graceful_shutdown_timeout=timedelta(0),
    ):
        handle = await temporal_environment.client.start_workflow(
            ApprovalBlockingWorkflow.run,
            fallback_request,
            id=tool_workflow_id(fallback_request),
            task_queue=WORKFLOW_TASK_QUEUE,
        )
        await wait_for_status(handle, WorkflowState.WAITING_APPROVAL.value)
        gateway.approved = True
        await temporal_environment.sleep(61)
        result = await handle.result()
        assert result.state is WorkflowState.COMPLETED

        expired_gateway = FakeGatewayService()
        expired_activities = TemporalActivities(expired_gateway, FakeGenerationService())
    expired_request = tool_input(timeout=10, fallback=5)
    async with create_worker(temporal_environment.client, expired_activities):
        expired = await temporal_environment.client.execute_workflow(
            ApprovalBlockingWorkflow.run,
            expired_request,
            id=tool_workflow_id(expired_request),
            task_queue=WORKFLOW_TASK_QUEUE,
        )
    assert expired.state is WorkflowState.EXPIRED
    assert expired_gateway.effects == 0


@pytest.mark.asyncio
async def test_generation_polling_retry_restart_and_replay(temporal_environment):
    generation = FakeGenerationService(
        [GenerationState.PENDING, GenerationState.PROCESSING, GenerationState.READY]
    )
    generation.transient_polls = 1
    activities = TemporalActivities(FakeGatewayService(), generation)
    request = generation_input(poll=60)
    async with create_worker(
        temporal_environment.client,
        activities,
        graceful_shutdown_timeout=timedelta(0),
        max_cached_workflows=0,
    ):
        handle = await temporal_environment.client.start_workflow(
            MediaGenerationWorkflow.run,
            request,
            id=generation_workflow_id(request),
            task_queue=WORKFLOW_TASK_QUEUE,
        )
        await wait_for_status(handle, WorkflowState.GENERATING.value)
        await temporal_environment.sleep(61)

    async with create_worker(temporal_environment.client, activities, max_cached_workflows=0):
        result = await handle.result()
        history = await handle.fetch_history()

    assert result.state is WorkflowState.COMPLETED
    assert generation.start_calls == 1
    assert generation.poll_calls == 4
    replay = await Replayer(workflows=[MediaGenerationWorkflow]).replay_workflow(history)
    assert replay.replay_failure is None


@pytest.mark.asyncio
async def test_generation_terminal_failure_and_deadline(temporal_environment):
    failed_generation = FakeGenerationService([GenerationState.FAILED])
    failed_activities = TemporalActivities(FakeGatewayService(), failed_generation)
    failed_request = generation_input(poll=1)
    async with create_worker(temporal_environment.client, failed_activities):
        failed = await temporal_environment.client.execute_workflow(
            MediaGenerationWorkflow.run,
            failed_request,
            id=generation_workflow_id(failed_request),
            task_queue=WORKFLOW_TASK_QUEUE,
        )
    assert failed.state is WorkflowState.FAILED
    assert failed.reason_code == "CONTENT_REJECTED"
    assert failed_generation.poll_calls == 1

    pending = FakeGenerationService([GenerationState.PENDING] * 10)
    pending_activities = TemporalActivities(FakeGatewayService(), pending)
    deadline_request = generation_input(poll=5, maximum=10)
    async with create_worker(temporal_environment.client, pending_activities):
        deadline = await temporal_environment.client.execute_workflow(
            MediaGenerationWorkflow.run,
            deadline_request,
            id=generation_workflow_id(deadline_request),
            task_queue=WORKFLOW_TASK_QUEUE,
        )
    assert deadline.state is WorkflowState.EXPIRED
    assert deadline.reason_code == "GENERATION_DEADLINE_EXCEEDED"


@pytest.mark.asyncio
async def test_scheduled_publication_revalidates_revoke_and_policy(temporal_environment):
    outcomes = []
    for mode in ("approved", "revoked", "denied"):
        gateway = FakeGatewayService()
        activities = TemporalActivities(gateway, FakeGenerationService())
        request = tool_input(schedule=3600)
        async with create_worker(temporal_environment.client, activities):
            handle = await temporal_environment.client.start_workflow(
                ScheduledPublicationWorkflow.run,
                request,
                id=tool_workflow_id(request),
                task_queue=WORKFLOW_TASK_QUEUE,
            )
            await wait_for_status(handle, WorkflowState.SCHEDULED.value)
            gateway.approved = True
            if mode == "revoked":
                gateway.revoked = True
            if mode == "denied":
                gateway.denied = True
            await temporal_environment.sleep(3601)
            outcomes.append((await handle.result(), gateway.effects))

    assert outcomes[0][0].state is WorkflowState.COMPLETED
    assert outcomes[0][1] == 1
    assert outcomes[1][0].state is WorkflowState.FAILED
    assert outcomes[1][1] == 0
    assert outcomes[2][0].state is WorkflowState.DENIED
    assert outcomes[2][1] == 0


@pytest.mark.asyncio
async def test_scheduled_wait_survives_worker_recreation(temporal_environment):
    gateway_service = FakeGatewayService()
    activities = TemporalActivities(gateway_service, FakeGenerationService())
    request = tool_input(schedule=3600)
    async with create_worker(
        temporal_environment.client,
        activities,
        graceful_shutdown_timeout=timedelta(0),
        max_cached_workflows=0,
    ):
        handle = await temporal_environment.client.start_workflow(
            ScheduledPublicationWorkflow.run,
            request,
            id=tool_workflow_id(request),
            task_queue=WORKFLOW_TASK_QUEUE,
        )
        await wait_for_status(handle, WorkflowState.SCHEDULED.value)

    gateway_service.approved = True
    async with create_worker(temporal_environment.client, activities, max_cached_workflows=0):
        await temporal_environment.sleep(3601)
        result = await handle.result()
        history = await handle.fetch_history()
    assert result.state is WorkflowState.COMPLETED
    assert gateway_service.effects == 1
    replay = await Replayer(workflows=[ScheduledPublicationWorkflow]).replay_workflow(history)
    assert replay.replay_failure is None


@pytest.mark.asyncio
async def test_activity_interruption_is_retried_after_worker_recreation(temporal_environment):
    class InterruptedToolService:
        def __init__(self):
            self.started = asyncio.Event()
            self.calls = 0
            self.effects = 0

        async def invoke(self, request):
            self.calls += 1
            if self.calls == 1:
                self.started.set()
                await asyncio.Event().wait()
            self.effects += 1
            return ToolActivityResult(
                "EXECUTED", request.operation_id, result_ref="result://fake/restarted"
            )

    service = InterruptedToolService()
    activities = TemporalActivities(service, FakeGenerationService())
    request = tool_input()
    async with create_worker(
        temporal_environment.client,
        activities,
        graceful_shutdown_timeout=timedelta(0),
        max_cached_workflows=0,
    ):
        handle = await temporal_environment.client.start_workflow(
            ApprovalBlockingWorkflow.run,
            request,
            id=tool_workflow_id(request),
            task_queue=WORKFLOW_TASK_QUEUE,
        )
        await asyncio.wait_for(service.started.wait(), timeout=2)

    async with create_worker(temporal_environment.client, activities, max_cached_workflows=0):
        result = await handle.result()
    assert result.state is WorkflowState.COMPLETED
    assert service.calls == 2
    assert service.effects == 1


@pytest.mark.asyncio
async def test_activity_response_loss_retries_same_operation_once(temporal_environment):
    gateway = FakeGatewayService()
    gateway.approved = True
    gateway.fail_after_effect_once = True
    activities = TemporalActivities(gateway, FakeGenerationService())
    request = tool_input()
    async with create_worker(temporal_environment.client, activities):
        result = await temporal_environment.client.execute_workflow(
            ApprovalBlockingWorkflow.run,
            request,
            id=tool_workflow_id(request),
            task_queue=WORKFLOW_TASK_QUEUE,
        )
    assert result.state is WorkflowState.COMPLETED
    assert result.operation_id == request.operation_id
    assert gateway.calls == 2
    assert gateway.effects == 1


@pytest.mark.asyncio
async def test_temporal_activity_uses_existing_r4_gateway_and_its_idempotency(
    temporal_environment,
):
    ctx, candidate = context(), tool(risk=RiskLevel.R4)
    obligations = (
        Obligation.VALIDATE_TOOL_INPUT,
        Obligation.CHECK_IDEMPOTENCY,
        Obligation.REQUIRE_APPROVAL,
        Obligation.AUDIT_EXECUTION,
    )
    governed = permission(
        ctx, candidate, decision=Decision.REQUIRES_APPROVAL, obligations=obligations
    )
    executor = FakePublishExecutor()
    real_gateway, uows, _ = gateway(ctx, candidate, governed, executor)
    operation_id = op()
    tool_request = ToolInvocationRequest(candidate.tool_key, {"value": "publish"}, operation_id)
    invocation = TrustedAgentInvocation(ctx, AGENT_ID)

    class Resolver:
        async def resolve(self, _request):
            return invocation, tool_request

    service = ToolGatewayWorkflowService(real_gateway, Resolver())

    class LoseFirstSuccessfulResponse:
        lost = False

        async def invoke(self, request):
            result = await service.invoke(request)
            if result.status == "EXECUTED" and not self.lost:
                self.lost = True
                raise RuntimeError("activity response lost")
            return result

    activity_service = LoseFirstSuccessfulResponse()
    activities = TemporalActivities(activity_service, FakeGenerationService())
    request = ToolWorkflowInput(
        tenant_id=str(ctx.tenant_id),
        requested_agent_definition_id=str(AGENT_ID),
        operation_id=operation_id,
        tool_key=candidate.tool_key,
        correlation_id=str(ctx.correlation_id),
        request_ref="tool-request://" + uuid4().hex,
    )
    async with create_worker(temporal_environment.client, activities):
        handle = await temporal_environment.client.start_workflow(
            ApprovalBlockingWorkflow.run,
            request,
            id=tool_workflow_id(request),
            task_queue=WORKFLOW_TASK_QUEUE,
        )
        await wait_for_status(handle, WorkflowState.WAITING_APPROVAL.value)
        approval_id = next(iter(uows.state["requests"]))
        uows.state["decisions"][approval_id] = ApprovalDecision(
            approval_id,
            ctx.tenant_id,
            HumanDecision.APPROVE,
            ctx.user_id,
            "user",
            NOW,
        )
        await handle.signal(ApprovalBlockingWorkflow.approval_state_may_have_changed)
        result = await handle.result()

    assert result.state is WorkflowState.COMPLETED
    assert executor.effects == 1
    assert activity_service.lost


@pytest.mark.asyncio
async def test_cancellation_waiting_approval_and_generation_is_safe(temporal_environment):
    gateway = FakeGatewayService()
    generation = FakeGenerationService()
    activities = TemporalActivities(gateway, generation)
    async with create_worker(temporal_environment.client, activities):
        approval_request = tool_input()
        approval = await temporal_environment.client.start_workflow(
            ApprovalBlockingWorkflow.run,
            approval_request,
            id=tool_workflow_id(approval_request),
            task_queue=WORKFLOW_TASK_QUEUE,
        )
        await wait_for_status(approval, WorkflowState.WAITING_APPROVAL.value)
        await approval.cancel()
        with pytest.raises(WorkflowFailureError):
            await approval.result()

        media_request = generation_input(poll=300)
        media = await temporal_environment.client.start_workflow(
            MediaGenerationWorkflow.run,
            media_request,
            id=generation_workflow_id(media_request),
            task_queue=WORKFLOW_TASK_QUEUE,
        )
        await wait_for_status(media, WorkflowState.GENERATING.value)
        await media.cancel()
        with pytest.raises(WorkflowFailureError):
            await media.result()
    assert gateway.effects == 0
    assert generation.start_calls == 1


@pytest.mark.asyncio
async def test_cancellation_after_tool_effect_does_not_claim_rollback(temporal_environment):
    class EffectStartedService:
        def __init__(self):
            self.started = asyncio.Event()
            self.effects = 0

        async def invoke(self, request):
            self.effects += 1
            self.started.set()
            await asyncio.Event().wait()
            return ToolActivityResult("EXECUTED", request.operation_id)

    service = EffectStartedService()
    request = tool_input()
    activities = TemporalActivities(service, FakeGenerationService())
    async with create_worker(
        temporal_environment.client,
        activities,
        graceful_shutdown_timeout=timedelta(0),
    ):
        handle = await temporal_environment.client.start_workflow(
            ApprovalBlockingWorkflow.run,
            request,
            id=tool_workflow_id(request),
            task_queue=WORKFLOW_TASK_QUEUE,
        )
        await asyncio.wait_for(service.started.wait(), timeout=2)
        await handle.cancel()
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    assert service.effects == 1


@pytest.mark.asyncio
async def test_temporal_history_contains_only_safe_references(temporal_environment):
    gateway = FakeGatewayService()
    gateway.approved = True
    request = tool_input()
    async with create_worker(
        temporal_environment.client, TemporalActivities(gateway, FakeGenerationService())
    ):
        handle = await temporal_environment.client.start_workflow(
            ApprovalBlockingWorkflow.run,
            request,
            id=tool_workflow_id(request),
            task_queue=WORKFLOW_TASK_QUEUE,
        )
        await handle.result()
        history_json = (await handle.fetch_history()).to_json()
        decoded_payloads = "\n".join(
            base64.b64decode(value).decode("utf-8", errors="replace")
            for value in re.findall(r'"data": "([A-Za-z0-9+/=]+)"', history_json)
        )
        history_text = (history_json + decoded_payloads).lower()
    for forbidden in (
        "authorization",
        "oauth",
        "password",
        "customer@example",
        "+1555",
        "raw_input",
        "raw_output",
        "prompt",
        "provider response",
    ):
        assert forbidden not in history_text
    assert request.operation_id in history_text
