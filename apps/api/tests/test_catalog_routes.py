# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,union-attr"

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from creative_marketer.catalog.application import (
    CatalogNotFound,
    CatalogPermissionDenied,
)
from creative_marketer_api.catalog_routes import create_catalog_router


def endpoint(router, path: str, method: str):
    return next(
        route.endpoint for route in router.routes if route.path == path and method in route.methods
    )


class CatalogServiceStub:
    def __init__(self) -> None:
        self.create_error: Exception | None = None
        self.workspace_error: Exception | None = None
        self.latest_snapshot: object | None = None

    async def create_snapshot(self, _context, _product_id):
        assert self.create_error is not None
        raise self.create_error

    async def get_workspace(self, _context, _product_id):
        if self.workspace_error is not None:
            raise self.workspace_error
        return SimpleNamespace(latest_snapshot=self.latest_snapshot)


def router(service: CatalogServiceStub):
    return create_catalog_router(None, None, service, None, "test", None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (CatalogNotFound("missing"), 404, "product_not_found"),
        (CatalogPermissionDenied("denied"), 403, "catalog_mutation_denied"),
    ],
)
async def test_snapshot_creation_errors_are_safely_mapped(error, status_code, detail) -> None:
    service = CatalogServiceStub()
    service.create_error = error
    create = endpoint(router(service), "/v1/products/{product_id}/snapshots", "POST")

    with pytest.raises(HTTPException) as caught:
        await create(uuid4(), object())

    assert caught.value.status_code == status_code
    assert caught.value.detail == detail


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace_missing", [True, False])
async def test_latest_snapshot_absence_is_safely_mapped(workspace_missing: bool) -> None:
    service = CatalogServiceStub()
    if workspace_missing:
        service.workspace_error = CatalogNotFound("missing")
    latest = endpoint(router(service), "/v1/products/{product_id}/snapshots/latest", "GET")

    with pytest.raises(HTTPException) as caught:
        await latest(uuid4(), object())

    assert caught.value.status_code == 404
    assert caught.value.detail == (
        "product_not_found" if workspace_missing else "snapshot_not_found"
    )


@pytest.mark.asyncio
async def test_latest_snapshot_returns_the_current_product_binding() -> None:
    service = CatalogServiceStub()
    product_id, snapshot_id = uuid4(), uuid4()
    service.latest_snapshot = SimpleNamespace(
        id=snapshot_id,
        product_id=product_id,
        schema_version=2,
        source_revision=7,
        digest="sha256:" + "1" * 64,
        created_at=datetime.now(UTC),
    )
    latest = endpoint(router(service), "/v1/products/{product_id}/snapshots/latest", "GET")

    response = await latest(product_id, object())

    assert response.id == snapshot_id
    assert response.product_id == product_id
    assert response.source_revision == 7
