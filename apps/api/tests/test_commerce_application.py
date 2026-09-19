# mypy: disable-error-code="no-untyped-def,no-untyped-call,assignment"

from datetime import UTC, datetime
from types import TracebackType
from uuid import uuid4

import pytest

from creative_marketer.commerce.application import CommerceService
from creative_marketer.commerce.domain import CommerceConflict, CommerceNotFound, SyncType
from creative_marketer.commerce.provider import FakeCommerceProvider
from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus


class MemoryCommerceRepository:
    def __init__(self):
        self.connection_values = []
        self.mapping_values = []
        self.product_values = []
        self.variant_values = []
        self.inventory_values = []
        self.order_values = []
        self.proposal_values = []
        self.digests = set()
        self.sync_values = {}

    async def connection(self, connection_id):
        return next((value for value in self.connection_values if value.id == connection_id), None)

    async def connections(self):
        return tuple(self.connection_values)

    async def add_connection(self, value):
        self.connection_values.append(value)

    async def mapping(self, product_id):
        return next(
            (value for value in reversed(self.mapping_values) if value.product_id == product_id),
            None,
        )

    async def add_mapping(self, value):
        self.mapping_values.append(value)

    async def products(self, connection_id):
        return tuple(value for value in self.product_values if value.connection_id == connection_id)

    async def inventories(self, connection_id, product_id=None):
        values = tuple(
            value for value in self.inventory_values if value.connection_id == connection_id
        )
        if product_id is None:
            return values
        mapping = await self.mapping(product_id)
        return tuple(
            value
            for value in values
            if mapping and value.external_product_id == mapping.external_product_id
        )

    async def orders(self, connection_id, product_id=None):
        values = tuple(value for value in self.order_values if value.connection_id == connection_id)
        if product_id is None:
            return values
        mapping = await self.mapping(product_id)
        return tuple(
            value
            for value in values
            if mapping
            and any(line.external_product_id == mapping.external_product_id for line in value.lines)
        )

    async def latest_order(self, connection_id, external_order_id):
        return next(
            (
                value
                for value in reversed(self.order_values)
                if value.connection_id == connection_id
                and value.external_order_id == external_order_id
            ),
            None,
        )

    async def latest_inventory(self, connection_id, external_variant_id):
        return next(
            (
                value
                for value in reversed(self.inventory_values)
                if value.connection_id == connection_id
                and value.external_variant_id == external_variant_id
            ),
            None,
        )

    async def _add(self, values, value):
        if value.source_digest in self.digests:
            return False
        self.digests.add(value.source_digest)
        values.append(value)
        return True

    async def add_product(self, value):
        return await self._add(self.product_values, value)

    async def add_variant(self, value):
        return await self._add(self.variant_values, value)

    async def add_inventory(self, value):
        return await self._add(self.inventory_values, value)

    async def add_order(self, value):
        return await self._add(self.order_values, value)

    async def add_payment(self, value):
        return await self._add([], value)

    async def add_fulfillment(self, value):
        return await self._add([], value)

    async def add_sync_run(self, value):
        self.sync_values[value.id] = value

    async def add_proposal(self, value):
        self.proposal_values.append(value)
        return True

    async def proposals(self, product_id):
        return tuple(value for value in self.proposal_values if value.product_id == product_id)


class Sink:
    def __init__(self):
        self.values = []

    async def record_paid_order(self, context, **value):
        self.values.append((context.tenant_id, value))


class Appender:
    def __init__(self):
        self.values = []

    async def append(self, value):
        self.values.append(value)


class Uow:
    def __init__(self, repo, audit, outbox):
        self.commerce = repo
        self.audit = audit
        self.outbox = outbox
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self):
        self.commits += 1


def context(tenant_id=None, role=MembershipRole.OWNER):
    now = datetime.now(UTC)
    user = uuid4()
    return ExecutionContext(
        tenant_id or uuid4(),
        Actor(ActorKind.USER, user),
        user,
        role,
        MembershipStatus.ACTIVE,
        "test",
        AuthenticationAssurance(now, "test", "high"),
    )


@pytest.mark.asyncio
async def test_full_fake_ingestion_is_explicit_idempotent_and_feeds_paid_attribution() -> None:
    ctx = context()
    repo, audit, outbox, sink = MemoryCommerceRepository(), Appender(), Appender(), Sink()
    uow = Uow(repo, audit, outbox)
    provider = FakeCommerceProvider(page_size=10)
    service = CommerceService(lambda tenant_id: uow, provider, sink)

    connection = await service.create_fake_connection(ctx)
    provider.seed_store(connection.external_store_id, attribution_code="cm_demo_reference")
    assert (await service.list_connections(ctx))[0].provider == "fake"

    catalog = await service.sync(ctx, connection.id, SyncType.CATALOG)
    assert catalog.added == 2
    product_id = uuid4()
    mapping = await service.map_product(
        ctx,
        product_id=product_id,
        connection_id=connection.id,
        external_product_id="fake-product-1",
        external_variant_id="fake-variant-1",
    )
    assert mapping.created_by == ctx.user_id
    with pytest.raises(CommerceConflict):
        await service.map_product(
            ctx,
            product_id=product_id,
            connection_id=connection.id,
            external_product_id="fake-product-1",
        )

    inventory = await service.sync(ctx, connection.id, SyncType.INVENTORY)
    orders = await service.sync(ctx, connection.id, SyncType.ORDERS)
    assert (inventory.added, orders.added) == (2, 3)
    duplicate_catalog = await service.sync(ctx, connection.id, SyncType.CATALOG)
    duplicate_inventory = await service.sync(ctx, connection.id, SyncType.INVENTORY)
    assert (duplicate_catalog.unchanged, duplicate_inventory.unchanged) == (2, 2)
    assert len(sink.values) == 1
    workspace = await service.workspace(ctx, product_id)
    assert workspace["inventory_exceptions"][0]["kind"] == "LOW_STOCK"
    assert workspace["order_exceptions"][0]["kind"] == "PAID_BUT_UNFULFILLED"

    duplicate = await service.sync(ctx, connection.id, SyncType.ORDERS)
    assert duplicate.added == 0
    assert duplicate.unchanged == 3
    assert len(sink.values) == 1
    assert {event.event_type for event in outbox.values} == {"commerce.sync.completed.v1"}


@pytest.mark.asyncio
async def test_sync_failure_is_safe_and_all_is_only_a_workflow_instruction() -> None:
    ctx = context()
    repo, provider = MemoryCommerceRepository(), FakeCommerceProvider()
    uow = Uow(repo, Appender(), Appender())
    service = CommerceService(lambda tenant_id: uow, provider)
    connection = await service.create_fake_connection(ctx)
    provider.seed_store(connection.external_store_id)
    provider.fail_next_read = True
    with pytest.raises(RuntimeError, match="TEMPORARILY_UNAVAILABLE"):
        await service.sync(ctx, connection.id, SyncType.CATALOG)
    assert tuple(repo.sync_values.values())[-1].status.value == "TEMPORARILY_UNAVAILABLE"
    with pytest.raises(ValueError, match="finite individual syncs"):
        await service.sync(ctx, connection.id, SyncType.ALL)


@pytest.mark.asyncio
async def test_commerce_writes_require_active_owner_or_admin() -> None:
    repo, provider = MemoryCommerceRepository(), FakeCommerceProvider()
    service = CommerceService(lambda tenant_id: Uow(repo, Appender(), Appender()), provider)
    with pytest.raises(Exception, match="OWNER or ADMIN"):
        await service.create_fake_connection(context(role=MembershipRole.MEMBER))


@pytest.mark.asyncio
async def test_unmapped_workspace_and_unknown_mapping_targets_fail_closed() -> None:
    ctx = context()
    repo, provider = MemoryCommerceRepository(), FakeCommerceProvider()
    service = CommerceService(lambda tenant_id: Uow(repo, Appender(), Appender()), provider)
    empty = await service.workspace(ctx, uuid4())
    assert empty == {
        "mapping": None,
        "inventory": (),
        "orders": (),
        "proposals": (),
        "inventory_exceptions": (),
        "order_exceptions": (),
    }
    with pytest.raises(CommerceNotFound, match="connection"):
        await service.sync(ctx, uuid4(), SyncType.CATALOG)
    with pytest.raises(CommerceNotFound, match="connection"):
        await service.map_product(
            ctx,
            product_id=uuid4(),
            connection_id=uuid4(),
            external_product_id="missing",
        )
    connection = await service.create_fake_connection(ctx)
    with pytest.raises(CommerceNotFound, match="observed commerce product"):
        await service.map_product(
            ctx,
            product_id=uuid4(),
            connection_id=connection.id,
            external_product_id="missing",
        )
