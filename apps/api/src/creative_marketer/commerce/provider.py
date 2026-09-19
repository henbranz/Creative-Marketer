from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from creative_marketer.agent_runtime.domain import canonical_digest

from .domain import FulfillmentState, PaymentState, canonical_money_text


@dataclass(frozen=True, slots=True)
class ProviderProduct:
    external_product_id: str
    title: str
    status: str
    variants: tuple[Mapping[str, object], ...]
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderInventory:
    external_product_id: str
    external_variant_id: str
    sku: str | None
    available_quantity: int | None
    location_id: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderOrder:
    external_order_id: str
    order_reference: str
    currency: str
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    shipping_total: Decimal
    total: Decimal
    payment_state: PaymentState
    fulfillment_state: FulfillmentState
    lines: tuple[Mapping[str, object], ...]
    created_at: datetime
    updated_at: datetime
    attribution_code: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderPage:
    items: tuple[object, ...]
    next_cursor: str | None


class CommerceReadProvider(Protocol):
    provider_key: str
    provider_version: str

    async def list_products(self, external_store_id: str, cursor: str | None) -> ProviderPage: ...
    async def collect_inventory(
        self, external_store_id: str, cursor: str | None
    ) -> ProviderPage: ...
    async def list_orders(self, external_store_id: str, cursor: str | None) -> ProviderPage: ...


class MutationDisposition(StrEnum):
    ACCEPTED = "ACCEPTED"
    FAILED = "FAILED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


@dataclass(frozen=True, slots=True)
class ProviderMutationResult:
    operation_id: str
    disposition: MutationDisposition
    safe_failure_code: str | None = None


class CommerceMutationProvider(Protocol):
    provider_key: str

    async def set_available_to(
        self,
        *,
        external_store_id: str,
        external_variant_id: str,
        quantity: int,
        idempotency_key: str,
    ) -> ProviderMutationResult: ...
    async def submit_refund(
        self,
        *,
        external_store_id: str,
        external_order_id: str,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
    ) -> ProviderMutationResult: ...
    async def get_operation_status(
        self, external_store_id: str, operation_id: str
    ) -> ProviderMutationResult: ...


@dataclass(slots=True)
class FakeCommerceProvider(CommerceReadProvider, CommerceMutationProvider):
    """Deterministic in-process fake. It performs no network I/O and stores no credentials."""

    provider_key: str = "fake"
    provider_version: str = "fake-commerce-v1"
    page_size: int = 2
    products: dict[str, list[ProviderProduct]] = field(default_factory=dict)
    inventory: dict[str, list[ProviderInventory]] = field(default_factory=dict)
    orders: dict[str, list[ProviderOrder]] = field(default_factory=dict)
    operations: dict[str, ProviderMutationResult] = field(default_factory=dict)
    idempotency: dict[str, str] = field(default_factory=dict)
    pending_effects: dict[str, Mapping[str, object]] = field(default_factory=dict)
    fail_next_read: bool = False
    next_mutation_disposition: MutationDisposition | None = None
    next_refund_disposition: MutationDisposition | None = None

    def seed_store(self, external_store_id: str, *, attribution_code: str | None = None) -> None:
        if external_store_id in self.products:
            return
        now = datetime(2026, 9, 19, 9, 0, tzinfo=UTC)
        self.products[external_store_id] = [
            ProviderProduct(
                "fake-product-1",
                "Demo Product",
                "ACTIVE",
                (
                    {
                        "external_variant_id": "fake-variant-1",
                        "sku": "DEMO-001",
                        "price": "49.00",
                        "currency": "USD",
                        "inventory_tracked": True,
                    },
                ),
                now,
            ),
            ProviderProduct(
                "fake-product-2",
                "Second Fake Product",
                "ACTIVE",
                (
                    {
                        "external_variant_id": "fake-variant-2",
                        "sku": "DEMO-002",
                        "price": "25.00",
                        "currency": "USD",
                        "inventory_tracked": True,
                    },
                ),
                now,
            ),
        ]
        self.inventory[external_store_id] = [
            ProviderInventory(
                "fake-product-1", "fake-variant-1", "DEMO-001", 2, "fake-location-1", now
            ),
            ProviderInventory(
                "fake-product-2", "fake-variant-2", "DEMO-002", 0, "fake-location-1", now
            ),
        ]
        line = {
            "external_line_id": "line-1",
            "external_product_id": "fake-product-1",
            "external_variant_id": "fake-variant-1",
            "sku": "DEMO-001",
            "quantity": 1,
            "unit_price": "49.00",
            "currency": "USD",
        }
        self.orders[external_store_id] = [
            ProviderOrder(
                "fake-order-paid",
                "FAKE-1001",
                "USD",
                Decimal("49"),
                Decimal("0"),
                Decimal("0"),
                Decimal("0"),
                Decimal("49"),
                PaymentState.PAID,
                FulfillmentState.UNFULFILLED,
                (line,),
                now,
                now,
                attribution_code,
            ),
            ProviderOrder(
                "fake-order-fulfilled",
                "FAKE-1002",
                "USD",
                Decimal("49"),
                Decimal("0"),
                Decimal("0"),
                Decimal("0"),
                Decimal("49"),
                PaymentState.PAID,
                FulfillmentState.FULFILLED,
                (line,),
                now,
                now,
            ),
            ProviderOrder(
                "fake-order-failed",
                "FAKE-1003",
                "USD",
                Decimal("49"),
                Decimal("0"),
                Decimal("0"),
                Decimal("0"),
                Decimal("49"),
                PaymentState.FAILED,
                FulfillmentState.UNFULFILLED,
                (line,),
                now,
                now,
            ),
        ]

    def _page(self, values: list[object], cursor: str | None) -> ProviderPage:
        if self.fail_next_read:
            self.fail_next_read = False
            raise RuntimeError("FAKE_COMMERCE_TEMPORARILY_UNAVAILABLE")
        start = int(cursor or "0")
        end = min(start + self.page_size, len(values))
        return ProviderPage(tuple(values[start:end]), str(end) if end < len(values) else None)

    async def list_products(self, external_store_id: str, cursor: str | None) -> ProviderPage:
        return self._page(list(self.products.get(external_store_id, ())), cursor)

    async def collect_inventory(self, external_store_id: str, cursor: str | None) -> ProviderPage:
        return self._page(list(self.inventory.get(external_store_id, ())), cursor)

    async def list_orders(self, external_store_id: str, cursor: str | None) -> ProviderPage:
        return self._page(list(self.orders.get(external_store_id, ())), cursor)

    def _result(
        self, idempotency_key: str, material: Mapping[str, object]
    ) -> ProviderMutationResult:
        existing = self.idempotency.get(idempotency_key)
        if existing is not None:
            return self.operations[existing]
        operation_id = canonical_digest(material)[7:31]
        disposition = self.next_mutation_disposition or MutationDisposition.ACCEPTED
        self.next_mutation_disposition = None
        result = ProviderMutationResult(
            operation_id,
            disposition,
            "FAKE_PROVIDER_FAILURE" if disposition is MutationDisposition.FAILED else None,
        )
        self.operations[operation_id] = result
        self.idempotency[idempotency_key] = operation_id
        return result

    async def set_available_to(
        self,
        *,
        external_store_id: str,
        external_variant_id: str,
        quantity: int,
        idempotency_key: str,
    ) -> ProviderMutationResult:
        result = self._result(
            idempotency_key,
            {
                "kind": "SET_AVAILABLE_TO",
                "store": external_store_id,
                "variant": external_variant_id,
                "quantity": quantity,
            },
        )
        effect = {
            "kind": "SET_AVAILABLE_TO",
            "store": external_store_id,
            "variant": external_variant_id,
            "quantity": quantity,
        }
        if result.disposition is MutationDisposition.ACCEPTED:
            self._apply_effect(effect)
        elif result.disposition is MutationDisposition.OUTCOME_UNKNOWN:
            self.pending_effects[result.operation_id] = effect
        return result

    async def submit_refund(
        self,
        *,
        external_store_id: str,
        external_order_id: str,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
    ) -> ProviderMutationResult:
        if self.next_refund_disposition is not None:
            self.next_mutation_disposition = self.next_refund_disposition
            self.next_refund_disposition = None
        result = self._result(
            idempotency_key,
            {
                "kind": "REFUND",
                "store": external_store_id,
                "order": external_order_id,
                "amount": canonical_money_text(amount),
                "currency": currency,
            },
        )
        effect = {
            "kind": "REFUND",
            "store": external_store_id,
            "order": external_order_id,
            "amount": amount,
        }
        if result.disposition is MutationDisposition.ACCEPTED:
            self._apply_effect(effect)
        elif result.disposition is MutationDisposition.OUTCOME_UNKNOWN:
            self.pending_effects[result.operation_id] = effect
        return result

    def _apply_effect(self, effect: Mapping[str, object]) -> None:
        if effect["kind"] == "SET_AVAILABLE_TO":
            quantity = effect["quantity"]
            if not isinstance(quantity, int) or isinstance(quantity, bool):
                raise ValueError("fake inventory effect quantity must be an integer")
            inventory_values = self.inventory.get(str(effect["store"]), [])
            for index, inventory_item in enumerate(inventory_values):
                if inventory_item.external_variant_id == str(effect["variant"]):
                    inventory_values[index] = replace(
                        inventory_item,
                        available_quantity=quantity,
                        updated_at=datetime.now(UTC),
                    )
                    return
        elif effect["kind"] == "REFUND":
            order_values = self.orders.get(str(effect["store"]), [])
            amount = Decimal(str(effect["amount"]))
            for index, order_item in enumerate(order_values):
                if order_item.external_order_id == str(effect["order"]):
                    state = (
                        PaymentState.REFUNDED
                        if amount == order_item.total
                        else PaymentState.PARTIALLY_REFUNDED
                    )
                    order_values[index] = replace(
                        order_item, payment_state=state, updated_at=datetime.now(UTC)
                    )
                    return

    async def get_operation_status(
        self, external_store_id: str, operation_id: str
    ) -> ProviderMutationResult:
        del external_store_id
        value = self.operations.get(operation_id)
        if value is None:
            return ProviderMutationResult(
                operation_id, MutationDisposition.FAILED, "OPERATION_NOT_FOUND"
            )
        if value.disposition is MutationDisposition.OUTCOME_UNKNOWN:
            effect = self.pending_effects.pop(operation_id, None)
            if effect is not None:
                self._apply_effect(effect)
            value = ProviderMutationResult(operation_id, MutationDisposition.ACCEPTED)
            self.operations[operation_id] = value
        return value

    def restore_unknown_operation(
        self,
        operation_id: str,
        effect: Mapping[str, object],
    ) -> None:
        """Reconstruct minimum fake state after a worker restart.

        The operation identifier is derived from immutable mutation material. A mismatch
        fails closed, so restart recovery cannot invent or redirect an effect.
        """
        material = (
            {
                "kind": "SET_AVAILABLE_TO",
                "store": effect["store"],
                "variant": effect["variant"],
                "quantity": effect["quantity"],
            }
            if effect["kind"] == "SET_AVAILABLE_TO"
            else {
                "kind": "REFUND",
                "store": effect["store"],
                "order": effect["order"],
                "amount": canonical_money_text(Decimal(str(effect["amount"]))),
                "currency": effect["currency"],
            }
        )
        expected = canonical_digest(material)[7:31]
        if expected != operation_id:
            raise ValueError("fake operation identity does not match immutable proposal")
        self.seed_store(str(effect["store"]))
        self.operations.setdefault(
            operation_id,
            ProviderMutationResult(operation_id, MutationDisposition.OUTCOME_UNKNOWN),
        )
        self.pending_effects.setdefault(operation_id, effect)
