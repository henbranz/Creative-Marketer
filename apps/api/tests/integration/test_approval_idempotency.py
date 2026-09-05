# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from creative_marketer.action_binding import (
    ActionBindingV1,
    NormalizedToolInput,
    OperationIdempotencyKey,
)
from creative_marketer.agent_governance.application import (
    ActivateAgentVersion,
    CreateAgentVersion,
    ResolveActiveAgentVersion,
)
from creative_marketer.approval_governance.application import (
    CreateApprovalRequest,
    DecideApproval,
    InspectApproval,
    RevokeApproval,
)
from creative_marketer.approval_governance.domain import (
    ApprovalConflict,
    ApprovalNotFound,
    ApprovalState,
    ApprovalValidationReason,
    ApprovalValidator,
    HumanDecision,
)
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
    IdempotencyNotFound,
    IdempotencyState,
    ReconciliationOutcome,
    ReservationOutcome,
    StaleExecutionAttempt,
)
from creative_marketer.identity.application.authentication import ActorKind
from creative_marketer.identity.application.use_cases import CreateTenant
from creative_marketer.infrastructure.database.agent_governance_uow import (
    SqlAlchemyAgentRegistryUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.approval_schema import approval_requests
from creative_marketer.infrastructure.database.approval_uow import (
    SqlAlchemyApprovalUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.execution_control_schema import idempotency_records
from creative_marketer.infrastructure.database.execution_control_uow import (
    SqlAlchemyIdempotencyUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.permission_governance_uow import (
    SqlAlchemyPermissionUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.tool_governance_uow import (
    SqlAlchemyToolRegistryUnitOfWorkFactory,
)
from creative_marketer.permission_governance.application import (
    ActivateToolPermissionVersion,
    CreateToolPermission,
    CreateToolPermissionVersion,
    EvaluateToolPermission,
)
from creative_marketer.permission_governance.domain import (
    ApprovalBehavior,
    PermissionEffect,
    ScopeAccess,
    ScopeRequirement,
    ToolPermissionVersionConfiguration,
    TrustedScopeRequirements,
)
from creative_marketer.tool_governance.application import (
    ActivateToolVersion,
    CreateToolVersion,
    PlatformControlContext,
    ResolveActiveTool,
)
from tests.integration.support import IdentityStack
from tests.integration.test_permission_engine import (
    agent_config,
    execution_context,
    setup_subject,
    tool_config,
)


async def approval_subject(
    identity_stack: IdentityStack,
    agent_factory: SqlAlchemyAgentRegistryUnitOfWorkFactory,
    tool_control_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
    tool_runtime_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
    permission_factory: SqlAlchemyPermissionUnitOfWorkFactory,
):
    ctx, agent_definition, tool_definition = await setup_subject(
        identity_stack, agent_factory, tool_control_factory
    )
    policy = await CreateToolPermission(permission_factory)(
        ctx, agent_definition.id, tool_definition.id
    )
    version = await CreateToolPermissionVersion(permission_factory)(
        ctx,
        policy.id,
        ToolPermissionVersionConfiguration(
            PermissionEffect.GRANT,
            ("catalog.product",),
            ("test",),
            ApprovalBehavior.ALWAYS,
        ),
    )
    await ActivateToolPermissionVersion(permission_factory)(ctx, policy.id, version.id)
    permission_decision = await EvaluateToolPermission(
        permission_factory,
        ResolveActiveAgentVersion(agent_factory),
        ResolveActiveTool(tool_runtime_factory),
    )(
        ctx,
        agent_definition.id,
        tool_definition.tool_key,
        TrustedScopeRequirements((ScopeRequirement("catalog.product", ScopeAccess.READ),)),
    )
    return ctx, permission_decision, agent_definition, tool_definition


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_concurrent_decision_reservation_and_attempt_ownership(
    admin_engine: AsyncEngine,
    identity_stack: IdentityStack,
    agent_registry_factory: SqlAlchemyAgentRegistryUnitOfWorkFactory,
    tool_control_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
    tool_runtime_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
    permission_factory: SqlAlchemyPermissionUnitOfWorkFactory,
    approval_factory: SqlAlchemyApprovalUnitOfWorkFactory,
    idempotency_factory: SqlAlchemyIdempotencyUnitOfWorkFactory,
) -> None:
    ctx, permission_decision, _, _ = await approval_subject(
        identity_stack,
        agent_registry_factory,
        tool_control_factory,
        tool_runtime_factory,
        permission_factory,
    )
    request = await CreateApprovalRequest(approval_factory)(
        ctx,
        permission_decision,
        NormalizedToolInput.from_trusted_value({"post_id": "123", "caption": "A"}),
        resource_type="catalog.product",
        resource_id="123",
    )
    decisions = await asyncio.gather(
        DecideApproval(approval_factory)(ctx, request.id, HumanDecision.APPROVE),
        DecideApproval(approval_factory)(ctx, request.id, HumanDecision.DENY),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, Exception) for result in decisions) == 1
    assert sum(isinstance(result, ApprovalConflict) for result in decisions) == 1
    for index in range(8):
        race_request = await CreateApprovalRequest(approval_factory)(
            ctx,
            permission_decision,
            NormalizedToolInput.from_trusted_value({"post_id": f"race-{index}"}),
        )
        race = await asyncio.gather(
            DecideApproval(approval_factory)(ctx, race_request.id, HumanDecision.APPROVE),
            DecideApproval(approval_factory)(ctx, race_request.id, HumanDecision.DENY),
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in race) == 1
        assert sum(isinstance(result, ApprovalConflict) for result in race) == 1
    await RevokeApproval(approval_factory)(ctx, request.id, "operator_request")
    assert (await InspectApproval(approval_factory)(ctx, request.id)).state is ApprovalState.REVOKED

    reservation_service = ReserveIdempotentOperation(idempotency_factory)
    reservation_rounds = [
        await asyncio.gather(
            reservation_service(ctx, request.binding),
            reservation_service(ctx, request.binding),
        )
        for _ in range(8)
    ]
    reservations = reservation_rounds[0]
    assert {result.outcome for result in reservations} == {
        ReservationOutcome.NEW_RESERVATION,
        ReservationOutcome.EXISTING_PENDING,
    }
    assert len({result.record.id for result in reservations}) == 1
    assert len({result.record.id for pair in reservation_rounds for result in pair}) == 1
    assert reservations[0].record.request_digest == request.action_digest

    alternate = replace(
        request.binding,
        idempotency_key="op_" + uuid4().hex,
    )
    conflict_binding = replace(alternate, normalized_input_digest="sha256:" + "f" * 64)
    conflicting = await asyncio.gather(
        reservation_service(ctx, alternate),
        reservation_service(ctx, conflict_binding),
    )
    assert {result.outcome for result in conflicting} == {
        ReservationOutcome.NEW_RESERVATION,
        ReservationOutcome.CONFLICT,
    }
    alternate_record = next(
        result.record
        for result in conflicting
        if result.outcome is ReservationOutcome.NEW_RESERVATION
    )
    alternate_attempt = await BeginExecutionAttempt(idempotency_factory)(ctx, alternate_record.id)
    await MarkUnknownExternalOutcome(idempotency_factory)(
        ctx, alternate_record.id, alternate_attempt.record.current_attempt_id
    )
    reconciled = await ReconcileUnknownOutcome(idempotency_factory)(
        ctx,
        alternate_record.id,
        ReconciliationOutcome.EFFECT_CONFIRMED,
        result_ref="result://operation/alternate",
    )
    assert reconciled.reconciliation_outcome is ReconciliationOutcome.EFFECT_CONFIRMED
    record_id = reservations[0].record.id
    attempts = await asyncio.gather(
        BeginExecutionAttempt(idempotency_factory)(ctx, record_id),
        BeginExecutionAttempt(idempotency_factory)(ctx, record_id),
    )
    assert {result.outcome for result in attempts} == {
        AttemptAcquisitionOutcome.ACQUIRED,
        AttemptAcquisitionOutcome.IN_PROGRESS,
    }
    acquired = next(
        result for result in attempts if result.outcome is AttemptAcquisitionOutcome.ACQUIRED
    )
    failed = await MarkFailedPreEffect(idempotency_factory)(
        ctx, record_id, acquired.record.current_attempt_id
    )
    assert failed.state is IdempotencyState.FAILED_PRE_EFFECT
    retry = await BeginExecutionAttempt(idempotency_factory)(ctx, record_id)
    with pytest.raises(StaleExecutionAttempt):
        await MarkSucceeded(idempotency_factory)(
            ctx, record_id, acquired.record.current_attempt_id, result_ref="result://stale"
        )
    succeeded = await MarkSucceeded(idempotency_factory)(
        ctx, record_id, retry.record.current_attempt_id, result_ref="result://operation/123"
    )
    assert succeeded.state is IdempotencyState.SUCCEEDED

    async with admin_engine.connect() as connection:
        actions = set(
            (
                await connection.execute(
                    text(
                        "SELECT action FROM audit.audit_records "
                        "WHERE approval_request_id = :approval OR idempotency_record_id = :record"
                    ),
                    {"approval": request.id, "record": record_id},
                )
            ).scalars()
        )
    assert {
        "approval.request.created",
        "idempotency.reserved",
        "idempotency.execution.started",
        "idempotency.succeeded",
    }.issubset(actions)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_rls_and_database_immutability_are_fail_closed(
    admin_engine: AsyncEngine,
    identity_stack: IdentityStack,
    agent_registry_factory: SqlAlchemyAgentRegistryUnitOfWorkFactory,
    tool_control_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
    tool_runtime_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
    permission_factory: SqlAlchemyPermissionUnitOfWorkFactory,
    approval_factory: SqlAlchemyApprovalUnitOfWorkFactory,
    idempotency_factory: SqlAlchemyIdempotencyUnitOfWorkFactory,
) -> None:
    ctx, permission_decision, _, _ = await approval_subject(
        identity_stack,
        agent_registry_factory,
        tool_control_factory,
        tool_runtime_factory,
        permission_factory,
    )
    request = await CreateApprovalRequest(approval_factory)(
        ctx, permission_decision, NormalizedToolInput.from_trusted_value({"amount": 100})
    )
    record = (await ReserveIdempotentOperation(idempotency_factory)(ctx, request.binding)).record
    other = await CreateTenant(identity_stack.uow_factory)("Other", f"approval-other-{uuid4()}")
    with pytest.raises(ApprovalNotFound):
        await InspectApproval(approval_factory)(execution_context(other.id), request.id)
    with pytest.raises(IdempotencyNotFound, match="not found"):
        await InspectIdempotencyState(idempotency_factory)(execution_context(other.id), record.id)

    async with admin_engine.begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                update(approval_requests)
                .where(approval_requests.c.id == request.id)
                .values(action_digest="sha256:" + "f" * 64)
            )
        await connection.rollback()
    async with admin_engine.begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                update(idempotency_records)
                .where(idempotency_records.c.id == record.id)
                .values(request_digest="sha256:" + "f" * 64)
            )
        await connection.rollback()
    async with admin_engine.connect() as connection:
        assert (
            await connection.scalar(
                select(idempotency_records.c.request_digest).where(
                    idempotency_records.c.id == record.id
                )
            )
            == request.action_digest
        )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_live_version_activations_invalidate_old_approval(
    admin_engine: AsyncEngine,
    identity_stack: IdentityStack,
    agent_registry_factory: SqlAlchemyAgentRegistryUnitOfWorkFactory,
    tool_control_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
    tool_runtime_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
    permission_factory: SqlAlchemyPermissionUnitOfWorkFactory,
    approval_factory: SqlAlchemyApprovalUnitOfWorkFactory,
) -> None:
    assert admin_engine is not None
    ctx, original, agent_definition, tool_definition = await approval_subject(
        identity_stack,
        agent_registry_factory,
        tool_control_factory,
        tool_runtime_factory,
        permission_factory,
    )
    normalized = NormalizedToolInput.from_trusted_value({"post_id": "123", "caption": "A"})
    request = await CreateApprovalRequest(approval_factory)(ctx, original, normalized)
    await DecideApproval(approval_factory)(ctx, request.id, HumanDecision.APPROVE)
    view = await InspectApproval(approval_factory)(ctx, request.id)
    evaluator = EvaluateToolPermission(
        permission_factory,
        ResolveActiveAgentVersion(agent_registry_factory),
        ResolveActiveTool(tool_runtime_factory),
    )
    scopes = TrustedScopeRequirements((ScopeRequirement("catalog.product", ScopeAccess.READ),))

    async def validate_current() -> ApprovalValidationReason:
        current = await evaluator(ctx, agent_definition.id, tool_definition.tool_key, scopes)
        rebound = ActionBindingV1.from_permission_decision(
            current,
            normalized,
            OperationIdempotencyKey(request.binding.idempotency_key),
        )
        return (
            ApprovalValidator()
            .validate(current, rebound, request, view.decision, view.revocation, datetime.now(UTC))
            .reason
        )

    agent_v2 = await CreateAgentVersion(agent_registry_factory)(
        ctx, agent_definition.id, replace(agent_config(), prompt_revision="research.v2")
    )
    await ActivateAgentVersion(agent_registry_factory)(ctx, agent_definition.id, agent_v2.id)
    assert await validate_current() is ApprovalValidationReason.AGENT_VERSION_CHANGED
    await ActivateAgentVersion(agent_registry_factory)(
        ctx, agent_definition.id, original.agent_version_id
    )

    control = PlatformControlContext(ActorKind.SYSTEM, uuid4(), "test", uuid4())
    tool_v2 = await CreateToolVersion(tool_control_factory)(
        control,
        tool_definition.id,
        replace(tool_config(), description="Reads one product with v2 semantics."),
    )
    await ActivateToolVersion(tool_control_factory)(control, tool_definition.id, tool_v2.id)
    assert await validate_current() is ApprovalValidationReason.TOOL_VERSION_CHANGED
    await ActivateToolVersion(tool_control_factory)(
        control, tool_definition.id, original.tool_version_id
    )

    policy_v2 = await CreateToolPermissionVersion(permission_factory)(
        ctx,
        original.permission_id,
        ToolPermissionVersionConfiguration(
            PermissionEffect.GRANT,
            ("catalog.product",),
            ("production", "test"),
            ApprovalBehavior.ALWAYS,
        ),
    )
    await ActivateToolPermissionVersion(permission_factory)(
        ctx, original.permission_id, policy_v2.id
    )
    assert await validate_current() is ApprovalValidationReason.POLICY_CHANGED
