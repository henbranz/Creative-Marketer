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
from creative_marketer.research.application import (
    ResearchConflict,
    ResearchNotFound,
    ResearchPermissionDenied,
    SocialCapabilityNotSupported,
)
from creative_marketer.research.domain import (
    ResearchTargetKind,
    ResearchValidationError,
    SocialEvidenceType,
    SocialPlatform,
)
from creative_marketer_api.research_routes import (
    AgentRunStart,
    ManualSocialEvidenceCreate,
    ResearchTargetCreate,
    SocialProviderQuery,
    SourceCreate,
    create_research_router,
)
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


class FailingResearchService:
    async def create_target(self, *_args, **_kwargs):
        raise ResearchValidationError("invalid target")

    async def list_targets(self, *_args, **_kwargs):
        raise ResearchNotFound("product not found")

    async def archive_target(self, *_args, **_kwargs):
        raise ResearchConflict("already archived")

    async def add_manual_social_evidence(self, *_args, **_kwargs):
        raise ResearchPermissionDenied("denied")

    async def list_social_evidence(self, *_args, **_kwargs):
        raise ResearchNotFound("product not found")

    async def get_social_evidence(self, *_args, **_kwargs):
        raise ResearchNotFound("evidence not found")

    async def query_social_provider(self, *_args, **_kwargs):
        raise SocialCapabilityNotSupported("disabled")

    def social_capabilities(self):
        return {platform: frozenset() for platform in SocialPlatform}

    async def create_source(self, *_args, **_kwargs):
        raise ResearchNotFound("product not found")

    async def list_sources(self, *_args, **_kwargs):
        raise ResearchNotFound("product not found")

    async def get_source(self, *_args, **_kwargs):
        raise ResearchNotFound("source not found")

    async def refresh_source(self, *_args, **_kwargs):
        raise ResearchConflict("archived")

    async def archive_source(self, *_args, **_kwargs):
        raise ResearchPermissionDenied("denied")

    async def list_fetches(self, *_args, **_kwargs):
        raise ResearchNotFound("source not found")

    async def get_evidence(self, *_args, **_kwargs):
        raise ResearchNotFound("evidence not found")

    async def manifest(self, *_args, **_kwargs):
        raise ResearchNotFound("product not found")


@pytest.mark.asyncio
async def test_social_routes_map_domain_failures_without_leaking_details() -> None:
    service = FailingResearchService()
    value = create_research_router(None, None, service, "test", None)
    context, product_id, target_id = object(), uuid4(), uuid4()
    calls = (
        (
            "/v1/products/{product_id}/research-targets",
            "POST",
            (
                product_id,
                ResearchTargetCreate(
                    kind=ResearchTargetKind.ADVERTISER,
                    display_name="Advertiser",
                    platform=SocialPlatform.FACEBOOK,
                ),
                context,
            ),
            422,
        ),
        ("/v1/products/{product_id}/research-targets", "GET", (product_id, context), 404),
        ("/v1/research-targets/{target_id}/archive", "POST", (target_id, context), 409),
        (
            "/v1/research-targets/{target_id}/social-evidence",
            "POST",
            (
                target_id,
                ManualSocialEvidenceCreate(
                    platform=SocialPlatform.FACEBOOK,
                    evidence_type=SocialEvidenceType.AD,
                    source_url="https://facebook.com/public",
                ),
                context,
            ),
            403,
        ),
        ("/v1/products/{product_id}/social-evidence", "GET", (product_id, context), 404),
        ("/v1/social-evidence/{evidence_id}", "GET", (uuid4(), context), 404),
        (
            "/v1/research-targets/{target_id}/provider-query",
            "POST",
            (target_id, SocialProviderQuery(capability="search_ads"), context),
            422,
        ),
    )
    for path, method, arguments, expected_status in calls:
        with pytest.raises(HTTPException) as raised:
            await endpoint(value, path, method)(*arguments)
        assert raised.value.status_code == expected_status

    capabilities = await endpoint(value, "/v1/social-research/capabilities", "GET")(context)
    assert all(not item.enabled for item in capabilities)

    source_calls = (
        (
            "/v1/products/{product_id}/research-sources",
            "POST",
            (
                product_id,
                SourceCreate(
                    url="https://example.com/",
                    display_name="Example",
                    category="other",
                ),
                context,
            ),
            404,
        ),
        ("/v1/products/{product_id}/research-sources", "GET", (product_id, context), 404),
        ("/v1/research-sources/{source_id}", "GET", (target_id, context), 404),
        ("/v1/research-sources/{source_id}/refresh", "POST", (target_id, context), 409),
        ("/v1/research-sources/{source_id}/archive", "POST", (target_id, context), 403),
        ("/v1/research-sources/{source_id}/fetches", "GET", (target_id, context), 404),
        ("/v1/research-evidence/{evidence_id}", "GET", (target_id, context), 404),
        (
            "/v1/products/{product_id}/research-context-manifest",
            "GET",
            (product_id, context),
            404,
        ),
    )
    for path, method, arguments, expected_status in source_calls:
        with pytest.raises(HTTPException) as raised:
            await endpoint(value, path, method)(*arguments)
        assert raised.value.status_code == expected_status
