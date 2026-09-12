# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,assignment"

from dataclasses import replace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import HTTPException

import creative_marketer_api.production_routes as production_routes
from creative_marketer.agent_runtime.domain import AgentRunNotReady
from creative_marketer.identity.application.errors import (
    AuthenticationUnavailable,
    TenantAccessDenied,
    Unauthenticated,
)
from creative_marketer.production.domain import (
    GenerationJob,
    GenerationJobStatus,
    MediaKind,
    ProductionNotFound,
    ProductionPermissionDenied,
    ProductionPlanDecision,
    ProductionPlanDecisionState,
)
from creative_marketer_api.production_routes import ProducerRunStart, create_production_router

from .test_agent_runtime_domain import run
from .test_production_service import service_fixture


def endpoint(router, path: str, method: str):
    return next(
        route.endpoint for route in router.routes if route.path == path and method in route.methods
    )


class AgentService:
    def __init__(self):
        self.run = replace(
            run(),
            agent_type="producer",
            input_context_kind="production_planning.v1",
            selected_evidence=(),
        )
        self.error = None

    async def request_producer(self, _context, **_values):
        if self.error:
            raise self.error
        return self.run


class ProductionService:
    def __init__(self):
        _service, repository, _uow = service_fixture()
        self.record = repository.record
        self.jobs = (
            GenerationJob(
                self.record.plan.tenant_id,
                self.record.plan.id,
                "image_one",
                MediaKind.IMAGE,
                "production_image",
                "image-route",
                "openai",
                "gpt-image-2",
                self.record.plan.cost.image_pricing_version,
                "sha256:" + "a" * 64,
                self.record.plan.context.selected_assets[:1],
                self.record.plan.cost.estimated_max_image_cost,
                "USD",
                status=GenerationJobStatus.READY,
            ),
        )
        self.error = None

    async def list_plans(self, _context, product_id):
        return (self.record,)

    async def get_plan(self, _context, plan_id):
        if self.error:
            raise self.error
        return self.record

    async def decide(self, _context, plan_id, state):
        if self.error:
            raise self.error
        decision = ProductionPlanDecision(
            self.record.plan.tenant_id,
            plan_id,
            self.record.plan.semantic_digest,
            state,
            uuid4(),
            "video-route",
            "image-route",
            self.record.plan.cost.video_pricing_version,
            self.record.plan.cost.image_pricing_version,
            self.record.plan.cost.estimated_total_cost,
            "USD",
        )
        return replace(self.record, decision=decision)

    async def list_jobs(self, _context, plan_id):
        if self.error:
            raise self.error
        return self.jobs

    async def get_job(self, _context, job_id):
        if self.error:
            raise self.error
        return self.jobs[0]


def router(agent, production):
    return create_production_router(None, None, agent, production, "test", None)


@pytest.mark.asyncio
async def test_production_routes_expose_plan_review_and_jobs() -> None:
    agent, production, context = AgentService(), ProductionService(), object()
    value = router(agent, production)
    plan = production.record.plan
    started = await endpoint(value, "/v1/creative/concepts/{concept_id}/production/runs", "POST")(
        plan.context.concept_id, ProducerRunStart(idempotency_key="producer-browser"), context
    )
    listed = await endpoint(value, "/v1/products/{product_id}/production/plans", "GET")(
        plan.product_id, context
    )
    loaded = await endpoint(value, "/v1/production/plans/{plan_id}", "GET")(plan.id, context)
    approved = await endpoint(value, "/v1/production/plans/{plan_id}/approve-generation", "POST")(
        plan.id, context
    )
    rejected = await endpoint(value, "/v1/production/plans/{plan_id}/reject", "POST")(
        plan.id, context
    )
    jobs = await endpoint(value, "/v1/production/plans/{plan_id}/jobs", "GET")(plan.id, context)
    job = await endpoint(value, "/v1/production/jobs/{job_id}", "GET")(
        production.jobs[0].id, context
    )
    assert started.id == agent.run.id
    assert listed[0].id == loaded.id == plan.id
    assert approved.status == ProductionPlanDecisionState.APPROVED_FOR_GENERATION.value
    assert rejected.status == ProductionPlanDecisionState.REJECTED.value
    assert jobs[0].id == job.id == production.jobs[0].id


@pytest.mark.asyncio
async def test_production_routes_map_errors() -> None:
    agent, production = AgentService(), ProductionService()
    value = router(agent, production)
    agent.error = AgentRunNotReady("missing")
    with pytest.raises(HTTPException) as start_error:
        await endpoint(value, "/v1/creative/concepts/{concept_id}/production/runs", "POST")(
            uuid4(), ProducerRunStart(idempotency_key="missing"), object()
        )
    assert start_error.value.status_code == 409
    production.error = ProductionNotFound("missing")
    with pytest.raises(HTTPException) as get_error:
        await endpoint(value, "/v1/production/plans/{plan_id}", "GET")(uuid4(), object())
    assert get_error.value.status_code == 404
    with pytest.raises(HTTPException):
        await endpoint(value, "/v1/production/plans/{plan_id}/jobs", "GET")(uuid4(), object())
    with pytest.raises(HTTPException):
        await endpoint(value, "/v1/production/jobs/{job_id}", "GET")(uuid4(), object())
    production.error = ProductionPermissionDenied("denied")
    with pytest.raises(HTTPException) as approval_error:
        await endpoint(value, "/v1/production/plans/{plan_id}/approve-generation", "POST")(
            uuid4(), object()
        )
    assert approval_error.value.status_code == 403


@pytest.mark.asyncio
async def test_production_route_authentication_boundary(monkeypatch) -> None:
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
    monkeypatch.setattr(production_routes, "ResolveTenantExecutionContext", Resolver)
    value = create_production_router(
        authenticator, object(), AgentService(), ProductionService(), "test", object()
    )
    dependency = cast(Any, value.routes[0]).dependant.dependencies[0].call
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
