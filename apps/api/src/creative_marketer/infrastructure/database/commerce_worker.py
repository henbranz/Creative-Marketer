from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from creative_marketer.action_binding import NormalizedToolInput
from creative_marketer.approval_governance.domain import HumanDecision
from creative_marketer.commerce.application import CommerceService
from creative_marketer.commerce.domain import (
    ActionJobStatus,
    ActionType,
    CommerceNotFound,
    CommercePermissionDenied,
    DurableSyncStatus,
    OperationStatus,
    SyncType,
)
from creative_marketer.commerce.provider import FakeCommerceProvider
from creative_marketer.commerce.workflow_execution import (
    CommerceActionView,
    CommerceGovernanceStore,
    CommerceSyncView,
)
from creative_marketer.execution_control.application import ReconcileUnknownOutcome
from creative_marketer.execution_control.domain import ReconciliationOutcome
from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.infrastructure.database.agent_governance_schema import agent_definitions
from creative_marketer.infrastructure.database.approval_schema import (
    approval_decisions,
    approval_requests,
)
from creative_marketer.infrastructure.database.commerce_schema import (
    action_jobs,
    action_proposals,
    action_results,
    connections,
    inventory_observations,
    order_observations,
    sync_requests,
)
from creative_marketer.infrastructure.database.execution_control_uow import (
    SqlAlchemyIdempotencyUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.schema import memberships, tenants, users
from creative_marketer.infrastructure.database.tool_execution_repositories import (
    SqlAlchemyToolCallRepository,
)
from creative_marketer.infrastructure.database.tool_execution_schema import tool_calls
from creative_marketer.infrastructure.temporal.activities import (
    CommerceActionExecutor,
    CommerceSyncExecutor,
    TrustedWorkflowToolRequestResolver,
)
from creative_marketer.tool_execution.application import ToolGateway
from creative_marketer.tool_execution.domain import (
    GatewayStatus,
    ToolInvocationRequest,
    TrustedAgentInvocation,
)
from creative_marketer.workflow_orchestration.contracts import (
    CommerceActionActivityResult,
    CommerceSyncActivityResult,
    ToolWorkflowInput,
)

_REQUEST_REF = re.compile(r"tool-request://([0-9a-f]{32})")


class SqlAlchemyCommerceGovernanceStore(CommerceGovernanceStore):
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    @staticmethod
    async def _tenant(session: AsyncSession, tenant_id: UUID) -> None:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )

    async def proposal_action(self, tenant_id: UUID, proposal_id: UUID) -> ActionType:
        async with self._factory() as session, session.begin():
            await self._tenant(session, tenant_id)
            value = await session.scalar(
                select(action_proposals.c.action_type).where(
                    action_proposals.c.tenant_id == tenant_id,
                    action_proposals.c.id == proposal_id,
                )
            )
            if value is None:
                raise CommerceNotFound("commerce proposal not found")
            return ActionType(str(value))

    async def commerce_agent_definition(self, tenant_id: UUID) -> UUID:
        async with self._factory() as session, session.begin():
            await self._tenant(session, tenant_id)
            values = (
                (
                    await session.execute(
                        select(agent_definitions.c.id).where(
                            agent_definitions.c.tenant_id == tenant_id,
                            agent_definitions.c.agent_type == "commerce_execution_workload",
                            agent_definitions.c.status == "active",
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(values) != 1:
                raise CommercePermissionDenied(
                    "exactly one active Commerce Operations AgentDefinition is required"
                )
            return cast(UUID, values[0])

    async def proposal_for_approval(self, tenant_id: UUID, approval_id: UUID) -> UUID:
        async with self._factory() as session, session.begin():
            await self._tenant(session, tenant_id)
            resource_id = await session.scalar(
                select(tool_calls.c.resource_id).where(
                    tool_calls.c.tenant_id == tenant_id,
                    tool_calls.c.approval_request_id == approval_id,
                    tool_calls.c.resource_type == "commerce_action_proposal",
                )
            )
            if resource_id is None:
                raise CommerceNotFound("Commerce approval was not found")
            return UUID(str(resource_id))

    async def action_view(self, tenant_id: UUID, proposal_id: UUID) -> CommerceActionView:
        async with self._factory() as session, session.begin():
            await self._tenant(session, tenant_id)
            proposal = (
                (
                    await session.execute(
                        select(action_proposals, connections.c.safe_store_identifier)
                        .join(
                            connections,
                            (connections.c.tenant_id == action_proposals.c.tenant_id)
                            & (connections.c.id == action_proposals.c.connection_id),
                        )
                        .where(
                            action_proposals.c.tenant_id == tenant_id,
                            action_proposals.c.id == proposal_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if proposal is None:
                raise CommerceNotFound("commerce proposal not found")
            call = (
                (
                    await session.execute(
                        select(tool_calls)
                        .where(
                            tool_calls.c.tenant_id == tenant_id,
                            tool_calls.c.resource_type == "commerce_action_proposal",
                            tool_calls.c.resource_id == str(proposal_id),
                        )
                        .order_by(tool_calls.c.created_at.desc())
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
            decision = job = result = None
            if call is not None and call["approval_request_id"] is not None:
                decision = (
                    (
                        await session.execute(
                            select(approval_decisions).where(
                                approval_decisions.c.tenant_id == tenant_id,
                                approval_decisions.c.approval_request_id
                                == call["approval_request_id"],
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
            job = (
                (
                    await session.execute(
                        select(action_jobs)
                        .where(
                            action_jobs.c.tenant_id == tenant_id,
                            action_jobs.c.proposal_id == proposal_id,
                        )
                        .order_by(action_jobs.c.created_at.desc())
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
            result = (
                (
                    await session.execute(
                        select(action_results).where(
                            action_results.c.tenant_id == tenant_id,
                            action_results.c.proposal_id == proposal_id,
                            action_results.c.status == OperationStatus.SUCCEEDED.value,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            state = "NEEDS_APPROVAL"
            failure = None
            if decision is not None and decision["decision"] == HumanDecision.DENY.value:
                state = "FAILED"
                failure = "APPROVAL_REJECTED"
            elif result is not None:
                state = "SUCCEEDED"
            elif job is not None:
                state = {
                    ActionJobStatus.SUBMITTING.value: "SUBMITTING",
                    ActionJobStatus.SUBMITTED.value: "SUBMITTING",
                    ActionJobStatus.OUTCOME_UNKNOWN.value: "OUTCOME_UNKNOWN",
                    ActionJobStatus.SUCCEEDED.value: "SUCCEEDED",
                    ActionJobStatus.FAILED.value: "FAILED",
                }.get(job["status"], "QUEUED")
                failure = job["safe_failure_code"]
            elif call is not None and call["status"] == "SUCCEEDED":
                state = "SUCCEEDED"
            elif call is not None and call["status"] == "FAILED_PRE_EFFECT":
                state = "FAILED"
                failure = "PRE_EFFECT_FAILURE"
            elif call is not None and call["status"] == "UNKNOWN_EXTERNAL_OUTCOME":
                state = "OUTCOME_UNKNOWN"
            elif call is not None and call["status"] == "EXECUTING":
                state = "SUBMITTING"
            elif decision is not None and decision["decision"] == HumanDecision.APPROVE.value:
                state = "APPROVED"
            current_quantity = sku = order_reference = None
            if proposal["action_type"] == ActionType.INVENTORY_ADJUSTMENT.value:
                current = (
                    await session.execute(
                        select(
                            inventory_observations.c.available_quantity,
                            inventory_observations.c.sku,
                        )
                        .where(
                            inventory_observations.c.tenant_id == tenant_id,
                            inventory_observations.c.connection_id == proposal["connection_id"],
                            inventory_observations.c.external_variant_id
                            == proposal["external_variant_id"],
                        )
                        .order_by(inventory_observations.c.captured_at.desc())
                        .limit(1)
                    )
                ).one_or_none()
                if current is not None:
                    current_quantity, sku = current
            else:
                order_reference = await session.scalar(
                    select(order_observations.c.order_reference)
                    .where(
                        order_observations.c.tenant_id == tenant_id,
                        order_observations.c.connection_id == proposal["connection_id"],
                        order_observations.c.external_order_id == proposal["external_order_id"],
                    )
                    .order_by(order_observations.c.captured_at.desc())
                    .limit(1)
                )
            return CommerceActionView(
                proposal_id,
                (
                    str(call["tool_key"])
                    if call is not None
                    else (
                        "commerce.inventory.adjust"
                        if proposal["action_type"] == ActionType.INVENTORY_ADJUSTMENT.value
                        else "commerce.refund.submit"
                    )
                ),
                "R5" if proposal["action_type"] == ActionType.INVENTORY_ADJUSTMENT.value else "R6",
                state,
                str(call["operation_id"]) if call is not None else None,
                f"tool-request://{call['id'].hex}" if call is not None else None,
                call["approval_request_id"] if call is not None else None,
                str(result["external_operation_id"]) if result is not None else None,
                failure,
                str(proposal["safe_store_identifier"]),
                str(sku or proposal["external_variant_id"])
                if proposal["external_variant_id"] is not None
                else None,
                current_quantity,
                proposal["exact_quantity"],
                str(order_reference) if order_reference is not None else None,
                str(proposal["exact_amount"]) if proposal["exact_amount"] is not None else None,
                proposal["currency"],
                str(proposal["reason"]),
            )

    async def create_sync_request(
        self,
        context: ExecutionContext,
        connection_id: UUID,
        sync_types: tuple[SyncType, ...],
        idempotency_key: str,
    ) -> CommerceSyncView:
        if (
            context.actor.kind is not ActorKind.USER
            or context.actor.id != context.user_id
            or context.membership_status is not MembershipStatus.ACTIVE
            or context.membership_role not in {MembershipRole.OWNER, MembershipRole.ADMIN}
        ):
            raise CommercePermissionDenied("active OWNER or ADMIN is required")
        request_id = uuid5(
            NAMESPACE_URL,
            f"creative-marketer:{context.tenant_id}:commerce-sync:{connection_id}:"
            f"{idempotency_key}",
        )
        now = datetime.now(UTC)
        async with self._factory() as session, session.begin():
            await self._tenant(session, context.tenant_id)
            exists = await session.scalar(
                select(connections.c.id).where(
                    connections.c.tenant_id == context.tenant_id,
                    connections.c.id == connection_id,
                    connections.c.status == "ACTIVE",
                )
            )
            if exists is None:
                raise CommerceNotFound("active CommerceConnection not found")
            await session.execute(
                pg_insert(sync_requests)
                .values(
                    id=request_id,
                    tenant_id=context.tenant_id,
                    connection_id=connection_id,
                    requested_by_user_id=context.user_id,
                    correlation_id=context.correlation_id,
                    sync_types=[item.value for item in sync_types],
                    status=DurableSyncStatus.QUEUED.value,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "id"])
            )
            row = (
                (
                    await session.execute(
                        select(sync_requests).where(sync_requests.c.id == request_id)
                    )
                )
                .mappings()
                .one()
            )
            return CommerceSyncView(
                request_id,
                connection_id,
                str(row["status"]),
                row["safe_failure_code"],
            )

    async def latest_sync_view(
        self, tenant_id: UUID, connection_id: UUID
    ) -> CommerceSyncView | None:
        async with self._factory() as session, session.begin():
            await self._tenant(session, tenant_id)
            row = (
                (
                    await session.execute(
                        select(sync_requests)
                        .where(
                            sync_requests.c.tenant_id == tenant_id,
                            sync_requests.c.connection_id == connection_id,
                        )
                        .order_by(sync_requests.c.created_at.desc())
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
            return (
                None
                if row is None
                else CommerceSyncView(
                    row["id"], connection_id, str(row["status"]), row["safe_failure_code"]
                )
            )

    async def fail_sync_start(self, tenant_id: UUID, request_id: UUID) -> None:
        async with self._factory() as session, session.begin():
            await self._tenant(session, tenant_id)
            await session.execute(
                update(sync_requests)
                .where(
                    sync_requests.c.tenant_id == tenant_id,
                    sync_requests.c.id == request_id,
                    sync_requests.c.status == DurableSyncStatus.QUEUED.value,
                )
                .values(
                    status=DurableSyncStatus.FAILED.value,
                    safe_failure_code="COMMERCE_WORKFLOW_START_FAILED",
                    updated_at=datetime.now(UTC),
                )
            )


@dataclass(slots=True)
class SqlAlchemyTrustedWorkflowToolRequestResolver(TrustedWorkflowToolRequestResolver):
    factory: async_sessionmaker[AsyncSession]

    async def resolve(
        self, request: ToolWorkflowInput
    ) -> tuple[TrustedAgentInvocation, ToolInvocationRequest]:
        invocation, tool_request, proposal_id, _ = await self.resolve_commerce(
            UUID(request.tenant_id), request.request_ref
        )
        if (
            str(invocation.requested_agent_definition_id) != request.requested_agent_definition_id
            or tool_request.operation_id != request.operation_id
            or tool_request.tool_key != request.tool_key
            or str(proposal_id) == ""
        ):
            raise CommercePermissionDenied("workflow locator does not match durable ToolCall")
        return invocation, tool_request

    async def resolve_commerce(
        self, tenant_id: UUID, request_ref: str, expected_proposal_id: UUID | None = None
    ) -> tuple[TrustedAgentInvocation, ToolInvocationRequest, UUID, UUID | None]:
        match = _REQUEST_REF.fullmatch(request_ref)
        if match is None:
            raise CommercePermissionDenied("invalid opaque Commerce request reference")
        call_id = UUID(hex=match.group(1))
        async with self.factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            call = await SqlAlchemyToolCallRepository(session).get(call_id)
            if call is None or call.tenant_id != tenant_id:
                raise CommercePermissionDenied("durable ToolCall was not found for tenant")
            if call.binding.resource_type != "commerce_action_proposal":
                raise CommercePermissionDenied("durable request resource type is invalid")
            proposal_id = UUID(cast(str, call.binding.resource_id))
            if expected_proposal_id is not None and proposal_id != expected_proposal_id:
                raise CommercePermissionDenied("proposal does not match durable request")
            expected_input = NormalizedToolInput.from_trusted_value(
                {"commerce_action_proposal_id": str(proposal_id)}
            )
            if expected_input.digest != call.binding.normalized_input_digest:
                raise CommercePermissionDenied("canonical Commerce input digest changed")
            approval = None
            if call.approval_request_id is not None:
                approval = (
                    (
                        await session.execute(
                            select(approval_requests).where(
                                approval_requests.c.tenant_id == tenant_id,
                                approval_requests.c.id == call.approval_request_id,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
            if (
                approval is None
                or approval["requested_by_actor_kind"] != ActorKind.USER.value
                or approval["action_digest"] != call.action_digest
            ):
                raise CommercePermissionDenied("exact human approval request is missing")
            user_id = cast(UUID, approval["requested_by_actor_id"])
            identity = (
                (
                    await session.execute(
                        select(
                            users.c.status.label("user_status"),
                            memberships.c.role,
                            memberships.c.status.label("membership_status"),
                            tenants.c.status.label("tenant_status"),
                            agent_definitions.c.status.label("agent_status"),
                            agent_definitions.c.agent_type,
                        )
                        .join(memberships, memberships.c.user_id == users.c.id)
                        .join(tenants, tenants.c.id == memberships.c.tenant_id)
                        .join(
                            agent_definitions,
                            agent_definitions.c.id == call.binding.requested_agent_definition_id,
                        )
                        .where(
                            users.c.id == user_id,
                            memberships.c.tenant_id == tenant_id,
                            agent_definitions.c.tenant_id == tenant_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                identity is None
                or identity["user_status"] != "active"
                or identity["membership_status"] != MembershipStatus.ACTIVE.value
                or identity["tenant_status"] != "active"
                or identity["agent_status"] != "active"
                or identity["agent_type"] != "commerce_execution_workload"
            ):
                raise CommercePermissionDenied("current initiating authority is inactive")
            context = ExecutionContext(
                tenant_id,
                Actor(ActorKind.USER, user_id),
                user_id,
                MembershipRole(identity["role"]),
                MembershipStatus(identity["membership_status"]),
                call.binding.environment,
                AuthenticationAssurance(
                    datetime.now(UTC),
                    "durable-tool-request",
                    "postgresql-authority",
                    request_ref,
                ),
                call.correlation_id,
            )
            return (
                TrustedAgentInvocation(context, call.binding.requested_agent_definition_id),
                ToolInvocationRequest(
                    call.binding.tool_key,
                    {"commerce_action_proposal_id": str(proposal_id)},
                    call.operation_id,
                ),
                proposal_id,
                call.idempotency_record_id,
            )


@dataclass(slots=True)
class GovernedCommerceActionExecutor(CommerceActionExecutor):
    gateway: ToolGateway
    resolver: SqlAlchemyTrustedWorkflowToolRequestResolver
    idempotency: SqlAlchemyIdempotencyUnitOfWorkFactory

    async def submit(
        self, tenant_id: UUID, proposal_id: UUID, request_ref: str
    ) -> CommerceActionActivityResult:
        invocation, request, persisted_proposal, _ = await self.resolver.resolve_commerce(
            tenant_id, request_ref, proposal_id
        )
        if persisted_proposal != proposal_id:
            raise CommercePermissionDenied("proposal authority mismatch")
        result = await self.gateway.invoke(invocation, request)
        return self._result(proposal_id, result.status, result.result_ref, result.reason_code)

    async def reconcile(
        self, tenant_id: UUID, proposal_id: UUID, request_ref: str
    ) -> CommerceActionActivityResult:
        invocation, original, _, record_id = await self.resolver.resolve_commerce(
            tenant_id, request_ref, proposal_id
        )
        status_operation = (
            "op_"
            + uuid5(
                NAMESPACE_URL,
                f"creative-marketer:{tenant_id}:commerce:{proposal_id}:operation-status:v1",
            ).hex
        )
        status = await self.gateway.invoke(
            invocation,
            ToolInvocationRequest(
                "commerce.operation.status",
                {"commerce_action_proposal_id": str(proposal_id)},
                status_operation,
            ),
        )
        if status.status in {GatewayStatus.EXECUTED, GatewayStatus.REPLAYED}:
            if record_id is None or status.result_ref is None:
                raise CommercePermissionDenied("unknown outcome ownership is missing")
            await ReconcileUnknownOutcome(self.idempotency)(
                invocation.initiating_context,
                record_id,
                ReconciliationOutcome.EFFECT_CONFIRMED,
                result_ref=status.result_ref,
            )
            final = await self.gateway.invoke(invocation, original)
            return self._result(proposal_id, final.status, final.result_ref, final.reason_code)
        return self._result(proposal_id, status.status, status.result_ref, status.reason_code)

    @staticmethod
    def _result(
        proposal_id: UUID,
        status: GatewayStatus,
        result_ref: str | None,
        reason: str | None,
    ) -> CommerceActionActivityResult:
        mapped = {
            GatewayStatus.AWAITING_APPROVAL: "AWAITING_APPROVAL",
            GatewayStatus.EXECUTED: "SUCCEEDED",
            GatewayStatus.REPLAYED: "SUCCEEDED",
            GatewayStatus.UNKNOWN_OUTCOME: "OUTCOME_UNKNOWN",
            GatewayStatus.BLOCKED_RECONCILIATION: "OUTCOME_UNKNOWN",
            GatewayStatus.IN_PROGRESS: "SUBMITTING",
        }.get(status, "FAILED")
        return CommerceActionActivityResult(str(proposal_id), mapped, result_ref, reason)


@dataclass(slots=True)
class SqlAlchemyCommerceSyncExecutor(CommerceSyncExecutor):
    factory: async_sessionmaker[AsyncSession]
    service: CommerceService
    provider: FakeCommerceProvider
    environment: str

    async def sync(
        self,
        tenant_id: UUID,
        sync_request_id: UUID,
        connection_id: UUID,
        sync_type: str,
        cursor: str | None,
    ) -> CommerceSyncActivityResult:
        # The deterministic workflow ID selects the one active request for this connection.
        async with self.factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            row = (
                (
                    await session.execute(
                        select(sync_requests, connections.c.external_store_id)
                        .join(
                            connections,
                            (connections.c.tenant_id == sync_requests.c.tenant_id)
                            & (connections.c.id == sync_requests.c.connection_id),
                        )
                        .where(
                            sync_requests.c.tenant_id == tenant_id,
                            sync_requests.c.id == sync_request_id,
                            sync_requests.c.connection_id == connection_id,
                            sync_requests.c.status.in_(["QUEUED", "RUNNING"]),
                        )
                        .order_by(sync_requests.c.created_at.desc())
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise CommercePermissionDenied("durable Commerce sync request is unavailable")
            identity = (
                (
                    await session.execute(
                        select(
                            users.c.status.label("user_status"),
                            memberships.c.role,
                            memberships.c.status.label("membership_status"),
                            tenants.c.status.label("tenant_status"),
                        )
                        .join(memberships, memberships.c.user_id == users.c.id)
                        .join(tenants, tenants.c.id == memberships.c.tenant_id)
                        .where(
                            users.c.id == row["requested_by_user_id"],
                            memberships.c.tenant_id == tenant_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                identity is None
                or identity["user_status"] != "active"
                or identity["membership_status"] != "active"
                or identity["tenant_status"] != "active"
            ):
                raise CommercePermissionDenied("sync initiating authority was revoked")
            await session.execute(
                update(sync_requests)
                .where(sync_requests.c.id == row["id"])
                .values(status="RUNNING", updated_at=datetime.now(UTC))
            )
            confirmed = (
                (
                    await session.execute(
                        select(
                            action_proposals.c.action_type,
                            action_proposals.c.external_variant_id,
                            action_proposals.c.external_order_id,
                            action_proposals.c.exact_quantity,
                            action_proposals.c.exact_amount,
                            action_proposals.c.currency,
                            action_results.c.external_operation_id,
                        )
                        .join(
                            action_results,
                            (action_results.c.tenant_id == action_proposals.c.tenant_id)
                            & (action_results.c.proposal_id == action_proposals.c.id),
                        )
                        .where(
                            action_proposals.c.tenant_id == tenant_id,
                            action_proposals.c.connection_id == connection_id,
                            action_results.c.status == OperationStatus.SUCCEEDED.value,
                        )
                    )
                )
                .mappings()
                .all()
            )
        self.provider.seed_store(str(row["external_store_id"]))
        for action in confirmed:
            effect: dict[str, object]
            if action["action_type"] == ActionType.INVENTORY_ADJUSTMENT.value:
                effect = {
                    "kind": "SET_AVAILABLE_TO",
                    "store": str(row["external_store_id"]),
                    "variant": str(action["external_variant_id"]),
                    "quantity": int(action["exact_quantity"]),
                }
            else:
                effect = {
                    "kind": "REFUND",
                    "store": str(row["external_store_id"]),
                    "order": str(action["external_order_id"]),
                    "amount": action["exact_amount"],
                    "currency": str(action["currency"]),
                }
            operation_id = str(action["external_operation_id"])
            if operation_id not in self.provider.operations:
                self.provider.restore_unknown_operation(operation_id, effect)
                await self.provider.get_operation_status(
                    str(row["external_store_id"]), operation_id
                )
        context = ExecutionContext(
            tenant_id,
            Actor(ActorKind.USER, row["requested_by_user_id"]),
            row["requested_by_user_id"],
            MembershipRole(identity["role"]),
            MembershipStatus.ACTIVE,
            self.environment,
            AuthenticationAssurance(
                datetime.now(UTC), "durable-sync-request", "postgresql-authority"
            ),
            row["correlation_id"],
        )
        try:
            summary = await self.service.sync(
                context, connection_id, SyncType(sync_type), cursor=cursor
            )
        except Exception:
            async with self.factory() as session, session.begin():
                await session.execute(
                    text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(tenant_id)},
                )
                await session.execute(
                    update(sync_requests)
                    .where(sync_requests.c.id == row["id"])
                    .values(
                        status="FAILED",
                        safe_failure_code="COMMERCE_SYNC_FAILED",
                        updated_at=datetime.now(UTC),
                    )
                )
            return CommerceSyncActivityResult(
                str(connection_id), sync_type, "FAILED", safe_failure_code="COMMERCE_SYNC_FAILED"
            )
        if summary.next_cursor is None and sync_type == row["sync_types"][-1]:
            async with self.factory() as session, session.begin():
                await session.execute(
                    text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(tenant_id)},
                )
                await session.execute(
                    update(sync_requests)
                    .where(sync_requests.c.id == row["id"])
                    .values(status="SUCCEEDED", updated_at=datetime.now(UTC))
                )
        return CommerceSyncActivityResult(
            str(connection_id), sync_type, "SUCCEEDED", summary.next_cursor
        )
