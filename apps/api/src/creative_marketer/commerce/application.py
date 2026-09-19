from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, cast
from uuid import UUID

from jsonschema import Draft202012Validator

from creative_marketer.agent_runtime.domain import canonical_digest
from creative_marketer.audit.application import AuditWriter
from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditOutcome
from creative_marketer.audit.safety import safe_metadata
from creative_marketer.events.application import OutboxWriter
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import tenant_event
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.identity.domain import MembershipRole, MembershipStatus

from .domain import (
    ActionType,
    CommerceActionProposal,
    CommerceAgentResult,
    CommerceConflict,
    CommerceConnection,
    CommerceNotFound,
    CommerceOperationsContextManifest,
    CommerceOperationsReport,
    CommercePermissionDenied,
    CommerceProductObservation,
    CommerceSyncRun,
    CommerceVariantObservation,
    FulfillmentObservation,
    InvalidCommerceProposal,
    InventoryObservation,
    OrderLineObservation,
    OrderObservation,
    PaymentObservation,
    PaymentState,
    ProductCommerceMapping,
    SyncStatus,
    SyncType,
    canonical_money_text,
    inventory_exceptions,
    order_exceptions,
    proposal_digest,
)
from .provider import (
    CommerceReadProvider,
    ProviderInventory,
    ProviderOrder,
    ProviderProduct,
)

COMMERCE_CONTRACT_KEY = "commerce.operations_report"
COMMERCE_CONTRACT_VERSION = 1


def load_commerce_output_schema() -> Mapping[str, object]:
    path = Path(__file__).with_name("schemas") / "commerce.operations_report.v1.json"
    return cast(Mapping[str, object], json.loads(path.read_text()))


def validate_commerce_output(
    output: Mapping[str, object],
    *,
    tenant_id: UUID,
    product_id: UUID,
    connection_id: UUID,
    inventories: tuple[InventoryObservation, ...],
    orders: tuple[OrderObservation, ...],
) -> tuple[CommerceActionProposal, ...]:
    """Reject every model-authored target/value that is not grounded in canonical facts."""
    errors = sorted(
        Draft202012Validator(
            load_commerce_output_schema(),
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        ).iter_errors(dict(output)),
        key=lambda item: list(item.path),
    )
    if errors:
        raise InvalidCommerceProposal("provider output does not match commerce contract")
    inventory_ids = {str(item.id) for item in inventories}
    order_ids = {str(item.id) for item in orders}
    for group, allowed in (
        (output["inventory_exceptions"], inventory_ids),
        (output["order_exceptions"], order_ids),
    ):
        assert isinstance(group, list)
        if any(str(item["observation_id"]) not in allowed for item in group):
            raise InvalidCommerceProposal("model referenced an unknown commerce observation")
    raw_proposals = output["action_proposals"]
    assert isinstance(raw_proposals, list)
    result = []
    for raw in raw_proposals:
        assert isinstance(raw, Mapping)
        if raw["action_type"] == ActionType.INVENTORY_ADJUSTMENT.value:
            variant_id = str(raw["external_variant_id"])
            observed = next(
                (item for item in inventories if item.external_variant_id == variant_id), None
            )
            refs = (
                (
                    {
                        "kind": "inventory_observation",
                        "id": str(observed.id),
                        "digest": observed.source_digest,
                    },
                )
                if observed
                else ()
            )
        else:
            order_id = str(raw["external_order_id"])
            observed_order = next(
                (item for item in orders if item.external_order_id == order_id), None
            )
            refs = (
                (
                    {
                        "kind": "order_observation",
                        "id": str(observed_order.id),
                        "digest": observed_order.source_digest,
                    },
                )
                if observed_order
                else ()
            )
        result.append(
            validate_action_proposal(
                tenant_id=tenant_id,
                product_id=product_id,
                connection_id=connection_id,
                candidate=raw,
                inventories=inventories,
                orders=orders,
                evidence_refs=refs,
            )
        )
    return tuple(result)


def build_commerce_result(
    output: Mapping[str, object],
    *,
    tenant_id: UUID,
    product_id: UUID,
    connection_id: UUID,
    agent_run_id: UUID,
    agent_version_id: UUID,
    manifest: CommerceOperationsContextManifest,
    inventories: tuple[InventoryObservation, ...],
    orders: tuple[OrderObservation, ...],
) -> CommerceAgentResult:
    proposals = validate_commerce_output(
        output,
        tenant_id=tenant_id,
        product_id=product_id,
        connection_id=connection_id,
        inventories=inventories,
        orders=orders,
    )
    raw_inventory = cast(list[Mapping[str, object]], output["inventory_exceptions"])
    raw_orders = cast(list[Mapping[str, object]], output["order_exceptions"])
    raw_limitations = cast(list[str], output["limitations"])
    content = {
        "context_manifest_id": str(manifest.id),
        "context_manifest_digest": manifest.semantic_digest,
        "summary": str(output["summary"]),
        "inventory_exceptions": [dict(item) for item in raw_inventory],
        "order_exceptions": [dict(item) for item in raw_orders],
        "limitations": list(raw_limitations),
        "schema_version": 1,
    }
    report = CommerceOperationsReport(
        tenant_id,
        product_id,
        agent_run_id,
        agent_version_id,
        manifest.id,
        manifest.semantic_digest,
        str(output["summary"]),
        tuple(dict(item) for item in raw_inventory),
        tuple(dict(item) for item in raw_orders),
        tuple(raw_limitations),
        canonical_digest(content),
    )
    return CommerceAgentResult(report, proposals)


class CommerceRepository(Protocol):
    async def connection(self, connection_id: UUID) -> CommerceConnection | None: ...
    async def connections(self) -> tuple[CommerceConnection, ...]: ...
    async def add_connection(self, value: CommerceConnection) -> None: ...
    async def mapping(self, product_id: UUID) -> ProductCommerceMapping | None: ...
    async def add_mapping(self, value: ProductCommerceMapping) -> None: ...
    async def products(self, connection_id: UUID) -> tuple[CommerceProductObservation, ...]: ...
    async def inventories(
        self, connection_id: UUID, product_id: UUID | None = None
    ) -> tuple[InventoryObservation, ...]: ...
    async def orders(
        self, connection_id: UUID, product_id: UUID | None = None
    ) -> tuple[OrderObservation, ...]: ...
    async def latest_order(
        self, connection_id: UUID, external_order_id: str
    ) -> OrderObservation | None: ...
    async def latest_inventory(
        self, connection_id: UUID, external_variant_id: str
    ) -> InventoryObservation | None: ...
    async def add_product(self, value: CommerceProductObservation) -> bool: ...
    async def add_variant(self, value: CommerceVariantObservation) -> bool: ...
    async def add_inventory(self, value: InventoryObservation) -> bool: ...
    async def add_order(self, value: OrderObservation) -> bool: ...
    async def add_payment(self, value: PaymentObservation) -> bool: ...
    async def add_fulfillment(self, value: FulfillmentObservation) -> bool: ...
    async def add_sync_run(self, value: CommerceSyncRun) -> None: ...
    async def add_proposal(self, value: CommerceActionProposal) -> bool: ...
    async def proposals(self, product_id: UUID) -> tuple[CommerceActionProposal, ...]: ...


class CommerceUnitOfWork(Protocol):
    commerce: CommerceRepository
    audit: AuditWriter
    outbox: OutboxWriter

    async def __aenter__(self) -> CommerceUnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class CommerceUnitOfWorkFactory(Protocol):
    def __call__(self, tenant_id: UUID) -> CommerceUnitOfWork: ...


class CommerceConversionSink(Protocol):
    async def record_paid_order(
        self,
        context: ExecutionContext,
        *,
        commerce_order_id: UUID,
        external_order_id: str,
        attribution_code: str,
        amount: Decimal,
        currency: str,
        occurred_at: datetime,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SyncSummary:
    run: CommerceSyncRun
    added: int
    unchanged: int
    next_cursor: str | None


def _product_digest(value: ProviderProduct) -> str:
    return canonical_digest(
        {
            "external_product_id": value.external_product_id,
            "title": value.title,
            "status": value.status,
            "variants": [dict(v) for v in value.variants],
            "updated_at": value.updated_at.isoformat(),
        }
    )


def _inventory_digest(value: ProviderInventory) -> str:
    return canonical_digest(
        {
            "external_product_id": value.external_product_id,
            "external_variant_id": value.external_variant_id,
            "sku": value.sku,
            "available_quantity": value.available_quantity,
            "location_id": value.location_id,
            "updated_at": value.updated_at.isoformat(),
        }
    )


def _order_digest(value: ProviderOrder) -> str:
    return canonical_digest(
        {
            "external_order_id": value.external_order_id,
            "currency": value.currency,
            "subtotal": str(value.subtotal),
            "discount_total": str(value.discount_total),
            "tax_total": str(value.tax_total),
            "shipping_total": str(value.shipping_total),
            "total": str(value.total),
            "payment_state": value.payment_state.value,
            "fulfillment_state": value.fulfillment_state.value,
            "lines": [dict(v) for v in value.lines],
            "updated_at": value.updated_at.isoformat(),
            "attribution_code": value.attribution_code,
        }
    )


@dataclass(slots=True)
class CommerceService:
    uow_factory: CommerceUnitOfWorkFactory
    reader: CommerceReadProvider
    conversion_sink: CommerceConversionSink | None = None
    contracts: EventContractRegistry = field(default_factory=EventContractRegistry)

    @staticmethod
    def _write(context: ExecutionContext) -> UUID:
        if (
            context.membership_status is not MembershipStatus.ACTIVE
            or context.membership_role not in {MembershipRole.OWNER, MembershipRole.ADMIN}
            or context.user_id is None
        ):
            raise CommercePermissionDenied("OWNER or ADMIN membership required")
        return context.user_id

    async def list_connections(self, context: ExecutionContext) -> tuple[CommerceConnection, ...]:
        async with self.uow_factory(context.tenant_id) as uow:
            return await uow.commerce.connections()

    async def create_fake_connection(
        self, context: ExecutionContext, display_name: str = "Fake Store"
    ) -> CommerceConnection:
        self._write(context)
        connection = CommerceConnection(
            context.tenant_id,
            "fake",
            display_name,
            f"fake-store-{context.tenant_id}",
            "fake.local",
            ("catalog.read", "inventory.read", "orders.read", "inventory.write", "refund.write"),
        )
        async with self.uow_factory(context.tenant_id) as uow:
            await uow.commerce.add_connection(connection)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="commerce.connection.created",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="commerce_connection",
                    resource_id=str(connection.id),
                    metadata=safe_metadata({"provider": "fake"}),
                )
            )
            await uow.commit()
        return connection

    async def map_product(
        self,
        context: ExecutionContext,
        *,
        product_id: UUID,
        connection_id: UUID,
        external_product_id: str,
        external_variant_id: str | None = None,
    ) -> ProductCommerceMapping:
        actor = self._write(context)
        async with self.uow_factory(context.tenant_id) as uow:
            connection = await uow.commerce.connection(connection_id)
            if connection is None:
                raise CommerceNotFound("connection not found")
            if not any(
                v.external_product_id == external_product_id
                for v in await uow.commerce.products(connection_id)
            ):
                raise CommerceNotFound("observed commerce product not found")
            if await uow.commerce.mapping(product_id) is not None:
                raise CommerceConflict("product already has an active commerce mapping")
            value = ProductCommerceMapping(
                context.tenant_id,
                product_id,
                connection_id,
                external_product_id,
                actor,
                external_variant_id,
            )
            await uow.commerce.add_mapping(value)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="commerce.mapping.created",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="product_commerce_mapping",
                    resource_id=str(value.id),
                    metadata=safe_metadata(
                        {"product_id": str(product_id), "connection_id": str(connection_id)}
                    ),
                )
            )
            await uow.commit()
            return value

    async def sync(
        self,
        context: ExecutionContext,
        connection_id: UUID,
        sync_type: SyncType,
        *,
        cursor: str | None = None,
    ) -> SyncSummary:
        self._write(context)
        now = datetime.now(UTC)
        run = CommerceSyncRun(
            context.tenant_id,
            connection_id,
            sync_type,
            SyncStatus.RUNNING,
            cursor=cursor,
            started_at=now,
        )
        async with self.uow_factory(context.tenant_id) as uow:
            connection = await uow.commerce.connection(connection_id)
            if connection is None:
                raise CommerceNotFound("connection not found")
            await uow.commerce.add_sync_run(run)
            await uow.commit()
        added = unchanged = 0
        next_cursor: str | None = None
        try:
            if sync_type is SyncType.CATALOG:
                page = await self.reader.list_products(connection.external_store_id, cursor)
                next_cursor = page.next_cursor
                async with self.uow_factory(context.tenant_id) as uow:
                    for raw in page.items:
                        product_value = cast(ProviderProduct, raw)
                        digest = _product_digest(product_value)
                        observed = CommerceProductObservation(
                            context.tenant_id,
                            connection_id,
                            product_value.external_product_id,
                            product_value.title,
                            product_value.status,
                            digest,
                            captured_at=product_value.updated_at,
                        )
                        if await uow.commerce.add_product(observed):
                            added += 1
                        else:
                            unchanged += 1
                        for variant in product_value.variants:
                            variant_digest = canonical_digest(
                                {
                                    "external_product_id": product_value.external_product_id,
                                    **dict(variant),
                                    "updated_at": product_value.updated_at.isoformat(),
                                }
                            )
                            variant_observation = CommerceVariantObservation(
                                context.tenant_id,
                                connection_id,
                                product_value.external_product_id,
                                str(variant["external_variant_id"]),
                                str(variant["sku"]) if variant.get("sku") else None,
                                Decimal(str(variant["price"])),
                                str(variant["currency"]),
                                bool(variant["inventory_tracked"])
                                if variant.get("inventory_tracked") is not None
                                else None,
                                variant_digest,
                                captured_at=product_value.updated_at,
                            )
                            await uow.commerce.add_variant(variant_observation)
                    await uow.commit()
            elif sync_type is SyncType.INVENTORY:
                page = await self.reader.collect_inventory(connection.external_store_id, cursor)
                next_cursor = page.next_cursor
                async with self.uow_factory(context.tenant_id) as uow:
                    for raw in page.items:
                        inventory_value = cast(ProviderInventory, raw)
                        inventory_observation = InventoryObservation(
                            context.tenant_id,
                            connection_id,
                            inventory_value.external_product_id,
                            inventory_value.external_variant_id,
                            inventory_value.available_quantity,
                            inventory_value.updated_at,
                            _inventory_digest(inventory_value),
                            inventory_value.sku,
                            inventory_value.location_id,
                        )
                        if await uow.commerce.add_inventory(inventory_observation):
                            added += 1
                        else:
                            unchanged += 1
                    await uow.commit()
            elif sync_type is SyncType.ORDERS:
                page = await self.reader.list_orders(connection.external_store_id, cursor)
                next_cursor = page.next_cursor
                conversions: list[OrderObservation] = []
                async with self.uow_factory(context.tenant_id) as uow:
                    for raw in page.items:
                        order_value = cast(ProviderOrder, raw)
                        lines = tuple(
                            OrderLineObservation(
                                str(v["external_line_id"]),
                                str(v["external_product_id"]),
                                str(v["external_variant_id"])
                                if v.get("external_variant_id")
                                else None,
                                str(v["sku"]) if v.get("sku") else None,
                                int(str(v["quantity"])),
                                Decimal(str(v["unit_price"])),
                                str(v["currency"]),
                            )
                            for v in order_value.lines
                        )
                        digest = _order_digest(order_value)
                        order_observation = OrderObservation(
                            context.tenant_id,
                            connection_id,
                            order_value.external_order_id,
                            order_value.order_reference,
                            order_value.currency,
                            order_value.subtotal,
                            order_value.discount_total,
                            order_value.tax_total,
                            order_value.shipping_total,
                            order_value.total,
                            order_value.payment_state,
                            order_value.fulfillment_state,
                            order_value.created_at,
                            order_value.updated_at,
                            datetime.now(UTC),
                            lines,
                            digest,
                            order_value.attribution_code,
                        )
                        if await uow.commerce.add_order(order_observation):
                            added += 1
                            await uow.commerce.add_payment(
                                PaymentObservation(
                                    context.tenant_id,
                                    connection_id,
                                    order_value.external_order_id,
                                    order_value.payment_state,
                                    order_value.total,
                                    order_value.currency,
                                    order_value.updated_at,
                                    canonical_digest(
                                        {
                                            "order": order_value.external_order_id,
                                            "state": order_value.payment_state.value,
                                            "amount": str(order_value.total),
                                            "currency": order_value.currency,
                                            "updated_at": order_value.updated_at.isoformat(),
                                        }
                                    ),
                                )
                            )
                            await uow.commerce.add_fulfillment(
                                FulfillmentObservation(
                                    context.tenant_id,
                                    connection_id,
                                    order_value.external_order_id,
                                    order_value.fulfillment_state,
                                    order_value.updated_at,
                                    canonical_digest(
                                        {
                                            "order": order_value.external_order_id,
                                            "state": order_value.fulfillment_state.value,
                                            "updated_at": order_value.updated_at.isoformat(),
                                        }
                                    ),
                                )
                            )
                            if (
                                order_observation.financial_status is PaymentState.PAID
                                and order_observation.attribution_code
                            ):
                                conversions.append(order_observation)
                        else:
                            unchanged += 1
                    await uow.commit()
                if self.conversion_sink:
                    for conversion in conversions:
                        await self.conversion_sink.record_paid_order(
                            context,
                            commerce_order_id=conversion.id,
                            external_order_id=conversion.external_order_id,
                            attribution_code=cast(str, conversion.attribution_code),
                            amount=conversion.total,
                            currency=conversion.currency,
                            occurred_at=conversion.updated_at_external,
                        )
            else:
                raise ValueError("ALL sync is orchestrated as finite individual syncs")
        except Exception:
            async with self.uow_factory(context.tenant_id) as uow:
                await uow.commerce.add_sync_run(
                    CommerceSyncRun(
                        context.tenant_id,
                        connection_id,
                        sync_type,
                        SyncStatus.TEMPORARILY_UNAVAILABLE,
                        cursor=cursor,
                        safe_failure_code="COMMERCE_PROVIDER_UNAVAILABLE",
                        id=run.id,
                        started_at=run.started_at,
                        completed_at=datetime.now(UTC),
                    )
                )
                await uow.commit()
            raise
        complete = CommerceSyncRun(
            context.tenant_id,
            connection_id,
            sync_type,
            SyncStatus.SUCCEEDED,
            cursor=next_cursor,
            id=run.id,
            started_at=run.started_at,
            completed_at=datetime.now(UTC),
        )
        async with self.uow_factory(context.tenant_id) as uow:
            await uow.commerce.add_sync_run(complete)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="commerce.sync.completed",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="commerce_sync_run",
                    resource_id=str(run.id),
                    metadata=safe_metadata(
                        {"sync_type": sync_type.value, "added": added, "unchanged": unchanged}
                    ),
                )
            )
            await uow.outbox.append(
                tenant_event(
                    context,
                    event_type="commerce.sync.completed.v1",
                    schema_version=1,
                    aggregate_type="commerce_sync_run",
                    aggregate_id=run.id,
                    payload={
                        "commerce_sync_run_id": str(run.id),
                        "connection_id": str(connection_id),
                        "sync_type": sync_type.value,
                        "added": added,
                    },
                    payload_schema_digest=self.contracts.schema_digest(
                        "commerce.sync.completed.v1"
                    ),
                    occurred_at=complete.completed_at or now,
                )
            )
            await uow.commit()
        return SyncSummary(complete, added, unchanged, next_cursor)

    async def workspace(self, context: ExecutionContext, product_id: UUID) -> dict[str, Any]:
        async with self.uow_factory(context.tenant_id) as uow:
            mapping = await uow.commerce.mapping(product_id)
            if mapping is None:
                return {
                    "mapping": None,
                    "inventory": (),
                    "orders": (),
                    "proposals": (),
                    "inventory_exceptions": (),
                    "order_exceptions": (),
                }
            inventories = await uow.commerce.inventories(mapping.connection_id, product_id)
            orders = await uow.commerce.orders(mapping.connection_id, product_id)
            proposals = await uow.commerce.proposals(product_id)
            return {
                "mapping": mapping,
                "inventory": inventories,
                "orders": orders,
                "proposals": proposals,
                "inventory_exceptions": inventory_exceptions(inventories),
                "order_exceptions": order_exceptions(orders),
            }


def validate_action_proposal(
    *,
    tenant_id: UUID,
    product_id: UUID,
    connection_id: UUID,
    candidate: Mapping[str, object],
    inventories: tuple[InventoryObservation, ...],
    orders: tuple[OrderObservation, ...],
    evidence_refs: tuple[Mapping[str, str], ...],
) -> CommerceActionProposal:
    try:
        action = ActionType(str(candidate["action_type"]))
        reason = str(candidate["reason"])
    except (KeyError, ValueError) as error:
        raise InvalidCommerceProposal("unsupported action proposal") from error
    if action is ActionType.INVENTORY_ADJUSTMENT:
        variant = str(candidate.get("external_variant_id", ""))
        quantity = candidate.get("exact_quantity")
        matching = [v for v in inventories if v.external_variant_id == variant]
        if (
            not matching
            or not isinstance(quantity, int)
            or isinstance(quantity, bool)
            or quantity < 0
        ):
            raise InvalidCommerceProposal(
                "inventory proposal is not grounded in an exact observed variant and quantity"
            )
        material: dict[str, object] = {
            "connection_id": str(connection_id),
            "product_id": str(product_id),
            "action_type": action.value,
            "external_product_id": matching[0].external_product_id,
            "external_variant_id": variant,
            "exact_quantity": quantity,
            "reason": reason,
            "evidence_refs": [dict(v) for v in evidence_refs],
            "strategy": "SET_AVAILABLE_TO",
            "schema_version": 1,
        }
        return CommerceActionProposal(
            tenant_id,
            connection_id,
            product_id,
            action,
            matching[0].external_product_id,
            variant,
            None,
            quantity,
            None,
            None,
            reason,
            evidence_refs,
            proposal_digest(material),
        )
    order_id = str(candidate.get("external_order_id", ""))
    amount = Decimal(str(candidate.get("exact_amount", "-1")))
    currency = str(candidate.get("currency", ""))
    matching_orders = [v for v in orders if v.external_order_id == order_id]
    if not matching_orders:
        raise InvalidCommerceProposal("refund target is not an observed order")
    order = matching_orders[0]
    if (
        order.financial_status is not PaymentState.PAID
        or currency != order.currency
        or amount <= 0
        or amount > order.refundable_amount
    ):
        raise InvalidCommerceProposal("refund exceeds deterministic current authority")
    material = {
        "connection_id": str(connection_id),
        "product_id": str(product_id),
        "action_type": action.value,
        "external_order_id": order_id,
        "exact_amount": canonical_money_text(amount),
        "currency": currency,
        "reason": reason,
        "evidence_refs": [dict(v) for v in evidence_refs],
        "schema_version": 1,
    }
    return CommerceActionProposal(
        tenant_id,
        connection_id,
        product_id,
        action,
        None,
        None,
        order_id,
        None,
        amount,
        currency,
        reason,
        evidence_refs,
        proposal_digest(material),
    )
