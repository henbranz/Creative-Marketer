# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,assignment,return-value"

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from creative_marketer.action_binding import NormalizedToolInput
from creative_marketer.approval_governance.domain import ApprovalDecision, HumanDecision
from creative_marketer.execution_control.domain import ReconciliationOutcome, reconcile
from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.permission_governance.domain import (
    Decision,
    DecisionReason,
    Obligation,
    PermissionDecision,
    TrustedScopeRequirements,
)
from creative_marketer.tool_execution.application import (
    ResourceAccessDenied,
    ResourceResolution,
    ToolExecutionBinding,
    ToolExecutionBindingRegistry,
    ToolGateway,
)
from creative_marketer.tool_execution.domain import (
    GatewayStatus,
    OutcomeUnknown,
    PreEffectFailure,
    ToolCallStatus,
    ToolExecutorResult,
    ToolInvocationRequest,
    TrustedAgentInvocation,
)
from creative_marketer.tool_governance.domain import (
    CredentialBoundary,
    ExecutionClass,
    IdempotencyRequirement,
    ResolvedToolVersion,
    RiskLevel,
    SideEffectClass,
)
from creative_marketer.tool_governance.schema_validation import validate_contract_schema

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def context():
    user = uuid4()
    return ExecutionContext(
        uuid4(),
        Actor(ActorKind.USER, user),
        user,
        MembershipRole.OWNER,
        MembershipStatus.ACTIVE,
        "test",
        AuthenticationAssurance(NOW, "test", "mfa"),
        uuid4(),
    )


def schema(properties, required=()):
    return validate_contract_schema(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": list(required),
        }
    )


def tool(*, risk=RiskLevel.R0, credential=CredentialBoundary.NONE):
    return ResolvedToolVersion(
        uuid4(),
        uuid4(),
        1,
        "fake.read",
        risk,
        SideEffectClass.READ_ONLY,
        ExecutionClass.INTERNAL,
        credential,
        IdempotencyRequirement.REQUIRED,
        schema({"value": {"type": "string"}}, ("value",)),
        schema({"ok": {"type": "boolean"}}, ("ok",)),
        (),
        DIGEST,
    )


def permission(ctx, candidate, *, decision=Decision.ALLOW, obligations=None):
    return PermissionDecision(
        decision,
        DecisionReason.ALLOWED
        if decision is Decision.ALLOW
        else (
            DecisionReason.APPROVAL_REQUIRED
            if decision is Decision.REQUIRES_APPROVAL
            else DecisionReason.PERMISSION_EXPLICITLY_DENIED
        ),
        ctx.tenant_id,
        ctx.actor.kind.value,
        ctx.actor.id,
        AGENT_ID,
        AGENT_ID,
        AGENT_VERSION,
        DIGEST,
        candidate.definition_id,
        candidate.version_id,
        candidate.configuration_digest,
        candidate.tool_key,
        candidate.risk_level,
        PERMISSION_ID,
        PERMISSION_VERSION,
        DIGEST,
        TrustedScopeRequirements(explicitly_unscoped=True).digest,
        ctx.environment,
        obligations
        or (
            Obligation.VALIDATE_TOOL_INPUT,
            Obligation.CHECK_IDEMPOTENCY,
            Obligation.AUDIT_EXECUTION,
        ),
        ctx.correlation_id,
    )


AGENT_ID, AGENT_VERSION, PERMISSION_ID, PERMISSION_VERSION = (uuid4() for _ in range(4))


class Repo:
    def __init__(self, state, name):
        self.state, self.name = state, name

    async def add(self, value):
        self.state[self.name][value.id] = value

    async def append(self, value):
        self.state[self.name].append(value)

    async def get(self, key, **kwargs):
        return self.state[self.name].get(key)

    async def update(self, value):
        self.state[self.name][value.id] = value

    async def get_by_key(self, tool_id, operation):
        return next(
            (
                x
                for x in self.state[self.name].values()
                if x.tool_definition_id == tool_id and x.idempotency_key == operation
            ),
            None,
        )

    async def reserve(self, value):
        items = self.state[self.name]
        if self.name == "calls":
            old = next(
                (
                    x
                    for x in items.values()
                    if x.binding.tool_definition_id == value.binding.tool_definition_id
                    and x.operation_id == value.operation_id
                ),
                None,
            )
        else:
            old = next(
                (
                    x
                    for x in items.values()
                    if x.tool_definition_id == value.tool_definition_id
                    and x.idempotency_key == value.idempotency_key
                ),
                None,
            )
        if old:
            return old, False
        items[value.id] = value
        return value, True


class MemoryUow:
    def __init__(self, factory):
        self.factory, self.state = factory, factory.state
        self.tool_calls, self.idempotency = Repo(self.state, "calls"), Repo(self.state, "idem")
        self.requests, self.decisions, self.revocations = (
            Repo(self.state, x) for x in ("requests", "decisions", "revocations")
        )
        self.audit, self.outbox = Repo(self.state, "audits"), Repo(self.state, "events")

    async def __aenter__(self):
        self.before = {
            key: value.copy() if isinstance(value, (dict, list)) else value
            for key, value in self.state.items()
        }
        self.committed = False
        return self

    async def __aexit__(self, *args):
        if not self.committed:
            self.state.clear()
            self.state.update(self.before)
        return None

    async def commit(self):
        if self.factory.commit_failures and self.factory.commit_failures.pop(0):
            raise RuntimeError("injected commit failure")
        self.state["commits"] += 1
        self.committed = True

    async def verify_authorization_snapshot(self, call):
        return self.state["authorized"]


class UowFactory:
    def __init__(self):
        self.commit_failures = []
        self.state = {
            "calls": {},
            "idem": {},
            "requests": {},
            "decisions": {},
            "revocations": {},
            "audits": [],
            "events": [],
            "commits": 0,
            "authorized": True,
        }

    def __call__(self, tenant):
        return MemoryUow(self)


class Resolver:
    def __init__(self, value):
        self.value = value

    async def __call__(self, *args):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class Evaluator:
    def __init__(self, value):
        self.value = value

    async def __call__(self, *args):
        return self.value


class ResourceResolver:
    async def __call__(self, *args):
        return ResourceResolution(TrustedScopeRequirements(explicitly_unscoped=True))


class DeniedResourceResolver:
    async def __call__(self, *args):
        raise ResourceAccessDenied


class FakeReadExecutor:
    def __init__(self, outcomes=None, output=None):
        self.outcomes, self.count = list(outcomes or []), 0
        self.output = {"ok": True} if output is None else output

    async def execute(self, execution_context, normalized):
        self.count += 1
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome:
            raise outcome
        return ToolExecutorResult(self.output, "result://fake/1")


class FakePublishExecutor(FakeReadExecutor):
    def __init__(self, outcomes=None):
        super().__init__(outcomes)
        self.effects = 0

    async def execute(self, execution_context, normalized):
        self.count += 1
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, PreEffectFailure):
            raise outcome
        self.effects += 1
        if outcome:
            raise outcome
        return ToolExecutorResult({"ok": True}, "result://fake/publish-1")


class Budget:
    def __init__(self, allowed):
        self.allowed = allowed

    async def authorize(self, *args):
        return self.allowed


def gateway(
    ctx,
    candidate,
    decision,
    executor=None,
    *,
    binding=True,
    budget=None,
    credential_capable=False,
    resource_resolver=None,
):
    uows, executor = UowFactory(), executor or FakeReadExecutor()
    bindings = (
        ()
        if not binding
        else (
            ToolExecutionBinding(
                candidate.definition_id,
                candidate.version_id,
                NormalizedToolInput.from_trusted_value,
                resource_resolver or ResourceResolver(),
                executor,
                credential_capable,
            ),
        )
    )
    return (
        ToolGateway(
            Resolver(
                SimpleNamespace(
                    requested_tenant_definition_id=AGENT_ID,
                    resolved_definition_id=AGENT_ID,
                    version_id=AGENT_VERSION,
                )
            ),
            Resolver(candidate),
            Evaluator(decision),
            ToolExecutionBindingRegistry(bindings),
            uows,
            budget,
            clock=lambda: NOW,
        ),
        uows,
        executor,
    )


@pytest.mark.asyncio
async def test_success_replay_conflict_and_audit_event_evidence():
    ctx, candidate = context(), tool()
    service, uows, executor = gateway(ctx, candidate, permission(ctx, candidate))
    invocation = TrustedAgentInvocation(ctx, AGENT_ID)
    request = ToolInvocationRequest("fake.read", {"value": "hello"}, "op_" + "1" * 32)
    first = await service.invoke(invocation, request)
    assert first.status is GatewayStatus.EXECUTED and first.output == {"ok": True}
    assert executor.count == 1 and len(uows.state["events"]) == 1
    assert uows.state["calls"][first.tool_call_id].status is ToolCallStatus.SUCCEEDED
    assert uows.state["audits"][-1].tool_call_id == first.tool_call_id
    replay = await service.invoke(invocation, request)
    assert replay.status is GatewayStatus.REPLAYED and executor.count == 1
    conflict = await service.invoke(invocation, replace(request, raw_input={"value": "changed"}))
    assert conflict.status is GatewayStatus.OPERATION_CONFLICT and executor.count == 1


@pytest.mark.asyncio
async def test_invalid_denied_missing_binding_and_stale_snapshot_fail_closed():
    ctx, candidate = context(), tool()
    allow = permission(ctx, candidate)
    for raw in (
        {},
        {"value": 1},
        {"value": "x", "extra": True},
        {"api_key": "secret"},
        {"value": "x" * 65_537},
    ):
        service, _, executor = gateway(ctx, candidate, allow)
        result = await service.invoke(
            TrustedAgentInvocation(ctx, AGENT_ID), ToolInvocationRequest("fake.read", raw)
        )
        assert result.status is GatewayStatus.INVALID_INPUT and executor.count == 0
    denied = replace(
        allow, decision=Decision.DENY, reason_code=DecisionReason.PERMISSION_EXPLICITLY_DENIED
    )
    service, _, executor = gateway(ctx, candidate, denied)
    assert (
        await service.invoke(
            TrustedAgentInvocation(ctx, AGENT_ID),
            ToolInvocationRequest("fake.read", {"value": "x"}),
        )
    ).status is GatewayStatus.DENIED
    service, uows, executor = gateway(
        ctx,
        candidate,
        permission(ctx, candidate),
        resource_resolver=DeniedResourceResolver(),
    )
    denied = await service.invoke(
        TrustedAgentInvocation(ctx, AGENT_ID), ToolInvocationRequest("fake.read", {"value": "x"})
    )
    assert denied.status is GatewayStatus.DENIED and executor.count == 0
    assert uows.state["audits"][-1].action == "tool.invocation.denied"
    service, _, _ = gateway(ctx, candidate, allow, binding=False)
    assert (
        await service.invoke(
            TrustedAgentInvocation(ctx, AGENT_ID),
            ToolInvocationRequest("fake.read", {"value": "x"}),
        )
    ).status is GatewayStatus.EXECUTOR_UNAVAILABLE
    service, uows, executor = gateway(ctx, candidate, allow)
    uows.state["authorized"] = False
    assert (
        await service.invoke(
            TrustedAgentInvocation(ctx, AGENT_ID),
            ToolInvocationRequest("fake.read", {"value": "x"}),
        )
    ).status is GatewayStatus.DENIED
    assert executor.count == 0


@pytest.mark.asyncio
async def test_budget_credentials_and_resolution_fail_closed():
    ctx, candidate = context(), tool()
    budgeted = permission(ctx, candidate, obligations=(Obligation.CHECK_BUDGET,))
    service, _, _ = gateway(ctx, candidate, budgeted)
    result = await service.invoke(
        TrustedAgentInvocation(ctx, AGENT_ID), ToolInvocationRequest("fake.read", {"value": "x"})
    )
    assert result.status is GatewayStatus.BUDGET_GUARD_UNAVAILABLE
    service, _, _ = gateway(ctx, candidate, budgeted, budget=Budget(False))
    assert (
        await service.invoke(
            TrustedAgentInvocation(ctx, AGENT_ID),
            ToolInvocationRequest("fake.read", {"value": "x"}),
        )
    ).status is GatewayStatus.BUDGET_GUARD_UNAVAILABLE
    connector = tool(credential=CredentialBoundary.CONNECTOR)
    service, _, _ = gateway(ctx, connector, permission(ctx, connector))
    assert (
        await service.invoke(
            TrustedAgentInvocation(ctx, AGENT_ID),
            ToolInvocationRequest("fake.read", {"value": "x"}),
        )
    ).status is GatewayStatus.CREDENTIAL_EXECUTION_UNAVAILABLE
    service.agent_resolver = Resolver(RuntimeError("unavailable"))
    assert (
        await service.invoke(
            TrustedAgentInvocation(ctx, AGENT_ID),
            ToolInvocationRequest("fake.read", {"value": "x"}),
        )
    ).status is GatewayStatus.DENIED


@pytest.mark.asyncio
async def test_pre_effect_retry_and_unknown_outcome_block_duplicate_effect():
    ctx, candidate = context(), tool()
    executor = FakePublishExecutor([PreEffectFailure(), None])
    service, _, _ = gateway(ctx, candidate, permission(ctx, candidate), executor)
    invocation = TrustedAgentInvocation(ctx, AGENT_ID)
    request = ToolInvocationRequest("fake.read", {"value": "x"}, "op_" + "2" * 32)
    assert (await service.invoke(invocation, request)).status is GatewayStatus.FAILED_PRE_EFFECT
    assert (await service.invoke(invocation, request)).status is GatewayStatus.EXECUTED
    assert executor.count == 2 and executor.effects == 1
    unknown = FakePublishExecutor([OutcomeUnknown()])
    service, uows, _ = gateway(ctx, candidate, permission(ctx, candidate), unknown)
    request = replace(request, operation_id="op_" + "3" * 32)
    assert (await service.invoke(invocation, request)).status is GatewayStatus.UNKNOWN_OUTCOME
    assert (
        await service.invoke(invocation, request)
    ).status is GatewayStatus.BLOCKED_RECONCILIATION
    assert unknown.count == 1 and unknown.effects == 1
    record = next(iter(uows.state["idem"].values()))
    uows.state["idem"][record.id] = reconcile(
        record, ReconciliationOutcome.NO_EFFECT_CONFIRMED, NOW
    )
    assert (await service.invoke(invocation, request)).status is GatewayStatus.EXECUTED
    assert unknown.count == 2

    confirmed = FakePublishExecutor([OutcomeUnknown()])
    service, uows, _ = gateway(ctx, candidate, permission(ctx, candidate), confirmed)
    request = replace(request, operation_id="op_" + "8" * 32)
    assert (await service.invoke(invocation, request)).status is GatewayStatus.UNKNOWN_OUTCOME
    record = next(iter(uows.state["idem"].values()))
    uows.state["idem"][record.id] = reconcile(
        record,
        ReconciliationOutcome.EFFECT_CONFIRMED,
        NOW,
        result_ref="result://fake/reconciled",
    )
    replay = await service.invoke(invocation, request)
    assert replay.status is GatewayStatus.REPLAYED and confirmed.count == 1
    assert next(iter(uows.state["calls"].values())).external_outcome.value == "RECONCILED"


@pytest.mark.asyncio
async def test_approval_binds_same_operation_and_changed_payload_conflicts():
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
    service, uows, executor = gateway(ctx, candidate, governed, FakePublishExecutor())
    invocation = TrustedAgentInvocation(ctx, AGENT_ID)
    request = ToolInvocationRequest("fake.read", {"value": "publish"}, "op_" + "4" * 32)
    waiting = await service.invoke(invocation, request)
    assert waiting.status is GatewayStatus.AWAITING_APPROVAL and executor.count == 0
    uows.state["decisions"][waiting.approval_request_id] = ApprovalDecision(
        waiting.approval_request_id,
        ctx.tenant_id,
        HumanDecision.APPROVE,
        ctx.user_id,
        "user",
        NOW,
    )
    executed = await service.invoke(invocation, request)
    assert executed.status is GatewayStatus.EXECUTED and executor.effects == 1
    conflict = await service.invoke(invocation, replace(request, raw_input={"value": "other"}))
    assert conflict.status is GatewayStatus.OPERATION_CONFLICT and executor.effects == 1


@pytest.mark.asyncio
async def test_invalid_output_is_not_retried_and_post_effect_commit_recovers_unknown():
    ctx, candidate = context(), tool()
    invalid = FakeReadExecutor(output={"unexpected": True})
    service, _, _ = gateway(ctx, candidate, permission(ctx, candidate), invalid)
    invocation = TrustedAgentInvocation(ctx, AGENT_ID)
    request = ToolInvocationRequest("fake.read", {"value": "x"}, "op_" + "5" * 32)
    result = await service.invoke(invocation, request)
    assert result.status is GatewayStatus.RESULT_CONTRACT_INVALID and result.output is None
    assert (await service.invoke(invocation, request)).status is GatewayStatus.REPLAYED
    assert invalid.count == 1

    service, uows, executor = gateway(ctx, candidate, permission(ctx, candidate))
    uows.commit_failures = [False, True, False]
    request = replace(request, operation_id="op_" + "6" * 32)
    recovered = await service.invoke(invocation, request)
    assert recovered.status is GatewayStatus.UNKNOWN_OUTCOME and executor.count == 1
    assert (
        await service.invoke(invocation, request)
    ).status is GatewayStatus.BLOCKED_RECONCILIATION

    service, uows, executor = gateway(ctx, candidate, permission(ctx, candidate))
    uows.commit_failures = [False, True, True]
    request = replace(request, operation_id="op_" + "7" * 32)
    ambiguous = await service.invoke(invocation, request)
    assert ambiguous.status is GatewayStatus.CRITICAL_AMBIGUOUS and executor.count == 1
    call = next(iter(uows.state["calls"].values()))
    assert call.status is ToolCallStatus.EXECUTING


def test_domain_contract_guards_and_exact_version_registry():
    with pytest.raises(ValueError):
        ToolInvocationRequest("fake.read", {}, "caller-controlled")
    with pytest.raises(ValueError):
        ToolExecutorResult({}, "https://provider.test/raw")
    candidate = tool()
    executor = FakeReadExecutor()
    binding = ToolExecutionBinding(
        candidate.definition_id,
        candidate.version_id,
        NormalizedToolInput.from_trusted_value,
        ResourceResolver(),
        executor,
    )
    registry = ToolExecutionBindingRegistry((binding,))
    assert registry.resolve(candidate) is binding
    assert registry.resolve(replace(candidate, version_id=uuid4())) is None
    with pytest.raises(ValueError):
        ToolExecutionBindingRegistry((binding, binding))
