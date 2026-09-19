import inspect
from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from creative_marketer.agent_runtime.application import initial_commerce_operations_route
from creative_marketer.agent_runtime.domain import canonical_digest
from creative_marketer.commerce.application import (
    validate_action_proposal,
    validate_commerce_output,
)
from creative_marketer.commerce.domain import (
    PII_FIELD_NAMES,
    ActionType,
    CommerceActionProposal,
    CommerceConnection,
    CommerceProductObservation,
    CommerceVariantObservation,
    FulfillmentObservation,
    FulfillmentState,
    InvalidCommerceProposal,
    InventoryIndicator,
    InventoryObservation,
    OrderLineObservation,
    OrderObservation,
    PaymentObservation,
    PaymentState,
    ProductCommerceMapping,
    inventory_exceptions,
    order_exceptions,
)
from creative_marketer.commerce.execution import (
    CommerceResourceResolver,
    CommerceToolExecutor,
    commerce_tool_bindings,
    normalize_commerce_input,
)
from creative_marketer.commerce.measurement import MeasurementConversionSink
from creative_marketer.commerce.provider import (
    FakeCommerceProvider,
    MutationDisposition,
    ProviderMutationResult,
)
from creative_marketer.commerce.tool_contracts import commerce_tool_contracts
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.tool_execution.application import ResourceAccessDenied
from creative_marketer.tool_execution.domain import (
    OutcomeUnknown,
    PreEffectFailure,
    ToolExecutionContext,
)
from creative_marketer.tool_governance.domain import (
    CredentialBoundary,
    IdempotencyRequirement,
    ResolvedToolVersion,
    RiskLevel,
    SideEffectClass,
)

NOW = datetime(2026, 9, 19, 9, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def inventory(*, quantity: int | None = 2) -> InventoryObservation:
    return InventoryObservation(
        uuid4(), uuid4(), "product-1", "variant-1", quantity, NOW, DIGEST, "SKU-1"
    )


def order(
    *,
    payment: PaymentState = PaymentState.PAID,
    fulfillment: FulfillmentState = FulfillmentState.UNFULFILLED,
) -> OrderObservation:
    tenant, connection = uuid4(), uuid4()
    return OrderObservation(
        tenant,
        connection,
        "order-1",
        "SAFE-1",
        "USD",
        Decimal("40"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("40"),
        payment,
        fulfillment,
        NOW,
        NOW,
        NOW,
        (
            OrderLineObservation(
                "line-1", "product-1", "variant-1", "SKU-1", 1, Decimal("40"), "USD"
            ),
        ),
        DIGEST,
    )


def proposal(action: ActionType = ActionType.INVENTORY_ADJUSTMENT) -> CommerceActionProposal:
    tenant_id, connection_id, product_id = uuid4(), uuid4(), uuid4()
    evidence_refs = ({"kind": "observation", "id": str(uuid4()), "digest": DIGEST},)
    if action is ActionType.INVENTORY_ADJUSTMENT:
        return CommerceActionProposal(
            tenant_id,
            connection_id,
            product_id,
            action,
            external_product_id="product-1",
            external_variant_id="variant-1",
            external_order_id=None,
            exact_quantity=7,
            exact_amount=None,
            currency=None,
            reason="Exact human-reviewed action",
            evidence_refs=evidence_refs,
            semantic_digest=DIGEST,
        )
    return CommerceActionProposal(
        tenant_id,
        connection_id,
        product_id,
        action,
        external_product_id=None,
        external_variant_id=None,
        external_order_id="order-1",
        exact_quantity=None,
        exact_amount=Decimal("10"),
        currency="USD",
        reason="Exact human-reviewed action",
        evidence_refs=evidence_refs,
        semantic_digest=DIGEST,
    )


def test_normalized_contracts_are_privacy_minimal_and_connection_has_no_credentials() -> None:
    contract_types = (
        CommerceConnection,
        CommerceProductObservation,
        CommerceVariantObservation,
        InventoryObservation,
        OrderLineObservation,
        OrderObservation,
        PaymentObservation,
        FulfillmentObservation,
        ProductCommerceMapping,
    )
    for contract in contract_types:
        assert set(field.name for field in fields(contract)).isdisjoint(PII_FIELD_NAMES)
    connection_fields = {field.name for field in fields(CommerceConnection)}
    assert connection_fields.isdisjoint({"access_token", "refresh_token", "client_secret"})


def test_inventory_unavailable_is_not_zero_and_rules_are_deterministic() -> None:
    unavailable = inventory(quantity=None)
    zero = inventory(quantity=0)
    low = inventory(quantity=5)
    assert unavailable.indicator() is InventoryIndicator.UNAVAILABLE
    assert zero.indicator() is InventoryIndicator.OUT_OF_STOCK
    assert low.indicator() is InventoryIndicator.LOW_STOCK
    assert [item["kind"] for item in inventory_exceptions((unavailable, zero, low))] == [
        "OUT_OF_STOCK",
        "LOW_STOCK",
    ]


def test_money_currency_and_order_exception_rules_are_conservative() -> None:
    assert order().refundable_amount == Decimal("40")
    failed = order(payment=PaymentState.FAILED)
    assert failed.refundable_amount == Decimal("0")
    assert {item["kind"] for item in order_exceptions((order(), failed))} == {
        "PAID_BUT_UNFULFILLED",
        "PAYMENT_FAILED",
    }
    with pytest.raises(ValueError, match="ISO 4217"):
        OrderLineObservation("line", "product", None, None, 1, Decimal("1"), "usd")
    with pytest.raises(ValueError, match="non-negative"):
        OrderLineObservation("line", "product", None, None, 1, Decimal("-1"), "USD")


def test_domain_rejects_invalid_sources_and_covers_partial_refund_rule() -> None:
    with pytest.raises(ValueError, match="explicitly identified"):
        CommerceConnection(uuid4(), "", "", "", "", ())
    with pytest.raises(ValueError, match="explicit external product"):
        ProductCommerceMapping(uuid4(), uuid4(), uuid4(), "", uuid4())
    with pytest.raises(ValueError, match="timezone-aware"):
        InventoryObservation(
            uuid4(), uuid4(), "product", "variant", 1, datetime(2026, 1, 1), DIGEST
        )
    with pytest.raises(ValueError, match="positive"):
        inventory().indicator(0)
    assert order_exceptions((order(payment=PaymentState.PARTIALLY_REFUNDED),))[0]["kind"] == (
        "PARTIAL_REFUND"
    )


def test_action_validator_rejects_invented_targets_and_excess_refunds() -> None:
    inv = inventory()
    observed_order = order()
    tenant, product, connection = inv.tenant_id, uuid4(), inv.connection_id
    accepted = validate_action_proposal(
        tenant_id=tenant,
        product_id=product,
        connection_id=connection,
        candidate={
            "action_type": "INVENTORY_ADJUSTMENT",
            "external_variant_id": inv.external_variant_id,
            "exact_quantity": 8,
            "reason": "Restock",
        },
        inventories=(inv,),
        orders=(),
        evidence_refs=({"kind": "inventory_observation", "id": str(inv.id), "digest": DIGEST},),
    )
    assert accepted.exact_quantity == 8
    assert accepted.risk_level is RiskLevel.R5
    with pytest.raises(InvalidCommerceProposal):
        validate_action_proposal(
            tenant_id=tenant,
            product_id=product,
            connection_id=connection,
            candidate={
                "action_type": "INVENTORY_ADJUSTMENT",
                "external_variant_id": "invented",
                "exact_quantity": 8,
                "reason": "Restock",
            },
            inventories=(inv,),
            orders=(),
            evidence_refs=(),
        )
    with pytest.raises(InvalidCommerceProposal, match="unsupported action"):
        validate_action_proposal(
            tenant_id=tenant,
            product_id=product,
            connection_id=connection,
            candidate={},
            inventories=(inv,),
            orders=(),
            evidence_refs=(),
        )
    with pytest.raises(InvalidCommerceProposal, match="not an observed order"):
        validate_action_proposal(
            tenant_id=observed_order.tenant_id,
            product_id=product,
            connection_id=observed_order.connection_id,
            candidate={
                "action_type": "REFUND",
                "external_order_id": "invented",
                "exact_amount": "1",
                "currency": "USD",
                "reason": "Invalid target",
            },
            inventories=(),
            orders=(observed_order,),
            evidence_refs=(),
        )
    with pytest.raises(InvalidCommerceProposal):
        validate_action_proposal(
            tenant_id=observed_order.tenant_id,
            product_id=product,
            connection_id=observed_order.connection_id,
            candidate={
                "action_type": "REFUND",
                "external_order_id": observed_order.external_order_id,
                "exact_amount": "40.01",
                "currency": "USD",
                "reason": "Customer-approved refund",
            },
            inventories=(),
            orders=(observed_order,),
            evidence_refs=(),
        )


@pytest.mark.asyncio
async def test_fake_provider_is_paged_idempotent_and_reconciles_unknown_without_network() -> None:
    provider = FakeCommerceProvider(page_size=1)
    provider.seed_store("store")
    first = await provider.list_products("store", None)
    second = await provider.list_products("store", first.next_cursor)
    assert len(first.items) == len(second.items) == 1
    assert second.next_cursor is None
    provider.next_mutation_disposition = MutationDisposition.OUTCOME_UNKNOWN
    unknown = await provider.submit_refund(
        external_store_id="store",
        external_order_id="fake-order-paid",
        amount=Decimal("10"),
        currency="USD",
        idempotency_key="op-one",
    )
    replay = await provider.submit_refund(
        external_store_id="store",
        external_order_id="fake-order-paid",
        amount=Decimal("10"),
        currency="USD",
        idempotency_key="op-one",
    )
    assert replay == unknown
    assert (
        await provider.get_operation_status("store", unknown.operation_id)
    ).disposition is MutationDisposition.ACCEPTED
    assert "http" not in inspect.getsource(FakeCommerceProvider).lower()


def test_registered_tools_preserve_risk_idempotency_and_connector_boundary() -> None:
    contracts = {item.tool_key: item.configuration for item in commerce_tool_contracts()}
    assert contracts["commerce.inventory.adjust"].risk_level is RiskLevel.R5
    assert contracts["commerce.refund.submit"].risk_level is RiskLevel.R6
    assert contracts["commerce.operation.status"].risk_level is RiskLevel.R1
    assert (
        contracts["commerce.inventory.adjust"].side_effect_class
        is SideEffectClass.EXTERNAL_MUTATION
    )
    assert (
        contracts["commerce.refund.submit"].side_effect_class is SideEffectClass.EXTERNAL_MUTATION
    )
    assert all(
        value.idempotency_requirement is IdempotencyRequirement.REQUIRED
        and value.credential_boundary is CredentialBoundary.CONNECTOR
        for value in contracts.values()
    )


class FakeAuthority:
    def __init__(self, value: CommerceActionProposal) -> None:
        self.value = value
        self.operation_id: str | None = None
        self.results: list[ProviderMutationResult] = []

    async def authorize_resource(self, tenant_id: UUID, proposal_id: UUID) -> None:
        assert (tenant_id, proposal_id) == (self.value.tenant_id, self.value.id)

    async def prepare(
        self, tenant_id: UUID, proposal_id: UUID
    ) -> tuple[CommerceActionProposal, str]:
        await self.authorize_resource(tenant_id, proposal_id)
        return self.value, "store"

    async def operation(
        self, tenant_id: UUID, proposal_id: UUID
    ) -> tuple[CommerceActionProposal, str, str]:
        await self.authorize_resource(tenant_id, proposal_id)
        assert self.operation_id is not None
        return self.value, "store", self.operation_id

    async def begin(self, tenant_id: UUID, value: CommerceActionProposal, key: str) -> None:
        assert tenant_id == value.tenant_id
        assert key == "op-exact"

    async def record(
        self, tenant_id: UUID, value: CommerceActionProposal, result: ProviderMutationResult
    ) -> None:
        assert tenant_id == value.tenant_id
        self.operation_id = result.operation_id
        self.results.append(result)


@pytest.mark.asyncio
async def test_unknown_action_is_not_retried_and_status_tool_reconciles() -> None:
    value = proposal()
    authority = FakeAuthority(value)
    provider = FakeCommerceProvider()
    provider.seed_store("store")
    provider.next_mutation_disposition = MutationDisposition.OUTCOME_UNKNOWN
    context = ToolExecutionContext(
        value.tenant_id, uuid4(), "op-exact", uuid4(), uuid4(), uuid4(), uuid4()
    )
    normalized = normalize_commerce_input({"commerce_action_proposal_id": str(value.id)})
    with pytest.raises(OutcomeUnknown):
        await CommerceToolExecutor("commerce.inventory.adjust", authority, provider).execute(
            context, normalized
        )
    assert len(provider.operations) == 1
    result = await CommerceToolExecutor("commerce.operation.status", authority, provider).execute(
        context, normalized
    )
    assert isinstance(result.output, dict)
    assert result.output["status"] == "SUCCEEDED"
    assert len(provider.operations) == 1
    assert [item.disposition for item in authority.results] == [
        MutationDisposition.OUTCOME_UNKNOWN,
        MutationDisposition.ACCEPTED,
    ]


@pytest.mark.asyncio
async def test_refund_execution_is_exact_and_safe_failures_do_not_report_success() -> None:
    value = proposal(ActionType.REFUND)
    authority = FakeAuthority(value)
    provider = FakeCommerceProvider()
    provider.seed_store("store")
    context = ToolExecutionContext(
        value.tenant_id, uuid4(), "op-exact", uuid4(), uuid4(), uuid4(), uuid4()
    )
    normalized = normalize_commerce_input({"commerce_action_proposal_id": str(value.id)})
    result = await CommerceToolExecutor("commerce.refund.submit", authority, provider).execute(
        context, normalized
    )
    assert isinstance(result.output, dict)
    assert result.output["status"] == "SUCCEEDED"
    assert authority.results[0].disposition is MutationDisposition.ACCEPTED

    failed_value = proposal()
    failed_authority = FakeAuthority(failed_value)
    failed_provider = FakeCommerceProvider()
    failed_provider.seed_store("store")
    failed_provider.next_mutation_disposition = MutationDisposition.FAILED
    failed_context = ToolExecutionContext(
        failed_value.tenant_id, uuid4(), "op-exact", uuid4(), uuid4(), uuid4(), uuid4()
    )
    with pytest.raises(PreEffectFailure, match="rejected"):
        await CommerceToolExecutor(
            "commerce.inventory.adjust", failed_authority, failed_provider
        ).execute(
            failed_context,
            normalize_commerce_input({"commerce_action_proposal_id": str(failed_value.id)}),
        )
    with pytest.raises(PreEffectFailure, match="do not match"):
        await CommerceToolExecutor("commerce.inventory.adjust", authority, provider).execute(
            context, normalized
        )


@pytest.mark.asyncio
async def test_resource_resolution_is_tenant_scoped_and_input_is_reference_only() -> None:
    value = proposal()
    authority = FakeAuthority(value)
    normalized = normalize_commerce_input({"commerce_action_proposal_id": str(value.id)})
    context = cast(ExecutionContext, SimpleNamespace(tenant_id=value.tenant_id))
    tool = cast(ResolvedToolVersion, object())
    resolved = await CommerceResourceResolver(authority)(context, tool, normalized)
    assert resolved.resource_type == "commerce_action_proposal"
    assert resolved.resource_id == str(value.id)
    with pytest.raises(ResourceAccessDenied):
        other = cast(ExecutionContext, SimpleNamespace(tenant_id=uuid4()))
        await CommerceResourceResolver(authority)(other, tool, normalized)
    with pytest.raises(ValueError, match="only commerce_action_proposal_id"):
        normalize_commerce_input(
            {"commerce_action_proposal_id": str(value.id), "exact_quantity": 100}
        )


@pytest.mark.asyncio
async def test_provider_exceptions_are_pre_effect_and_bindings_are_connector_capable() -> None:
    value = proposal()

    class BrokenAuthority(FakeAuthority):
        async def prepare(
            self, tenant_id: UUID, proposal_id: UUID
        ) -> tuple[CommerceActionProposal, str]:
            raise RuntimeError("database unavailable")

    context = ToolExecutionContext(
        value.tenant_id, uuid4(), "op-exact", uuid4(), uuid4(), uuid4(), uuid4()
    )
    normalized = normalize_commerce_input({"commerce_action_proposal_id": str(value.id)})
    with pytest.raises(PreEffectFailure, match="before a known effect"):
        await CommerceToolExecutor(
            "commerce.inventory.adjust", BrokenAuthority(value), FakeCommerceProvider()
        ).execute(context, normalized)
    tools = {
        key: cast(
            ResolvedToolVersion,
            SimpleNamespace(definition_id=uuid4(), version_id=uuid4()),
        )
        for key in (
            "commerce.inventory.adjust",
            "commerce.refund.submit",
            "commerce.operation.status",
        )
    }
    bindings = commerce_tool_bindings(FakeAuthority(value), FakeCommerceProvider(), tools)
    assert len(bindings) == 3 and all(binding.credential_capable for binding in bindings)


@pytest.mark.asyncio
async def test_measurement_adapter_uses_application_boundary() -> None:
    calls: list[tuple[ExecutionContext, dict[str, Any]]] = []

    class Measurement:
        async def ingest_commerce_conversion(
            self, context: ExecutionContext, **values: Any
        ) -> None:
            calls.append((context, values))

    sink = MeasurementConversionSink(cast(Any, Measurement()))
    ctx = cast(ExecutionContext, SimpleNamespace(tenant_id=uuid4()))
    await sink.record_paid_order(
        ctx,
        commerce_order_id=uuid4(),
        external_order_id="order-1",
        attribution_code="cm_ref",
        amount=Decimal("49"),
        currency="USD",
        occurred_at=NOW,
    )
    assert calls[0][1]["public_code"] == "cm_ref"


@pytest.mark.asyncio
async def test_fake_provider_applies_known_mutations_and_reports_unknown_operations() -> None:
    provider = FakeCommerceProvider()
    provider.seed_store("store")
    provider.seed_store("store")
    adjusted = await provider.set_available_to(
        external_store_id="store",
        external_variant_id="fake-variant-1",
        quantity=12,
        idempotency_key="inventory-known",
    )
    refunded = await provider.submit_refund(
        external_store_id="store",
        external_order_id="fake-order-paid",
        amount=Decimal("49"),
        currency="USD",
        idempotency_key="refund-known",
    )
    missing = await provider.get_operation_status("store", "missing")
    assert adjusted.disposition is refunded.disposition is MutationDisposition.ACCEPTED
    assert provider.inventory["store"][0].available_quantity == 12
    assert provider.orders["store"][0].payment_state is PaymentState.REFUNDED
    assert missing.safe_failure_code == "OPERATION_NOT_FOUND"


def test_strict_output_contract_rejects_unknown_facts_and_accepts_exact_refund() -> None:
    observed_order = order()
    with pytest.raises(InvalidCommerceProposal, match="does not match"):
        validate_commerce_output(
            {},
            tenant_id=observed_order.tenant_id,
            product_id=uuid4(),
            connection_id=observed_order.connection_id,
            inventories=(),
            orders=(observed_order,),
        )
    unknown = {
        "summary": "Summary",
        "inventory_exceptions": [],
        "order_exceptions": [{"observation_id": str(uuid4()), "explanation": "Not grounded"}],
        "action_proposals": [],
        "limitations": ["Human review is required."],
    }
    with pytest.raises(InvalidCommerceProposal, match="unknown commerce observation"):
        validate_commerce_output(
            unknown,
            tenant_id=observed_order.tenant_id,
            product_id=uuid4(),
            connection_id=observed_order.connection_id,
            inventories=(),
            orders=(observed_order,),
        )
    exact = {
        "summary": "One exact refund proposal.",
        "inventory_exceptions": [],
        "order_exceptions": [
            {
                "observation_id": str(observed_order.id),
                "explanation": "Paid order reviewed for an exact refund.",
            }
        ],
        "action_proposals": [
            {
                "action_type": "REFUND",
                "external_order_id": observed_order.external_order_id,
                "exact_amount": "10.00",
                "currency": "USD",
                "reason": "Exact reviewed partial refund.",
            }
        ],
        "limitations": ["Human approval is required."],
    }
    accepted = validate_commerce_output(
        exact,
        tenant_id=observed_order.tenant_id,
        product_id=uuid4(),
        connection_id=observed_order.connection_id,
        inventories=(),
        orders=(observed_order,),
    )
    assert accepted[0].risk_level is RiskLevel.R6
    assert accepted[0].exact_amount == Decimal("10.00")


def test_commerce_agent_route_is_gpt_56_sol_and_no_shopify_connector_exists() -> None:
    route = initial_commerce_operations_route()
    assert route.model == "gpt-5.6-sol"
    assert route.profile_key == "commerce_operations"
    assert route.capabilities == frozenset({"text", "reasoning", "structured_output"})
    assert canonical_digest({"route": route.route_version}).startswith("sha256:")
