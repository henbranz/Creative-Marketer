# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from creative_marketer.identity.application.errors import (
    AuthenticationUnavailable,
    MembershipInactive,
    TenantAccessDenied,
    Unauthenticated,
)
from creative_marketer.orchestration.application import CanonicalCycleState, CycleReadinessEngine
from creative_marketer.orchestration.domain import (
    CreativeCycle,
    CycleNotFound,
    CycleStage,
    CycleStatus,
    CycleTransition,
    SupervisorContextManifest,
    SupervisorReport,
)
from creative_marketer_api.orchestration_routes import (
    StartCycleRequest,
    create_orchestration_router,
)
from tests.test_agent_runtime_application import context


def endpoint(router, name):
    return next(route.endpoint for route in router.routes if route.name == name)


class Service:
    fail = False

    def __init__(self, ctx):
        self.cycle = CreativeCycle(
            ctx.tenant_id,
            uuid4(),
            ctx.user_id,
            uuid4(),
            "sha256:" + "a" * 64,
        )
        self.state = CanonicalCycleState(self.cycle)
        self.readiness = CycleReadinessEngine().cycle(self.state)
        self.transition = CycleTransition(
            ctx.tenant_id,
            self.cycle.id,
            None,
            CycleStage.CREATED,
            CycleStatus.ACTIVE,
            "CYCLE_STARTED",
            "user",
            ctx.user_id,
            ctx.correlation_id,
        )
        manifest = SupervisorContextManifest(
            ctx.tenant_id,
            self.cycle.product_id,
            self.cycle.id,
            1,
            CycleStage.CREATED,
            self.readiness,
            self.cycle.product_snapshot_id,
            self.cycle.product_snapshot_digest,
            self.cycle.artifacts,
            (),
        )
        self.report = SupervisorReport(
            ctx.tenant_id,
            self.cycle.product_id,
            self.cycle.id,
            manifest.id,
            manifest.semantic_digest,
            "Cycle summary.",
            "Cycle was created.",
            (),
            (),
            (),
        )

    def reject(self):
        if self.fail:
            raise CycleNotFound("missing")

    async def preflight(self, *_args):
        return self.readiness

    async def start(self, *_args, **_kwargs):
        self.reject()
        return self.cycle

    async def start_next_from_experiment(self, *_args, **_kwargs):
        self.reject()
        return self.cycle

    async def active(self, *_args):
        return None if self.fail else self.cycle

    async def get(self, *_args):
        self.reject()
        return self.state, self.readiness, (), (self.transition,), self.report

    async def reconcile(self, *_args):
        self.reject()
        return self.cycle

    async def cancel(self, *_args):
        self.reject()
        return replace(self.cycle, status=CycleStatus.CANCELLED, current_stage=CycleStage.CANCELLED)

    async def create_supervisor_report(self, *_args):
        self.reject()
        return SimpleNamespace(
            id=uuid4(), status=SimpleNamespace(value="PENDING"), agent_type="supervisor"
        )


@pytest.mark.asyncio
async def test_orchestration_handlers_render_full_cycle_and_safe_errors() -> None:
    ctx = context(uuid4())
    service = Service(ctx)
    router = create_orchestration_router(None, None, service, "test", None)
    product_id = service.cycle.product_id
    assert (await endpoint(router, "readiness")(product_id, ctx)).state == "READY"
    started = await endpoint(router, "start")(product_id, StartCycleRequest(), ctx)
    assert started.provider_mode == "DEMO_FAKE"
    next_cycle = await endpoint(router, "start_from_experiment")(uuid4(), ctx)
    assert next_cycle.id == service.cycle.id
    active = await endpoint(router, "active")(product_id, ctx)
    assert active.timeline[0].reason_code == "CYCLE_STARTED"
    assert active.supervisor_report.summary == "Cycle summary."
    assert (await endpoint(router, "get")(service.cycle.id, ctx)).id == service.cycle.id
    assert (await endpoint(router, "reconcile")(service.cycle.id, ctx)).id == service.cycle.id
    assert (await endpoint(router, "cancel")(service.cycle.id, ctx)).status == "CANCELLED"
    supervisor_run = await endpoint(router, "supervisor_report")(service.cycle.id, ctx)
    assert supervisor_run.status == "PENDING" and supervisor_run.agent_type == "supervisor"

    service.fail = True
    assert (await endpoint(router, "active")(product_id, ctx)).status_code == 204
    for name, args in (
        ("start", (product_id, StartCycleRequest(), ctx)),
        ("start_from_experiment", (uuid4(), ctx)),
        ("get", (uuid4(), ctx)),
        ("reconcile", (uuid4(), ctx)),
        ("cancel", (uuid4(), ctx)),
        ("supervisor_report", (uuid4(), ctx)),
    ):
        with pytest.raises(HTTPException) as error:
            await endpoint(router, name)(*args)
        assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "status_code"),
    [
        (AuthenticationUnavailable("offline"), 503),
        (Unauthenticated("invalid"), 401),
        (TenantAccessDenied("wrong tenant"), 403),
        (MembershipInactive("inactive"), 403),
    ],
)
async def test_orchestration_authentication_dependency_fails_closed(failure, status_code) -> None:
    class Authenticator:
        async def authenticate(self, _credential):
            raise failure

    router = create_orchestration_router(Authenticator(), None, None, "test", None)
    route = cast(
        APIRoute,
        next(route for route in router.routes if getattr(route, "name", None) == "readiness"),
    )
    dependency = route.dependant.dependencies[0].call
    assert dependency is not None

    with pytest.raises(HTTPException) as missing:
        await dependency(None, None, None)
    assert missing.value.status_code == 401

    with pytest.raises(HTTPException) as rejected:
        await dependency("Bearer credential", uuid4(), uuid4())
    assert rejected.value.status_code == status_code
