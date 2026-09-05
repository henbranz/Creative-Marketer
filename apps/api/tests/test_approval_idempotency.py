# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,index,misc"

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from creative_marketer.action_binding import (
    ActionBindingV1,
    NormalizedToolInput,
    OperationIdempotencyKey,
    canonical_json_v1,
    sha256_canonical_v1,
)
from creative_marketer.approval_governance.application import (
    CreateApprovalRequest,
    DecideApproval,
    InspectApproval,
    RevokeApproval,
)
from creative_marketer.approval_governance.domain import (
    ApprovalConflict,
    ApprovalDecision,
    ApprovalExpired,
    ApprovalForbidden,
    ApprovalNotFound,
    ApprovalRequest,
    ApprovalRevocation,
    ApprovalState,
    ApprovalValidationReason,
    ApprovalValidator,
    HumanDecision,
    approval_ttl,
    effective_approval_state,
)
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.execution_control.application import (
    BeginExecutionAttempt,
    InspectIdempotencyState,
    MarkFailedPreEffect,
    MarkSucceeded,
    MarkUnknownExternalOutcome,
    ReconcileUnknownOutcome,
    ReserveIdempotentOperation,
)
from creative_marketer.execution_control.domain import (
    AttemptAcquisitionOutcome,
    IdempotencyConflict,
    IdempotencyNotFound,
    IdempotencyRecord,
    IdempotencyState,
    InvalidIdempotencyTransition,
    ReconciliationOutcome,
    ReservationOutcome,
    StaleExecutionAttempt,
    begin_attempt,
    complete_attempt,
    reconcile,
    reservation_outcome,
)
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
)
from creative_marketer.tool_governance.domain import RiskLevel

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def context(
    *,
    tenant_id=None,
    role=MembershipRole.OWNER,
    actor_kind=ActorKind.USER,
    status=MembershipStatus.ACTIVE,
) -> ExecutionContext:
    user_id = uuid4()
    return ExecutionContext(
        tenant_id or uuid4(),
        Actor(actor_kind, user_id if actor_kind is ActorKind.USER else uuid4()),
        user_id,
        role,
        status,
        "test",
        AuthenticationAssurance(NOW, "test", "mfa"),
        uuid4(),
    )


def permission(ctx: ExecutionContext, *, risk=RiskLevel.R4, decision=Decision.REQUIRES_APPROVAL):
    return PermissionDecision(
        decision,
        DecisionReason.APPROVAL_REQUIRED
        if decision is Decision.REQUIRES_APPROVAL
        else DecisionReason.ALLOWED,
        ctx.tenant_id,
        ctx.actor.kind.value,
        ctx.actor.id,
        uuid4(),
        uuid4(),
        uuid4(),
        DIGEST_A,
        uuid4(),
        uuid4(),
        DIGEST_A,
        "social.post.publish",
        risk,
        uuid4(),
        uuid4(),
        DIGEST_A,
        DIGEST_A,
        ctx.environment,
        (Obligation.REQUIRE_APPROVAL,),
        uuid4(),
    )


def binding(
    *,
    ctx=None,
    risk=RiskLevel.R4,
    key=None,
    normalized_digest=DIGEST_A,
) -> ActionBindingV1:
    ctx = ctx or context()
    return ActionBindingV1(
        ctx.tenant_id,
        uuid4(),
        uuid4(),
        uuid4(),
        DIGEST_A,
        uuid4(),
        uuid4(),
        DIGEST_A,
        "social.post.publish",
        risk,
        uuid4(),
        uuid4(),
        DIGEST_A,
        1,
        DIGEST_A,
        "social.post",
        "post-123",
        ctx.environment,
        normalized_digest,
        key or OperationIdempotencyKey.generate().value,
    )


def approval(bound: ActionBindingV1, *, created_at=NOW) -> ApprovalRequest:
    return ApprovalRequest(
        bound,
        "agent",
        uuid4(),
        created_at,
        created_at + approval_ttl(bound.risk_level),
    )


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        b"bytes",
        NOW,
        {1: "bad"},
        {"items": {1, 2}},
        9_007_199_254_740_992,
        "\ud800",
    ],
)
def test_canonicalization_rejects_nonportable_values(value) -> None:
    with pytest.raises(ValueError):
        canonical_json_v1(value)


@pytest.mark.parametrize(
    "value",
    [
        {"password": "hidden"},
        {"nested": {"authorization": "value"}},
        {"caption": "Bearer abcdef"},
        {"caption": "api_key=abcdef"},
    ],
)
def test_normalized_input_rejects_credentials(value) -> None:
    with pytest.raises(ValueError):
        NormalizedToolInput.from_trusted_value(value)


def test_canonicalization_is_order_independent_stable_and_immutable() -> None:
    left = NormalizedToolInput.from_trusted_value({"b": 2, "a": [True, None, "é"]})
    right = NormalizedToolInput.from_trusted_value({"a": [True, None, "é"], "b": 2})
    assert left == right
    assert left.canonical_json == '{"a":[true,null,"é"],"b":2}'
    assert left.digest == sha256_canonical_v1({"b": 2, "a": [True, None, "é"]})
    with pytest.raises(TypeError):
        left.value()["a"] = []
    with pytest.raises(FrozenInstanceError):
        left.digest = DIGEST_B
    with pytest.raises(ValueError):
        NormalizedToolInput('{"b":2, "a":1}', DIGEST_A)
    with pytest.raises(ValueError):
        NormalizedToolInput("not-json", DIGEST_A)


def test_operation_keys_and_binding_validate_all_invariants() -> None:
    with pytest.raises(ValueError):
        OperationIdempotencyKey("caller-key")
    ctx = context()
    normalized = NormalizedToolInput.from_trusted_value({"post_id": "123", "caption": "A"})
    decision = permission(ctx)
    built = ActionBindingV1.from_permission_decision(
        decision,
        normalized,
        OperationIdempotencyKey.generate(),
        resource_type="social.post",
        resource_id="123",
    )
    assert built.tenant_id == ctx.tenant_id
    assert built.normalized_input_digest == normalized.digest
    assert built.action_digest.startswith("sha256:")
    assert built.action_digest == sha256_canonical_v1(built.primitive())
    for change in (
        {"normalized_input_digest": DIGEST_B},
        {"agent_version_id": uuid4()},
        {"tool_version_id": uuid4()},
        {"permission_version_id": uuid4()},
        {"resource_id": "other"},
        {"environment": "production"},
    ):
        assert replace(built, **change).action_digest != built.action_digest
    with pytest.raises(ValueError):
        replace(built, resource_id=None)
    with pytest.raises(ValueError):
        replace(built, canonicalization_version=2)
    with pytest.raises(ValueError):
        replace(built, agent_configuration_digest="bad")
    denied = replace(decision, decision=Decision.DENY)
    with pytest.raises(ValueError):
        ActionBindingV1.from_permission_decision(
            denied, normalized, OperationIdempotencyKey.generate()
        )


@pytest.mark.parametrize(
    ("risk", "expected"),
    [
        (RiskLevel.R0, timedelta(days=7)),
        (RiskLevel.R4, timedelta(days=7)),
        (RiskLevel.R5, timedelta(hours=24)),
        (RiskLevel.R6, timedelta(hours=1)),
    ],
)
def test_approval_ttl_is_risk_bounded(risk, expected) -> None:
    assert approval_ttl(risk) == expected
    with pytest.raises(ValueError):
        approval_ttl(RiskLevel.R7)


def test_approval_history_and_state_precedence() -> None:
    request = approval(binding())
    approved = ApprovalDecision(
        request.id, request.tenant_id, HumanDecision.APPROVE, uuid4(), "user", NOW
    )
    denied = replace(approved, decision=HumanDecision.DENY)
    revoked = ApprovalRevocation(request.id, request.tenant_id, uuid4(), NOW, "operator_request")
    assert effective_approval_state(request, None, None, NOW) is ApprovalState.PENDING
    assert effective_approval_state(request, approved, None, NOW) is ApprovalState.APPROVED
    assert effective_approval_state(request, denied, None, NOW) is ApprovalState.DENIED
    assert (
        effective_approval_state(request, approved, None, request.expires_at)
        is ApprovalState.EXPIRED
    )
    assert (
        effective_approval_state(request, denied, revoked, request.expires_at)
        is ApprovalState.REVOKED
    )
    with pytest.raises(FrozenInstanceError):
        approved.decision = HumanDecision.DENY
    with pytest.raises(ValueError):
        replace(approved, decided_by_actor_kind="agent")
    with pytest.raises(ValueError):
        replace(approved, safe_note="Authorization: Bearer abc")
    with pytest.raises(ValueError):
        replace(revoked, reason_code="")
    with pytest.raises(ValueError):
        replace(approved, decided_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        replace(revoked, revoked_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        replace(request, expires_at=request.expires_at + timedelta(seconds=1))


def test_approval_validator_reports_exact_mismatch_and_never_overrides_deny() -> None:
    original = binding()
    request = approval(original)
    decision = ApprovalDecision(
        request.id, request.tenant_id, HumanDecision.APPROVE, uuid4(), "user", NOW
    )
    current_permission = permission(context(tenant_id=request.tenant_id))
    validator = ApprovalValidator()
    assert (
        validator.validate(current_permission, original, request, decision, None, NOW).reason
        is ApprovalValidationReason.VALID
    )
    cases = (
        (replace(original, tenant_id=uuid4()), ApprovalValidationReason.TENANT_MISMATCH),
        (
            replace(original, agent_version_id=uuid4()),
            ApprovalValidationReason.AGENT_VERSION_CHANGED,
        ),
        (replace(original, tool_version_id=uuid4()), ApprovalValidationReason.TOOL_VERSION_CHANGED),
        (
            replace(original, permission_version_id=uuid4()),
            ApprovalValidationReason.POLICY_CHANGED,
        ),
        (replace(original, environment="prod"), ApprovalValidationReason.ENVIRONMENT_CHANGED),
        (
            replace(original, idempotency_key=OperationIdempotencyKey.generate().value),
            ApprovalValidationReason.IDEMPOTENCY_MISMATCH,
        ),
        (
            replace(original, normalized_input_digest=DIGEST_B),
            ApprovalValidationReason.ACTION_MISMATCH,
        ),
    )
    for candidate, reason in cases:
        assert (
            validator.validate(current_permission, candidate, request, decision, None, NOW).reason
            is reason
        )
    assert (
        validator.validate(
            replace(current_permission, decision=Decision.DENY),
            original,
            request,
            decision,
            None,
            NOW,
        ).reason
        is ApprovalValidationReason.CURRENT_PERMISSION_DENIED
    )
    assert (
        validator.validate(current_permission, original, None, None, None, NOW).reason
        is ApprovalValidationReason.NOT_FOUND
    )
    for candidate_decision, revocation, at, reason in (
        (None, None, NOW, ApprovalValidationReason.PENDING),
        (
            replace(decision, decision=HumanDecision.DENY),
            None,
            NOW,
            ApprovalValidationReason.DENIED,
        ),
        (
            decision,
            ApprovalRevocation(request.id, request.tenant_id, uuid4(), NOW, "x"),
            NOW,
            ApprovalValidationReason.REVOKED,
        ),
        (decision, None, request.expires_at, ApprovalValidationReason.EXPIRED),
    ):
        assert (
            validator.validate(
                current_permission, original, request, candidate_decision, revocation, at
            ).reason
            is reason
        )


@pytest.mark.parametrize("state", list(IdempotencyState))
def test_same_key_different_digest_conflicts_in_every_state(state) -> None:
    reconciliation = (
        ReconciliationOutcome.EFFECT_CONFIRMED if state is IdempotencyState.RECONCILED else None
    )
    attempt = uuid4() if state is IdempotencyState.EXECUTING else None
    lease = NOW + timedelta(minutes=5) if attempt else None
    record = IdempotencyRecord(
        uuid4(),
        uuid4(),
        uuid4(),
        OperationIdempotencyKey.generate().value,
        DIGEST_A,
        state,
        1 if attempt else 0,
        attempt,
        lease,
        "result://abc"
        if state in {IdempotencyState.SUCCEEDED, IdempotencyState.RECONCILED}
        else None,
        reconciliation,
    )
    assert reservation_outcome(record, DIGEST_B) is ReservationOutcome.CONFLICT


def test_idempotency_state_machine_is_conservative_and_replay_safe() -> None:
    record = IdempotencyRecord.from_binding(binding())
    executing, outcome = begin_attempt(record, NOW)
    assert outcome is AttemptAcquisitionOutcome.ACQUIRED
    assert executing.attempt_count == 1
    assert (
        begin_attempt(executing, NOW + timedelta(days=1))[1]
        is AttemptAcquisitionOutcome.IN_PROGRESS
    )
    with pytest.raises(StaleExecutionAttempt):
        complete_attempt(
            executing, uuid4(), IdempotencyState.SUCCEEDED, NOW, result_ref="result://x"
        )
    failed = complete_attempt(
        executing, executing.current_attempt_id, IdempotencyState.FAILED_PRE_EFFECT, NOW
    )
    retry, outcome = begin_attempt(failed, NOW)
    assert outcome is AttemptAcquisitionOutcome.ACQUIRED
    assert retry.attempt_count == 2
    unknown = complete_attempt(
        retry, retry.current_attempt_id, IdempotencyState.UNKNOWN_EXTERNAL_OUTCOME, NOW
    )
    assert (
        begin_attempt(unknown, NOW)[1] is AttemptAcquisitionOutcome.UNKNOWN_REQUIRES_RECONCILIATION
    )
    no_effect = reconcile(unknown, ReconciliationOutcome.NO_EFFECT_CONFIRMED, NOW)
    assert begin_attempt(no_effect, NOW)[1] is AttemptAcquisitionOutcome.ACQUIRED
    confirmed = reconcile(
        unknown, ReconciliationOutcome.EFFECT_CONFIRMED, NOW, result_ref="result://abc"
    )
    assert begin_attempt(confirmed, NOW)[1] is AttemptAcquisitionOutcome.REPLAY_SUCCEEDED


def test_idempotency_rejects_invalid_transitions_and_results() -> None:
    record = IdempotencyRecord.from_binding(binding())
    with pytest.raises(InvalidIdempotencyTransition):
        reconcile(record, ReconciliationOutcome.NO_EFFECT_CONFIRMED, NOW)
    executing, _ = begin_attempt(record, NOW)
    with pytest.raises(InvalidIdempotencyTransition):
        complete_attempt(executing, executing.current_attempt_id, IdempotencyState.SUCCEEDED, NOW)
    with pytest.raises(InvalidIdempotencyTransition):
        complete_attempt(
            executing,
            executing.current_attempt_id,
            IdempotencyState.FAILED_PRE_EFFECT,
            NOW,
            result_ref="result://x",
        )
    with pytest.raises(ValueError):
        replace(record, result_ref="raw provider output")
    with pytest.raises(ValueError):
        replace(record, result_ref="https://provider.example/result")
    with pytest.raises(ValueError):
        replace(record, result_ref="result://sk-abcdefghijk")
    with pytest.raises(ValueError):
        replace(record, attempt_count=-1)
    with pytest.raises(ValueError):
        replace(record, request_digest="bad")
    with pytest.raises(ValueError):
        replace(record, current_attempt_id=uuid4())
    with pytest.raises(ValueError):
        replace(record, state=IdempotencyState.RECONCILED)


class MemoryApprovalRepository:
    def __init__(self):
        self.items = {}

    async def add(self, value):
        key = value.approval_request_id if hasattr(value, "approval_request_id") else value.id
        if key in self.items:
            raise ApprovalConflict("duplicate")
        self.items[key] = value

    async def get(self, approval_id, *, for_update=False):
        return self.items.get(approval_id)


class MemoryIdempotencyRepository:
    def __init__(self):
        self.items = {}

    async def reserve(self, candidate):
        key = (candidate.tool_definition_id, candidate.idempotency_key)
        if key in self.items:
            return self.items[key], False
        self.items[key] = candidate
        return candidate, True

    async def get(self, record_id, *, for_update=False):
        return next((item for item in self.items.values() if item.id == record_id), None)

    async def get_by_key(self, tool_definition_id, idempotency_key):
        return self.items.get((tool_definition_id, idempotency_key))

    async def update(self, record):
        self.items[(record.tool_definition_id, record.idempotency_key)] = record


class MemoryAudit:
    def __init__(self):
        self.records = []

    async def append(self, record):
        self.records.append(record)


class MemoryOutbox:
    def __init__(self):
        self.events = []

    async def append(self, event):
        self.events.append(event)


class MemoryUow:
    def __init__(self, requests=None, decisions=None, revocations=None, records=None):
        self.requests = requests
        self.decisions = decisions
        self.revocations = revocations
        self.records = records
        self.audit = MemoryAudit()
        self.outbox = MemoryOutbox()
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_approval_application_authority_expiry_revocation_and_audit() -> None:
    ctx = context()
    requests, decisions, revocations = (
        MemoryApprovalRepository(),
        MemoryApprovalRepository(),
        MemoryApprovalRepository(),
    )
    uow = MemoryUow(requests, decisions, revocations)

    def factory(_tenant):
        return uow

    create = CreateApprovalRequest(factory, lambda: NOW)
    normalized = NormalizedToolInput.from_trusted_value({"post_id": "123"})
    request = await create(ctx, permission(ctx), normalized)
    assert request.binding.idempotency_key.startswith("op_")
    assert uow.audit.records[-1].approval_request_id == request.id
    with pytest.raises(ApprovalForbidden):
        await create(ctx, replace(permission(ctx), decision=Decision.ALLOW), normalized)

    for role, risk, allowed in (
        (MembershipRole.OWNER, RiskLevel.R4, True),
        (MembershipRole.ADMIN, RiskLevel.R4, True),
        (MembershipRole.OWNER, RiskLevel.R5, True),
        (MembershipRole.OWNER, RiskLevel.R6, True),
        (MembershipRole.ADMIN, RiskLevel.R6, False),
        (MembershipRole.ADMIN, RiskLevel.R5, True),
        (MembershipRole.MEMBER, RiskLevel.R4, False),
    ):
        actor = context(tenant_id=ctx.tenant_id, role=role)
        candidate = approval(binding(ctx=ctx, risk=risk))
        requests.items[candidate.id] = candidate
        service = DecideApproval(factory, lambda: NOW)
        if allowed:
            assert (
                await service(actor, candidate.id, HumanDecision.APPROVE)
            ).decision is HumanDecision.APPROVE
        else:
            with pytest.raises(ApprovalForbidden):
                await service(actor, candidate.id, HumanDecision.APPROVE)

    agent = context(tenant_id=ctx.tenant_id, actor_kind=ActorKind.AGENT)
    with pytest.raises(ApprovalForbidden):
        await DecideApproval(factory, lambda: NOW)(agent, request.id, HumanDecision.APPROVE)

    expired = approval(binding(ctx=ctx), created_at=NOW - timedelta(days=8))
    requests.items[expired.id] = expired
    with pytest.raises(ApprovalExpired):
        await DecideApproval(factory, lambda: NOW)(ctx, expired.id, HumanDecision.APPROVE)

    pending = approval(binding(ctx=ctx))
    requests.items[pending.id] = pending
    revoked = await RevokeApproval(factory, lambda: NOW)(ctx, pending.id, "operator_request")
    assert revoked.approval_request_id == pending.id
    with pytest.raises(ApprovalConflict):
        await DecideApproval(factory, lambda: NOW)(ctx, pending.id, HumanDecision.APPROVE)
    assert (
        await InspectApproval(factory, lambda: NOW)(ctx, pending.id)
    ).state is ApprovalState.REVOKED
    denied_request = approval(binding(ctx=ctx))
    requests.items[denied_request.id] = denied_request
    await DecideApproval(factory, lambda: NOW)(ctx, denied_request.id, HumanDecision.DENY)
    registry = EventContractRegistry()
    for emitted in uow.outbox.events:
        registry.validate_event(emitted)
    assert {emitted.event_type for emitted in uow.outbox.events} >= {
        "governance.approval.requested.v1",
        "governance.approval.granted.v1",
        "governance.approval.denied.v1",
        "governance.approval.revoked.v1",
    }


@pytest.mark.asyncio
async def test_idempotency_application_reserve_attempt_complete_reconcile_and_audit() -> None:
    ctx = context()
    records = MemoryIdempotencyRepository()
    uow = MemoryUow(records=records)

    def factory(_tenant):
        return uow

    bound = binding(ctx=ctx)
    reserve = ReserveIdempotentOperation(factory)
    first = await reserve(ctx, bound)
    assert first.outcome is ReservationOutcome.NEW_RESERVATION
    assert (await reserve(ctx, bound)).outcome is ReservationOutcome.EXISTING_PENDING
    assert (
        await reserve(ctx, replace(bound, normalized_input_digest=DIGEST_B))
    ).outcome is ReservationOutcome.CONFLICT
    with pytest.raises(IdempotencyConflict):
        await reserve(context(), bound)

    acquired = await BeginExecutionAttempt(factory, clock=lambda: NOW)(ctx, first.record.id)
    assert acquired.outcome is AttemptAcquisitionOutcome.ACQUIRED
    attempt_id = acquired.record.current_attempt_id
    failed = await MarkFailedPreEffect(factory)(ctx, first.record.id, attempt_id)
    assert failed.state is IdempotencyState.FAILED_PRE_EFFECT
    retry = await BeginExecutionAttempt(factory, clock=lambda: NOW)(ctx, first.record.id)
    unknown = await MarkUnknownExternalOutcome(factory)(
        ctx, first.record.id, retry.record.current_attempt_id
    )
    assert unknown.state is IdempotencyState.UNKNOWN_EXTERNAL_OUTCOME
    reconciled = await ReconcileUnknownOutcome(factory, clock=lambda: NOW)(
        ctx, first.record.id, ReconciliationOutcome.NO_EFFECT_CONFIRMED
    )
    assert reconciled.reconciliation_outcome is ReconciliationOutcome.NO_EFFECT_CONFIRMED
    final_attempt = await BeginExecutionAttempt(factory, clock=lambda: NOW)(ctx, first.record.id)
    succeeded = await MarkSucceeded(factory)(
        ctx, first.record.id, final_attempt.record.current_attempt_id, result_ref="result://abc"
    )
    assert succeeded.state is IdempotencyState.SUCCEEDED
    assert uow.audit.records[-1].attempt_id == final_attempt.record.current_attempt_id


@pytest.mark.asyncio
async def test_application_not_found_conflict_and_reconciliation_authority_paths() -> None:
    ctx = context()
    requests, decisions, revocations = (
        MemoryApprovalRepository(),
        MemoryApprovalRepository(),
        MemoryApprovalRepository(),
    )
    approval_uow = MemoryUow(requests, decisions, revocations)

    def approval_factory(_tenant):
        return approval_uow

    missing = uuid4()
    with pytest.raises(ApprovalNotFound):
        await DecideApproval(approval_factory, lambda: NOW)(ctx, missing, HumanDecision.APPROVE)
    with pytest.raises(ApprovalNotFound):
        await RevokeApproval(approval_factory, lambda: NOW)(ctx, missing, "operator_request")
    with pytest.raises(ApprovalNotFound):
        await InspectApproval(approval_factory, lambda: NOW)(ctx, missing)
    request = approval(binding(ctx=ctx))
    requests.items[request.id] = request
    revocations.items[request.id] = ApprovalRevocation(
        request.id, request.tenant_id, ctx.user_id, NOW, "operator_request"
    )
    with pytest.raises(ApprovalConflict):
        await RevokeApproval(approval_factory, lambda: NOW)(ctx, request.id, "operator_request")

    records = MemoryIdempotencyRepository()
    idempotency_uow = MemoryUow(records=records)

    def idempotency_factory(_tenant):
        return idempotency_uow

    with pytest.raises(IdempotencyNotFound):
        await BeginExecutionAttempt(idempotency_factory, clock=lambda: NOW)(ctx, missing)
    with pytest.raises(IdempotencyNotFound):
        await MarkSucceeded(idempotency_factory)(ctx, missing, uuid4(), result_ref="result://x")
    with pytest.raises(IdempotencyNotFound):
        await InspectIdempotencyState(idempotency_factory)(ctx, missing)
    member = context(tenant_id=ctx.tenant_id, role=MembershipRole.MEMBER)
    with pytest.raises(InvalidIdempotencyTransition):
        await ReconcileUnknownOutcome(idempotency_factory, clock=lambda: NOW)(
            member, missing, ReconciliationOutcome.NO_EFFECT_CONFIRMED
        )
    with pytest.raises(IdempotencyNotFound):
        await ReconcileUnknownOutcome(idempotency_factory, clock=lambda: NOW)(
            ctx, missing, ReconciliationOutcome.NO_EFFECT_CONFIRMED
        )
