# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from creative_marketer.agent_governance.application import (
    ActivateAgentVersion,
    CreateAgentVersion,
    CreateTenantAgentDefinition,
)
from creative_marketer.approval_governance.application import DecideApproval
from creative_marketer.approval_governance.domain import HumanDecision
from creative_marketer.commerce.application import CommerceService
from creative_marketer.commerce.domain import (
    ActionType,
    CommerceActionProposal,
    CommerceNotFound,
    CommercePermissionDenied,
    SyncType,
    proposal_digest,
)
from creative_marketer.commerce.execution import CommerceWorkloadIdentity
from creative_marketer.commerce.gateway_composition import CommerceGatewayFactory
from creative_marketer.commerce.provider import FakeCommerceProvider, MutationDisposition
from creative_marketer.commerce.tool_contracts import commerce_tool_contracts
from creative_marketer.commerce.workflow_execution import CommerceGovernedExecutionService
from creative_marketer.identity.application.authentication import ActorKind
from creative_marketer.infrastructure.database.commerce_authority import (
    SqlAlchemyCommerceExecutionAuthority,
)
from creative_marketer.infrastructure.database.commerce_uow import (
    SqlAlchemyCommerceUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.commerce_worker import (
    GovernedCommerceActionExecutor,
    SqlAlchemyCommerceGovernanceStore,
    SqlAlchemyCommerceSyncExecutor,
    SqlAlchemyTrustedWorkflowToolRequestResolver,
)
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.execution_control_uow import (
    SqlAlchemyIdempotencyUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.schema import memberships
from creative_marketer.permission_governance.application import (
    ActivateToolPermissionVersion,
    CreateToolPermission,
    CreateToolPermissionVersion,
)
from creative_marketer.permission_governance.domain import (
    PermissionEffect,
    ToolPermissionVersionConfiguration,
)
from creative_marketer.tool_governance.application import (
    ActivateToolVersion,
    CreateToolDefinition,
    CreateToolVersion,
    PlatformControlContext,
)
from creative_marketer.workflow_orchestration.contracts import (
    CommerceActionWorkflowInput,
    CommerceSyncWorkflowInput,
    ToolWorkflowInput,
)
from scripts.bootstrap_commerce_agent import commerce_execution_configuration
from tests.integration.test_asset_library import product_setup


@dataclass
class WorkflowRecorder:
    actions: list[CommerceActionWorkflowInput] = field(default_factory=list)
    signals: list[CommerceActionWorkflowInput] = field(default_factory=list)
    syncs: list[CommerceSyncWorkflowInput] = field(default_factory=list)
    fail_sync_start: bool = False

    async def start_action(self, request: CommerceActionWorkflowInput) -> None:
        if request not in self.actions:
            self.actions.append(request)

    async def signal_action(self, request: CommerceActionWorkflowInput) -> None:
        self.signals.append(request)

    async def start_sync(self, request: CommerceSyncWorkflowInput) -> None:
        if self.fail_sync_start:
            raise RuntimeError("Temporal unavailable")
        if request not in self.syncs:
            self.syncs.append(request)


def _proposal(
    tenant_id: UUID,
    connection_id: UUID,
    product_id: UUID,
    action: ActionType,
) -> CommerceActionProposal:
    evidence = ({"kind": "observation", "id": str(uuid4()), "digest": "sha256:" + "e" * 64},)
    reason = "Exact integration-reviewed action"
    material: dict[str, object] = {
        "connection_id": str(connection_id),
        "product_id": str(product_id),
        "action_type": action.value,
    }
    if action is ActionType.INVENTORY_ADJUSTMENT:
        material.update(
            {
                "external_product_id": "fake-product-1",
                "external_variant_id": "fake-variant-1",
                "exact_quantity": 8,
                "reason": reason,
                "evidence_refs": [dict(value) for value in evidence],
                "strategy": "SET_AVAILABLE_TO",
                "schema_version": 1,
            }
        )
        return CommerceActionProposal(
            tenant_id,
            connection_id,
            product_id,
            action,
            "fake-product-1",
            "fake-variant-1",
            None,
            8,
            None,
            None,
            reason,
            evidence,
            proposal_digest(material),
        )
    material.update(
        {
            "external_order_id": "fake-order-paid",
            "exact_amount": "10",
            "currency": "USD",
            "reason": reason,
            "evidence_refs": [dict(value) for value in evidence],
            "schema_version": 1,
        }
    )
    return CommerceActionProposal(
        tenant_id,
        connection_id,
        product_id,
        action,
        None,
        None,
        "fake-order-paid",
        None,
        Decimal("10"),
        "USD",
        reason,
        evidence,
        proposal_digest(material),
    )


async def _governance(
    context,
    sessions,
    agent_registry_factory,
    tool_control_factory,
    permission_factory,
    provider,
):
    definition = await CreateTenantAgentDefinition(agent_registry_factory)(
        context,
        agent_key="commerce_execution_workload",
        agent_type="commerce_execution_workload",
    )
    version = await CreateAgentVersion(agent_registry_factory)(
        context, definition.id, commerce_execution_configuration()
    )
    await ActivateAgentVersion(agent_registry_factory)(context, definition.id, version.id)
    control = PlatformControlContext(ActorKind.WORKLOAD, uuid4(), "test", uuid4())
    for contract in commerce_tool_contracts():
        tool = await CreateToolDefinition(tool_control_factory)(
            control, tool_key=contract.tool_key, category="commerce"
        )
        tool_version = await CreateToolVersion(tool_control_factory)(
            control, tool.id, contract.configuration
        )
        await ActivateToolVersion(tool_control_factory)(control, tool.id, tool_version.id)
        permission = await CreateToolPermission(permission_factory)(context, definition.id, tool.id)
        permission_version = await CreateToolPermissionVersion(permission_factory)(
            context,
            permission.id,
            ToolPermissionVersionConfiguration(
                PermissionEffect.GRANT, ("commerce.operations",), ("test",)
            ),
        )
        await ActivateToolPermissionVersion(permission_factory)(
            context, permission.id, permission_version.id
        )
    authority = SqlAlchemyCommerceExecutionAuthority(
        sessions, CommerceWorkloadIdentity(uuid4(), "integration-commerce-worker", "test")
    )
    gateway = await CommerceGatewayFactory(sessions, authority, provider)()
    workflows = WorkflowRecorder()

    async def gateway_factory():
        return gateway

    governance = CommerceGovernedExecutionService(
        SqlAlchemyCommerceGovernanceStore(sessions), gateway_factory, workflows
    )
    executor = GovernedCommerceActionExecutor(
        gateway,
        SqlAlchemyTrustedWorkflowToolRequestResolver(sessions),
        SqlAlchemyIdempotencyUnitOfWorkFactory(sessions),
    )
    return definition.id, governance, workflows, executor


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_governed_inventory_refund_rejection_and_durable_resolution(
    admin_engine: AsyncEngine,
    runtime_database_url: str,
    catalog_factory,
    agent_registry_factory,
    tool_control_factory,
    permission_factory,
    approval_factory,
) -> None:
    context, _, product = await product_setup(admin_engine, catalog_factory)
    sessions = create_session_factory(runtime_database_url)
    provider = FakeCommerceProvider(page_size=10)
    commerce_uow = SqlAlchemyCommerceUnitOfWorkFactory(sessions)
    commerce = CommerceService(commerce_uow, provider)
    connection = await commerce.create_fake_connection(context)
    provider.seed_store(connection.external_store_id)
    for sync_type in (SyncType.CATALOG, SyncType.INVENTORY, SyncType.ORDERS):
        await commerce.sync(context, connection.id, sync_type)
    await commerce.map_product(
        context,
        product_id=product.id,
        connection_id=connection.id,
        external_product_id="fake-product-1",
        external_variant_id="fake-variant-1",
    )
    proposals = [
        _proposal(context.tenant_id, connection.id, product.id, ActionType.INVENTORY_ADJUSTMENT),
        _proposal(context.tenant_id, connection.id, product.id, ActionType.REFUND),
        _proposal(context.tenant_id, connection.id, product.id, ActionType.INVENTORY_ADJUSTMENT),
    ]
    async with commerce_uow(context.tenant_id) as uow:
        for proposal in proposals:
            assert await uow.commerce.add_proposal(proposal)
        await uow.commit()

    definition_id, governance, workflows, executor = await _governance(
        context,
        sessions,
        agent_registry_factory,
        tool_control_factory,
        permission_factory,
        provider,
    )
    inventory, refund, rejected = proposals

    pending = await governance.request_action(context, inventory.id)
    duplicate = await governance.request_action(context, inventory.id)
    assert pending.operation_id == duplicate.operation_id
    assert pending.state == "NEEDS_APPROVAL" and pending.risk_level == "R5"
    assert len(workflows.actions) == 1
    assert pending.approval_request_id is not None and pending.request_ref is not None
    await DecideApproval(approval_factory)(
        context, pending.approval_request_id, HumanDecision.APPROVE
    )
    await governance.wake_action(context, inventory.id)
    inventory_result = await executor.submit(context.tenant_id, inventory.id, pending.request_ref)
    assert inventory_result.status == "SUCCEEDED"
    assert len(provider.operations) == 1
    assert (
        await governance.store.action_view(context.tenant_id, inventory.id)
    ).state == "SUCCEEDED"

    refund_pending = await governance.request_action(context, refund.id)
    assert refund_pending.risk_level == "R6" and refund_pending.approval_request_id is not None
    await DecideApproval(approval_factory)(
        context, refund_pending.approval_request_id, HumanDecision.APPROVE
    )
    provider.next_mutation_disposition = MutationDisposition.OUTCOME_UNKNOWN
    unknown = await executor.submit(
        context.tenant_id,
        refund.id,
        refund_pending.request_ref,
    )
    assert unknown.status == "OUTCOME_UNKNOWN" and len(provider.operations) == 2
    reconciled = await executor.reconcile(
        context.tenant_id,
        refund.id,
        refund_pending.request_ref,
    )
    assert reconciled.status == "SUCCEEDED" and len(provider.operations) == 2
    replay = await executor.submit(
        context.tenant_id,
        refund.id,
        refund_pending.request_ref,
    )
    assert replay.status == "SUCCEEDED" and len(provider.operations) == 2

    denied = await governance.request_action(context, rejected.id)
    assert denied.approval_request_id is not None and denied.request_ref is not None
    await DecideApproval(approval_factory)(context, denied.approval_request_id, HumanDecision.DENY)
    denied_result = await executor.submit(context.tenant_id, rejected.id, denied.request_ref)
    assert denied_result.status == "FAILED" and len(provider.operations) == 2

    resolver = executor.resolver
    workflow_request = ToolWorkflowInput(
        str(context.tenant_id),
        str(definition_id),
        pending.operation_id,
        pending.tool_key,
        str(context.correlation_id),
        pending.request_ref,
    )
    resolved, tool_request = await resolver.resolve(workflow_request)
    assert resolved.initiating_context.user_id == context.user_id
    assert tool_request.raw_input == {"commerce_action_proposal_id": str(inventory.id)}
    with pytest.raises(CommercePermissionDenied):
        await resolver.resolve(
            ToolWorkflowInput(
                str(context.tenant_id),
                str(definition_id),
                "op_" + uuid4().hex,
                pending.tool_key,
                str(context.correlation_id),
                pending.request_ref,
            )
        )
    with pytest.raises(CommercePermissionDenied):
        await resolver.resolve_commerce(uuid4(), pending.request_ref)
    with pytest.raises(CommercePermissionDenied, match="invalid opaque"):
        await resolver.resolve_commerce(context.tenant_id, "not-a-request-reference")
    with pytest.raises(CommercePermissionDenied, match="proposal does not match"):
        await resolver.resolve_commerce(context.tenant_id, pending.request_ref, uuid4())
    with pytest.raises(CommerceNotFound):
        await governance.store.proposal_action(context.tenant_id, uuid4())
    with pytest.raises(CommerceNotFound):
        await governance.store.proposal_for_approval(context.tenant_id, uuid4())
    with pytest.raises(CommerceNotFound):
        await governance.store.action_view(context.tenant_id, uuid4())
    with pytest.raises(CommercePermissionDenied, match="exactly one"):
        await governance.store.commerce_agent_definition(uuid4())

    sync_types = (SyncType.CATALOG, SyncType.INVENTORY, SyncType.ORDERS)
    sync = await governance.request_sync(context, connection.id, sync_types, "manual-sync-1")
    duplicate_sync = await governance.request_sync(
        context, connection.id, sync_types, "manual-sync-1"
    )
    assert sync.request_id == duplicate_sync.request_id and len(workflows.syncs) == 1
    restarted_provider = FakeCommerceProvider(page_size=10)
    sync_executor = SqlAlchemyCommerceSyncExecutor(
        sessions,
        CommerceService(commerce_uow, restarted_provider),
        restarted_provider,
        "test",
    )
    for sync_type in sync_types:
        outcome = await sync_executor.sync(
            context.tenant_id, sync.request_id, connection.id, sync_type.value, None
        )
        assert outcome.status == "SUCCEEDED"
    latest_sync = await governance.store.latest_sync_view(context.tenant_id, connection.id)
    assert latest_sync is not None and latest_sync.status == "SUCCEEDED"
    workspace = await commerce.workspace(context, product.id)
    assert workspace["inventory"][0].available_quantity == 8

    failed_provider = FakeCommerceProvider(page_size=10, fail_next_read=True)
    failed_request = await governance.request_sync(
        context, connection.id, sync_types, "provider-read-failure"
    )
    failed_executor = SqlAlchemyCommerceSyncExecutor(
        sessions,
        CommerceService(commerce_uow, failed_provider),
        failed_provider,
        "test",
    )
    failed_outcome = await failed_executor.sync(
        context.tenant_id,
        failed_request.request_id,
        connection.id,
        SyncType.CATALOG.value,
        None,
    )
    assert failed_outcome.status == "FAILED"

    revoked_request = await governance.request_sync(
        context, connection.id, sync_types, "revoked-initiator"
    )

    workflows.fail_sync_start = True
    with pytest.raises(RuntimeError, match="Temporal unavailable"):
        await governance.request_sync(context, connection.id, sync_types, "failed-start")
    failed_sync = await governance.store.latest_sync_view(context.tenant_id, connection.id)
    assert failed_sync is not None
    assert failed_sync.status == "FAILED"
    assert failed_sync.safe_failure_code == "COMMERCE_WORKFLOW_START_FAILED"

    async with admin_engine.begin() as connection_admin:
        await connection_admin.execute(
            update(memberships)
            .where(
                memberships.c.tenant_id == context.tenant_id,
                memberships.c.user_id == context.user_id,
            )
            .values(status="inactive")
        )
    with pytest.raises(CommercePermissionDenied, match="inactive"):
        await resolver.resolve(workflow_request)
    with pytest.raises(CommercePermissionDenied, match="revoked"):
        await sync_executor.sync(
            context.tenant_id,
            revoked_request.request_id,
            connection.id,
            SyncType.CATALOG.value,
            None,
        )
