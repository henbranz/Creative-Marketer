# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,assignment"

from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import HTTPException

import creative_marketer_api.commerce_routes as commerce_routes
from creative_marketer.agent_runtime.domain import AgentRunNotReady
from creative_marketer.approval_governance.domain import HumanDecision
from creative_marketer.commerce.domain import (
    CommerceConflict,
    CommerceConnection,
    ProductCommerceMapping,
    SyncStatus,
    SyncType,
)
from creative_marketer.commerce.provider import FakeCommerceProvider
from creative_marketer.commerce.workflow_execution import CommerceActionView, CommerceSyncView
from creative_marketer.identity.application.errors import (
    AuthenticationUnavailable,
    TenantAccessDenied,
    Unauthenticated,
)
from creative_marketer_api.commerce_routes import (
    AnalyzeRequest,
    FakeConnectionCreate,
    MappingCreate,
    SyncRequest,
    create_commerce_router,
)
from tests.test_agent_runtime_domain import run
from tests.test_commerce import inventory, order, proposal


def endpoint(router, path: str, method: str):
    return next(
        route.endpoint for route in router.routes if route.path == path and method in route.methods
    )


class Runtime:
    error = None

    async def request_commerce_operations(self, *_args, **_kwargs):
        if self.error:
            raise self.error
        return run()


class Service:
    error = None

    def __init__(self) -> None:
        self.connection = CommerceConnection(
            uuid4(), "fake", "Fake Store", "fake-store", "fake.local", ("orders.read",)
        )
        self.product_id = uuid4()
        self.mapping = ProductCommerceMapping(
            self.connection.tenant_id,
            self.product_id,
            self.connection.id,
            "product-1",
            uuid4(),
            "variant-1",
        )

    async def list_connections(self, *_args):
        return (self.connection,)

    async def create_fake_connection(self, *_args):
        if self.error:
            raise self.error
        return self.connection

    async def map_product(self, *_args, **_kwargs):
        if self.error:
            raise self.error
        return self.mapping

    async def sync(self, _ctx, _connection_id, sync_type, **_kwargs):
        if self.error:
            raise self.error
        return SimpleNamespace(
            run=SimpleNamespace(id=uuid4(), status=SyncStatus.SUCCEEDED),
            added=2,
            unchanged=1,
            next_cursor=None,
            sync_type=sync_type,
        )

    async def workspace(self, *_args):
        inv = inventory()
        observed_order = order()
        action = proposal()
        return {
            "mapping": self.mapping,
            "inventory": (inv,),
            "orders": (observed_order,),
            "proposals": (action,),
            "inventory_exceptions": ({"kind": "LOW_STOCK"},),
            "order_exceptions": ({"kind": "PAID_BUT_UNFULFILLED"},),
        }


class Governance:
    def __init__(self, service: Service) -> None:
        self.service = service
        self.store = self

    async def request_sync(self, _ctx, connection_id, _sync_types, _idempotency_key):
        if self.service.error:
            raise self.service.error
        return CommerceSyncView(uuid4(), connection_id, "QUEUED")

    async def latest_sync_view(self, _tenant_id, _connection_id):
        return None

    async def action_view(self, _tenant_id, proposal_id):
        return CommerceActionView(
            proposal_id,
            "commerce.inventory.adjust",
            "R5",
            "NEEDS_APPROVAL",
            None,
            None,
            None,
            None,
            None,
            "Fake Store",
            "DEMO-001",
            2,
            9,
            None,
            None,
            None,
            "replenish",
        )

    async def request_action(self, ctx, proposal_id):
        return await self.action_view(ctx.tenant_id, proposal_id)

    async def proposal_for_approval(self, _tenant_id, _approval_id):
        return self.service.product_id

    async def wake_action(self, ctx, proposal_id):
        return await self.action_view(ctx.tenant_id, proposal_id)


@pytest.mark.asyncio
async def test_commerce_routes_expose_fake_store_sync_workspace_and_analysis() -> None:
    runtime, service, provider = Runtime(), Service(), FakeCommerceProvider()
    router = create_commerce_router(
        None, None, runtime, service, provider, "test", None, Governance(service)
    )
    ctx = SimpleNamespace(tenant_id=service.connection.tenant_id)

    connections = await endpoint(router, "/v1/commerce/connections", "GET")(ctx)
    created = await endpoint(router, "/v1/commerce/connections/fake", "POST")(
        FakeConnectionCreate(), ctx
    )
    mapped = await endpoint(router, "/v1/commerce/mappings", "POST")(
        MappingCreate(
            product_id=service.product_id,
            connection_id=service.connection.id,
            external_product_id="product-1",
            external_variant_id="variant-1",
        ),
        ctx,
    )
    synced = await endpoint(router, "/v1/commerce/connections/{connection_id}/sync", "POST")(
        service.connection.id,
        SyncRequest(sync_type=SyncType.ORDERS, idempotency_key="sync-route"),
        ctx,
    )
    workspace = await endpoint(router, "/v1/products/{product_id}/commerce", "GET")(
        service.product_id, ctx
    )
    analyzed = await endpoint(router, "/v1/products/{product_id}/commerce/analyze", "POST")(
        service.product_id, AnalyzeRequest(idempotency_key="commerce-route"), ctx
    )

    assert connections[0].is_fake and created.is_fake
    assert service.connection.external_store_id in provider.products
    assert mapped["external_variant_id"] == "variant-1"
    assert synced == {
        "sync_request_id": synced["sync_request_id"],
        "status": "QUEUED",
        "safe_failure_code": None,
    }
    assert workspace["inventory"][0]["source"] == "Observed"
    assert workspace["orders"][0]["attributed"] is False
    assert workspace["proposals"][0]["source"] == "AI Proposal"
    assert workspace["proposals"][0]["risk_level"] == "R5"
    assert analyzed.id


@pytest.mark.asyncio
async def test_commerce_routes_return_unmapped_workspace_and_safe_conflicts() -> None:
    runtime, service, provider = Runtime(), Service(), FakeCommerceProvider()
    router = create_commerce_router(
        None, None, runtime, service, provider, "test", None, Governance(service)
    )

    async def empty_workspace(*_args):
        return {
            "mapping": None,
            "inventory": (),
            "orders": (),
            "proposals": (),
            "inventory_exceptions": (),
            "order_exceptions": (),
        }

    service.workspace = empty_workspace
    empty = await endpoint(router, "/v1/products/{product_id}/commerce", "GET")(
        service.product_id, SimpleNamespace(tenant_id=service.connection.tenant_id)
    )
    assert empty["mapping"] is None and empty["inventory"] == []

    service.error = CommerceConflict("conflict")
    for path, args in (
        (
            "/v1/commerce/connections/fake",
            (FakeConnectionCreate(), object()),
        ),
        (
            "/v1/commerce/mappings",
            (
                MappingCreate(
                    product_id=service.product_id,
                    connection_id=service.connection.id,
                    external_product_id="product-1",
                ),
                object(),
            ),
        ),
        (
            "/v1/commerce/connections/{connection_id}/sync",
            (
                service.connection.id,
                SyncRequest(sync_type=SyncType.ALL, idempotency_key="sync-conflict"),
                SimpleNamespace(tenant_id=service.connection.tenant_id),
            ),
        ),
    ):
        with pytest.raises(HTTPException) as error:
            await endpoint(router, path, "POST")(*args)
        assert error.value.status_code == 409

    runtime.error = AgentRunNotReady("not ready")
    with pytest.raises(HTTPException) as analysis_error:
        await endpoint(router, "/v1/products/{product_id}/commerce/analyze", "POST")(
            service.product_id, AnalyzeRequest(idempotency_key="not-ready"), object()
        )
    assert analysis_error.value.status_code == 409


@pytest.mark.asyncio
async def test_governed_action_and_approval_routes(monkeypatch) -> None:
    runtime, service, provider = Runtime(), Service(), FakeCommerceProvider()
    governance = Governance(service)

    class Decision:
        def __init__(self, _factory):
            pass

        async def __call__(self, *_args, **_kwargs):
            return SimpleNamespace(decision=HumanDecision.APPROVE)

    monkeypatch.setattr(commerce_routes, "DecideApproval", Decision)
    router = create_commerce_router(
        None, None, runtime, service, provider, "test", None, governance, object()
    )
    ctx = SimpleNamespace(tenant_id=service.connection.tenant_id)
    proposal_id, approval_id = uuid4(), uuid4()

    requested = await endpoint(router, "/v1/commerce/actions/{proposal_id}/request", "POST")(
        proposal_id, ctx
    )
    status_value = await endpoint(router, "/v1/commerce/actions/{proposal_id}", "GET")(
        proposal_id, ctx
    )
    decision = await endpoint(router, "/v1/approvals/{approval_id}/decision", "POST")(
        approval_id,
        commerce_routes.ApprovalDecisionInput(decision=HumanDecision.APPROVE),
        ctx,
    )

    assert requested == status_value
    assert requested["risk_level"] == "R5"
    assert requested["current_quantity"] == 2
    assert decision == {
        "approval_request_id": approval_id,
        "decision": "APPROVE",
        "proposal_id": service.product_id,
    }

    unavailable = create_commerce_router(
        None, None, runtime, service, provider, "test", None, None, None
    )
    for path, method, args in (
        ("/v1/commerce/actions/{proposal_id}/request", "POST", (proposal_id, ctx)),
        ("/v1/commerce/actions/{proposal_id}", "GET", (proposal_id, ctx)),
        (
            "/v1/approvals/{approval_id}/decision",
            "POST",
            (
                approval_id,
                commerce_routes.ApprovalDecisionInput(decision=HumanDecision.DENY),
                ctx,
            ),
        ),
    ):
        with pytest.raises(HTTPException) as error:
            await endpoint(unavailable, path, method)(*args)
        assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_commerce_route_authentication_boundary(monkeypatch) -> None:
    class Authenticator:
        error = None

        async def authenticate(self, _credential):
            if self.error:
                raise self.error
            return object()

    class Resolver:
        error = None

        def __init__(self, *_values):
            pass

        async def __call__(self, *_values):
            if self.error:
                raise self.error
            return object()

    authenticator = Authenticator()
    monkeypatch.setattr(commerce_routes, "ResolveTenantExecutionContext", Resolver)
    router = create_commerce_router(
        authenticator, object(), Runtime(), Service(), FakeCommerceProvider(), "test", object()
    )
    dependency = cast(Any, router.routes[0]).dependant.dependencies[0].call
    tenant_id = uuid4()

    with pytest.raises(HTTPException) as missing:
        await dependency(None, tenant_id, None)
    assert missing.value.status_code == 401
    authenticator.error = AuthenticationUnavailable()
    with pytest.raises(HTTPException) as unavailable:
        await dependency("Bearer token", tenant_id, None)
    assert unavailable.value.status_code == 503
    authenticator.error = Unauthenticated()
    with pytest.raises(HTTPException) as unknown:
        await dependency("Bearer token", tenant_id, None)
    assert unknown.value.status_code == 401
    authenticator.error = None
    Resolver.error = TenantAccessDenied()
    with pytest.raises(HTTPException) as denied:
        await dependency("Bearer token", tenant_id, None)
    assert denied.value.status_code == 403
    Resolver.error = None
    assert await dependency("Bearer token", tenant_id, uuid4()) is not None
