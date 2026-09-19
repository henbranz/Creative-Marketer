# mypy: disable-error-code="no-untyped-def,no-untyped-call,index"

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from creative_marketer.agent_governance.application import (
    ActivateAgentVersion,
    CreateAgentVersion,
    CreateTenantAgentDefinition,
)
from creative_marketer.agent_runtime.application import (
    AgentRunService,
    ModelProviderRegistry,
    ModelRouter,
    WorkloadIdentity,
    initial_commerce_operations_route,
)
from creative_marketer.agent_runtime.domain import (
    AgentRunStatus,
    ModelInvocationResult,
    ModelUsage,
)
from creative_marketer.catalog.application import CatalogService
from creative_marketer.commerce.application import CommerceService
from creative_marketer.commerce.domain import (
    ActionType,
    CommerceNotFound,
    PaymentState,
    SyncType,
)
from creative_marketer.commerce.execution import CommerceToolExecutor, normalize_commerce_input
from creative_marketer.commerce.provider import (
    FakeCommerceProvider,
    MutationDisposition,
    ProviderInventory,
    ProviderOrder,
)
from creative_marketer.infrastructure.database.agent_runtime_uow import (
    SqlAlchemyAgentRuntimeUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.commerce_authority import (
    SqlAlchemyCommerceExecutionAuthority,
)
from creative_marketer.infrastructure.database.commerce_uow import (
    SqlAlchemyCommerceUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.model_providers.fake import FakeModelProvider
from creative_marketer.tool_execution.domain import OutcomeUnknown, ToolExecutionContext
from scripts.bootstrap_commerce_agent import commerce_configuration
from tests.integration.test_asset_library import product_setup


class IdentityProvider:
    async def current(self) -> WorkloadIdentity:
        return WorkloadIdentity("integration-commerce-agent", "test")


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_commerce_agent_and_governed_unknown_outcome_are_end_to_end(
    admin_engine: AsyncEngine,
    runtime_database_url: str,
    catalog_factory,
    agent_registry_factory,
) -> None:
    context, _, product = await product_setup(admin_engine, catalog_factory)
    await CatalogService(catalog_factory).create_snapshot(context, product.id)
    definition = await CreateTenantAgentDefinition(agent_registry_factory)(
        context, agent_key="commerce_operations", agent_type="commerce_operations"
    )
    version = await CreateAgentVersion(agent_registry_factory)(
        context, definition.id, commerce_configuration()
    )
    await ActivateAgentVersion(agent_registry_factory)(context, definition.id, version.id)

    sessions = create_session_factory(runtime_database_url)
    provider = FakeCommerceProvider(page_size=10)
    commerce = CommerceService(SqlAlchemyCommerceUnitOfWorkFactory(sessions), provider)
    connection = await commerce.create_fake_connection(context)
    provider.seed_store(connection.external_store_id)
    await commerce.sync(context, connection.id, SyncType.CATALOG)
    await commerce.map_product(
        context,
        product_id=product.id,
        connection_id=connection.id,
        external_product_id="fake-product-1",
        external_variant_id="fake-variant-1",
    )
    await commerce.sync(context, connection.id, SyncType.INVENTORY)
    await commerce.sync(context, connection.id, SyncType.ORDERS)

    def model(invocation):
        inventory = invocation.capability_context["inventory"]
        orders = invocation.capability_context["orders"]
        return ModelInvocationResult(
            {
                "summary": "Fake Store has one low-stock variant and a paid open order.",
                "inventory_exceptions": [
                    {
                        "observation_id": inventory[0]["observation_id"],
                        "explanation": "Rule-based low stock.",
                    }
                ],
                "order_exceptions": [
                    {
                        "observation_id": orders[0]["observation_id"],
                        "explanation": "Paid but unfulfilled.",
                    }
                ],
                "action_proposals": [
                    {
                        "action_type": "INVENTORY_ADJUSTMENT",
                        "external_variant_id": "fake-variant-1",
                        "exact_quantity": 8,
                        "reason": "Restore a reviewed buffer.",
                    },
                    {
                        "action_type": "REFUND",
                        "external_order_id": "fake-order-paid",
                        "exact_amount": "10.00",
                        "currency": "USD",
                        "reason": "Issue the exact reviewed partial refund.",
                    },
                ],
                "limitations": ["Fake Store observations; human approval required."],
            },
            "fake-commerce-response",
            ModelUsage(1000, 500, 1500),
            "openai",
            "gpt-5.6-sol",
        )

    fake_model = FakeModelProvider(model)
    runtime = AgentRunService(
        SqlAlchemyAgentRuntimeUnitOfWorkFactory(sessions),
        ModelRouter((initial_commerce_operations_route(),)),
        ModelProviderRegistry({"openai": fake_model}),
        IdentityProvider(),
    )
    pending = await runtime.request_commerce_operations(
        context, product_id=product.id, idempotency_key="commerce-integration"
    )
    assert pending.status is AgentRunStatus.PENDING
    assert pending == await runtime.request_commerce_operations(
        context, product_id=product.id, idempotency_key="commerce-integration"
    )
    completed = await runtime.execute(context.tenant_id, pending.id)
    assert completed.status is AgentRunStatus.SUCCEEDED
    assert len(fake_model.calls) == 1
    workspace = await commerce.workspace(context, product.id)
    proposals = {proposal.action_type: proposal for proposal in workspace["proposals"]}
    inventory_action = proposals[ActionType.INVENTORY_ADJUSTMENT]
    refund_action = proposals[ActionType.REFUND]
    assert inventory_action.risk_level.value == "R5"
    assert refund_action.risk_level.value == "R6"

    authority = SqlAlchemyCommerceExecutionAuthority(sessions)
    inventory_context = ToolExecutionContext(
        context.tenant_id,
        uuid4(),
        "op_" + uuid4().hex,
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    inventory_input = normalize_commerce_input(
        {"commerce_action_proposal_id": str(inventory_action.id)}
    )
    inventory_result = await CommerceToolExecutor(
        "commerce.inventory.adjust", authority, provider
    ).execute(inventory_context, inventory_input)
    assert inventory_result.output["status"] == "SUCCEEDED"

    refreshed_inventory = await provider.collect_inventory(connection.external_store_id, None)
    assert isinstance(refreshed_inventory.items[0], ProviderInventory)
    assert refreshed_inventory.items[0].available_quantity == 8

    provider.next_mutation_disposition = MutationDisposition.OUTCOME_UNKNOWN
    refund_context = ToolExecutionContext(
        context.tenant_id,
        uuid4(),
        "op_" + uuid4().hex,
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    refund_input = normalize_commerce_input({"commerce_action_proposal_id": str(refund_action.id)})
    with pytest.raises(OutcomeUnknown):
        await CommerceToolExecutor("commerce.refund.submit", authority, provider).execute(
            refund_context, refund_input
        )
    assert len(provider.operations) == 2
    result = await CommerceToolExecutor("commerce.operation.status", authority, provider).execute(
        refund_context, refund_input
    )
    assert result.output["status"] == "SUCCEEDED"
    assert len(provider.operations) == 2

    refreshed_orders = await provider.list_orders(connection.external_store_id, None)
    assert isinstance(refreshed_orders.items[0], ProviderOrder)
    assert refreshed_orders.items[0].payment_state is PaymentState.PARTIALLY_REFUNDED


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_commerce_cross_tenant_authority_is_invisible(
    admin_engine: AsyncEngine,
    runtime_database_url: str,
    catalog_factory,
) -> None:
    context, _, _ = await product_setup(admin_engine, catalog_factory)
    sessions = create_session_factory(runtime_database_url)
    provider = FakeCommerceProvider()
    commerce = CommerceService(SqlAlchemyCommerceUnitOfWorkFactory(sessions), provider)
    connection = await commerce.create_fake_connection(context)
    other_tenant = uuid4()
    with pytest.raises(CommerceNotFound):
        await SqlAlchemyCommerceExecutionAuthority(sessions).authorize_resource(
            other_tenant, connection.id
        )
