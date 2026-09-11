# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,assignment,union-attr"

from uuid import uuid4

import pytest
from fastapi import HTTPException

from creative_marketer.agent_runtime.domain import (
    AgentRunDenied,
    AgentRunNotFound,
    AgentRunNotReady,
    parse_research_output,
)
from creative_marketer_api.research_routes import AgentRunStart, create_research_router
from tests.test_agent_runtime_domain import block, output, run


def endpoint(router, path: str, method: str):
    return next(
        route.endpoint for route in router.routes if route.path == path and method in route.methods
    )


class AgentService:
    def __init__(self):
        self.run = run()
        reference = block()
        self.snapshot = parse_research_output(
            output(reference), run=self.run, selected_blocks=(reference,)
        )
        self.request_error = None

    async def request_researcher(self, _context, *, product_id, idempotency_key):
        assert product_id == self.run.product_id
        assert idempotency_key
        if self.request_error:
            raise self.request_error
        return self.run

    async def list_runs(self, _context, product_id):
        assert product_id == self.run.product_id
        return (self.run,)

    async def get_run(self, _context, run_id):
        if run_id != self.run.id:
            raise AgentRunNotFound("missing")
        return self.run

    async def list_snapshots(self, _context, product_id):
        assert product_id == self.run.product_id
        return (self.snapshot,)

    async def get_snapshot(self, _context, snapshot_id):
        if snapshot_id != self.snapshot.id:
            raise AgentRunNotFound("missing")
        return self.snapshot

    async def snapshot_freshness(self, _context, snapshot):
        assert snapshot is self.snapshot
        return "current"


def router(service: AgentService):
    return create_research_router(None, None, None, "test", None, service)


@pytest.mark.asyncio
async def test_researcher_run_and_snapshot_routes_return_governed_state() -> None:
    service = AgentService()
    value = router(service)
    context = object()
    started = await endpoint(value, "/v1/products/{product_id}/research/runs", "POST")(
        service.run.product_id, AgentRunStart(idempotency_key="browser-request"), context
    )
    listed = await endpoint(value, "/v1/products/{product_id}/research/runs", "GET")(
        service.run.product_id, context
    )
    fetched = await endpoint(value, "/v1/agent-runs/{run_id}", "GET")(service.run.id, context)
    snapshots = await endpoint(value, "/v1/products/{product_id}/research/snapshots", "GET")(
        service.run.product_id, context
    )
    snapshot = await endpoint(value, "/v1/research/snapshots/{snapshot_id}", "GET")(
        service.snapshot.id, context
    )
    assert started.id == fetched.id == listed[0].id
    assert fetched.operational_status == "normal"
    assert not fetched.is_stranded
    assert fetched.recovery_of_run_id is None
    assert snapshots[0].freshness == snapshot.freshness == "current"
    assert snapshot.findings[0].citations[0].block_digest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (AgentRunDenied("denied"), 403),
        (ValueError("invalid"), 422),
        (AgentRunNotReady("not ready"), 409),
    ],
)
async def test_start_researcher_maps_safe_error_codes(error, status_code) -> None:
    service = AgentService()
    service.request_error = error
    start = endpoint(router(service), "/v1/products/{product_id}/research/runs", "POST")
    with pytest.raises(HTTPException) as raised:
        await start(
            service.run.product_id, AgentRunStart(idempotency_key="browser-request"), object()
        )
    assert raised.value.status_code == status_code


@pytest.mark.asyncio
async def test_researcher_get_routes_hide_missing_tenant_state() -> None:
    service = AgentService()
    value = router(service)
    for path in ("/v1/agent-runs/{run_id}", "/v1/research/snapshots/{snapshot_id}"):
        get = endpoint(value, path, "GET")
        with pytest.raises(HTTPException) as raised:
            await get(uuid4(), object())
        assert raised.value.status_code == 404
