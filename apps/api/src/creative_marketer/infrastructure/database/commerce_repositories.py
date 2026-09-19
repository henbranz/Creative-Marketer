from collections.abc import Mapping
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.commerce.domain import (
    ActionType,
    CommerceActionProposal,
    CommerceConnection,
    CommerceProductObservation,
    CommerceSyncRun,
    CommerceVariantObservation,
    ConnectionStatus,
    FulfillmentObservation,
    FulfillmentState,
    InventoryObservation,
    MappingStatus,
    OrderLineObservation,
    OrderObservation,
    PaymentObservation,
    PaymentState,
    ProductCommerceMapping,
)

from .commerce_schema import (
    action_proposals,
    connections,
    fulfillment_observations,
    inventory_observations,
    order_observations,
    payment_observations,
    product_mappings,
    product_observations,
    sync_runs,
    variant_observations,
)


class SqlAlchemyCommerceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def connection(self, connection_id: UUID) -> CommerceConnection | None:
        row = (
            (
                await self.session.execute(
                    select(connections).where(connections.c.id == connection_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        return (
            CommerceConnection(
                row["tenant_id"],
                row["provider"],
                row["display_name"],
                row["external_store_id"],
                row["safe_store_identifier"],
                tuple(row["capabilities"]),
                row["id"],
                ConnectionStatus(row["status"]),
                row["created_at"],
                row["updated_at"],
            )
            if row
            else None
        )

    async def connections(self) -> tuple[CommerceConnection, ...]:
        rows = (
            await self.session.execute(select(connections).order_by(connections.c.created_at))
        ).mappings()
        return tuple(
            CommerceConnection(
                r["tenant_id"],
                r["provider"],
                r["display_name"],
                r["external_store_id"],
                r["safe_store_identifier"],
                tuple(r["capabilities"]),
                r["id"],
                ConnectionStatus(r["status"]),
                r["created_at"],
                r["updated_at"],
            )
            for r in rows
        )

    async def add_connection(self, v: CommerceConnection) -> None:
        await self.session.execute(
            insert(connections).values(
                id=v.id,
                tenant_id=v.tenant_id,
                provider=v.provider,
                display_name=v.display_name,
                external_store_id=v.external_store_id,
                safe_store_identifier=v.safe_store_identifier,
                status=v.status.value,
                capabilities=list(v.capabilities),
                created_at=v.created_at,
                updated_at=v.updated_at,
            )
        )

    async def mapping(self, product_id: UUID) -> ProductCommerceMapping | None:
        row = (
            (
                await self.session.execute(
                    select(product_mappings)
                    .where(
                        product_mappings.c.product_id == product_id,
                        product_mappings.c.status == "ACTIVE",
                    )
                    .order_by(product_mappings.c.created_at.desc())
                )
            )
            .mappings()
            .first()
        )
        return (
            ProductCommerceMapping(
                row["tenant_id"],
                row["product_id"],
                row["connection_id"],
                row["external_product_id"],
                row["created_by"],
                row["external_variant_id"],
                row["id"],
                MappingStatus(row["status"]),
                row["created_at"],
            )
            if row
            else None
        )

    async def add_mapping(self, v: ProductCommerceMapping) -> None:
        await self.session.execute(
            insert(product_mappings).values(
                id=v.id,
                tenant_id=v.tenant_id,
                product_id=v.product_id,
                connection_id=v.connection_id,
                external_product_id=v.external_product_id,
                external_variant_id=v.external_variant_id,
                status=v.status.value,
                created_by=v.created_by,
                created_at=v.created_at,
            )
        )

    async def products(self, connection_id: UUID) -> tuple[CommerceProductObservation, ...]:
        rows = (
            await self.session.execute(
                select(product_observations)
                .where(product_observations.c.connection_id == connection_id)
                .order_by(product_observations.c.captured_at.desc())
            )
        ).mappings()
        return tuple(
            CommerceProductObservation(
                r["tenant_id"],
                r["connection_id"],
                r["external_product_id"],
                r["title"],
                r["status"],
                r["source_digest"],
                r["id"],
                r["provider"],
                r["provider_version"],
                r["schema_version"],
                r["captured_at"],
            )
            for r in rows
        )

    async def inventories(
        self, connection_id: UUID, product_id: UUID | None = None
    ) -> tuple[InventoryObservation, ...]:
        query = (
            select(inventory_observations)
            .where(inventory_observations.c.connection_id == connection_id)
            .order_by(inventory_observations.c.captured_at.desc())
        )
        if product_id is not None:
            mapping = await self.mapping(product_id)
            if mapping:
                query = query.where(
                    inventory_observations.c.external_product_id == mapping.external_product_id
                )
        rows = (await self.session.execute(query)).mappings()
        return tuple(
            InventoryObservation(
                r["tenant_id"],
                r["connection_id"],
                r["external_product_id"],
                r["external_variant_id"],
                r["available_quantity"],
                r["captured_at"],
                r["source_digest"],
                r["sku"],
                r["location_id"],
                r["committed_quantity"],
                r["on_hand_quantity"],
                r["id"],
                r["provider"],
                r["provider_version"],
                r["schema_version"],
            )
            for r in rows
        )

    @staticmethod
    def _order(r: Mapping[str, Any]) -> OrderObservation:
        lines = tuple(
            OrderLineObservation(
                str(v["external_line_id"]),
                str(v["external_product_id"]),
                v.get("external_variant_id"),
                v.get("sku"),
                int(v["quantity"]),
                Decimal(str(v["unit_price"])),
                str(v["currency"]),
                UUID(v["mapped_product_id"]) if v.get("mapped_product_id") else None,
            )
            for v in r["lines"]
        )
        return OrderObservation(
            r["tenant_id"],
            r["connection_id"],
            r["external_order_id"],
            r["order_reference"],
            r["currency"],
            Decimal(r["subtotal"]),
            Decimal(r["discount_total"]),
            Decimal(r["tax_total"]),
            Decimal(r["shipping_total"]),
            Decimal(r["total"]),
            PaymentState(r["financial_status"]),
            FulfillmentState(r["fulfillment_status"]),
            r["created_at_external"],
            r["updated_at_external"],
            r["captured_at"],
            lines,
            r["source_digest"],
            r["attribution_code"],
            r["id"],
            r["schema_version"],
        )

    async def orders(
        self, connection_id: UUID, product_id: UUID | None = None
    ) -> tuple[OrderObservation, ...]:
        query = (
            select(order_observations)
            .where(order_observations.c.connection_id == connection_id)
            .order_by(order_observations.c.captured_at.desc())
        )
        rows = (await self.session.execute(query)).mappings()
        values = tuple(self._order(cast(Mapping[str, Any], r)) for r in rows)
        if product_id is None:
            return values
        mapping = await self.mapping(product_id)
        return tuple(
            v
            for v in values
            if mapping
            and any(line.external_product_id == mapping.external_product_id for line in v.lines)
        )

    async def latest_order(
        self, connection_id: UUID, external_order_id: str
    ) -> OrderObservation | None:
        row = (
            (
                await self.session.execute(
                    select(order_observations)
                    .where(
                        order_observations.c.connection_id == connection_id,
                        order_observations.c.external_order_id == external_order_id,
                    )
                    .order_by(order_observations.c.captured_at.desc())
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        return self._order(cast(Mapping[str, Any], row)) if row else None

    async def latest_inventory(
        self, connection_id: UUID, external_variant_id: str
    ) -> InventoryObservation | None:
        values = await self.inventories(connection_id)
        return next((v for v in values if v.external_variant_id == external_variant_id), None)

    async def _dedup_insert(self, table: Any, values: dict[str, object], digest: str) -> bool:
        result = await self.session.execute(
            pg_insert(table)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["tenant_id", "source_digest"])
            .returning(table.c.id)
        )
        return result.scalar_one_or_none() is not None

    async def add_product(self, v: CommerceProductObservation) -> bool:
        return await self._dedup_insert(
            product_observations,
            {
                "id": v.id,
                "tenant_id": v.tenant_id,
                "connection_id": v.connection_id,
                "external_product_id": v.external_product_id,
                "title": v.title,
                "status": v.status,
                "provider": v.provider,
                "provider_version": v.provider_version,
                "source_digest": v.source_digest,
                "schema_version": v.schema_version,
                "captured_at": v.captured_at,
            },
            v.source_digest,
        )

    async def add_variant(self, v: CommerceVariantObservation) -> bool:
        return await self._dedup_insert(
            variant_observations,
            {
                "id": v.id,
                "tenant_id": v.tenant_id,
                "connection_id": v.connection_id,
                "external_product_id": v.external_product_id,
                "external_variant_id": v.external_variant_id,
                "sku": v.sku,
                "price": v.price,
                "currency": v.currency,
                "inventory_tracked": None
                if v.inventory_tracked is None
                else str(v.inventory_tracked).lower(),
                "source_digest": v.source_digest,
                "schema_version": v.schema_version,
                "captured_at": v.captured_at,
            },
            v.source_digest,
        )

    async def add_inventory(self, v: InventoryObservation) -> bool:
        return await self._dedup_insert(
            inventory_observations,
            {
                "id": v.id,
                "tenant_id": v.tenant_id,
                "connection_id": v.connection_id,
                "external_product_id": v.external_product_id,
                "external_variant_id": v.external_variant_id,
                "sku": v.sku,
                "location_id": v.location_id,
                "available_quantity": v.available_quantity,
                "committed_quantity": v.committed_quantity,
                "on_hand_quantity": v.on_hand_quantity,
                "provider": v.provider,
                "provider_version": v.provider_version,
                "source_digest": v.source_digest,
                "schema_version": v.schema_version,
                "captured_at": v.captured_at,
            },
            v.source_digest,
        )

    async def add_order(self, v: OrderObservation) -> bool:
        lines = [
            {
                "external_line_id": x.external_line_id,
                "external_product_id": x.external_product_id,
                "external_variant_id": x.external_variant_id,
                "sku": x.sku,
                "quantity": x.quantity,
                "unit_price": str(x.unit_price),
                "currency": x.currency,
                "mapped_product_id": str(x.mapped_product_id) if x.mapped_product_id else None,
            }
            for x in v.lines
        ]
        return await self._dedup_insert(
            order_observations,
            {
                "id": v.id,
                "tenant_id": v.tenant_id,
                "connection_id": v.connection_id,
                "external_order_id": v.external_order_id,
                "order_reference": v.order_reference,
                "currency": v.currency,
                "subtotal": v.subtotal,
                "discount_total": v.discount_total,
                "tax_total": v.tax_total,
                "shipping_total": v.shipping_total,
                "total": v.total,
                "financial_status": v.financial_status.value,
                "fulfillment_status": v.fulfillment_status.value,
                "created_at_external": v.created_at_external,
                "updated_at_external": v.updated_at_external,
                "captured_at": v.captured_at,
                "lines": lines,
                "attribution_code": v.attribution_code,
                "source_digest": v.source_digest,
                "schema_version": v.schema_version,
            },
            v.source_digest,
        )

    async def add_payment(self, v: PaymentObservation) -> bool:
        return await self._dedup_insert(
            payment_observations,
            {
                "id": v.id,
                "tenant_id": v.tenant_id,
                "connection_id": v.connection_id,
                "external_order_id": v.external_order_id,
                "state": v.state.value,
                "amount": v.amount,
                "currency": v.currency,
                "provider_payment_reference": v.provider_payment_reference,
                "captured_at": v.captured_at,
                "source_digest": v.source_digest,
                "schema_version": v.schema_version,
            },
            v.source_digest,
        )

    async def add_fulfillment(self, v: FulfillmentObservation) -> bool:
        return await self._dedup_insert(
            fulfillment_observations,
            {
                "id": v.id,
                "tenant_id": v.tenant_id,
                "connection_id": v.connection_id,
                "external_order_id": v.external_order_id,
                "state": v.state.value,
                "captured_at": v.captured_at,
                "source_digest": v.source_digest,
                "schema_version": v.schema_version,
            },
            v.source_digest,
        )

    async def add_sync_run(self, v: CommerceSyncRun) -> None:
        await self.session.execute(
            pg_insert(sync_runs)
            .values(
                id=v.id,
                tenant_id=v.tenant_id,
                connection_id=v.connection_id,
                sync_type=v.sync_type.value,
                status=v.status.value,
                cursor=v.cursor,
                safe_failure_code=v.safe_failure_code,
                started_at=v.started_at,
                completed_at=v.completed_at,
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "status": v.status.value,
                    "cursor": v.cursor,
                    "safe_failure_code": v.safe_failure_code,
                    "completed_at": v.completed_at,
                },
            )
        )

    async def add_proposal(self, v: CommerceActionProposal) -> bool:
        result = await self.session.execute(
            pg_insert(action_proposals)
            .values(
                id=v.id,
                tenant_id=v.tenant_id,
                connection_id=v.connection_id,
                product_id=v.product_id,
                action_type=v.action_type.value,
                external_product_id=v.external_product_id,
                external_variant_id=v.external_variant_id,
                external_order_id=v.external_order_id,
                exact_quantity=v.exact_quantity,
                exact_amount=v.exact_amount,
                currency=v.currency,
                reason=v.reason,
                evidence_refs=[dict(x) for x in v.evidence_refs],
                semantic_digest=v.semantic_digest,
                schema_version=v.schema_version,
                created_at=v.created_at,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "semantic_digest"])
            .returning(action_proposals.c.id)
        )
        return result.scalar_one_or_none() is not None

    async def proposals(self, product_id: UUID) -> tuple[CommerceActionProposal, ...]:
        rows = (
            await self.session.execute(
                select(action_proposals)
                .where(action_proposals.c.product_id == product_id)
                .order_by(action_proposals.c.created_at.desc())
            )
        ).mappings()
        return tuple(
            CommerceActionProposal(
                r["tenant_id"],
                r["connection_id"],
                r["product_id"],
                ActionType(r["action_type"]),
                r["external_product_id"],
                r["external_variant_id"],
                r["external_order_id"],
                r["exact_quantity"],
                Decimal(r["exact_amount"]) if r["exact_amount"] is not None else None,
                r["currency"],
                r["reason"],
                tuple(r["evidence_refs"]),
                r["semantic_digest"],
                r["id"],
                r["schema_version"],
                r["created_at"],
            )
            for r in rows
        )
