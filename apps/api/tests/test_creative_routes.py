# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,assignment,union-attr"

from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from creative_marketer.agent_runtime.domain import AgentRunNotReady
from creative_marketer.creative.domain import (
    CreativeConceptDecision,
    CreativeDecisionState,
    CreativeNotFound,
    CreativePermissionDenied,
)
from creative_marketer_api.creative_routes import (
    CreativeDecisionRequest,
    CreativeRunStart,
    create_creative_router,
)
from tests.test_agent_runtime_domain import run
from tests.test_creative_strategy import context as strategy_context
from tests.test_creative_strategy import output, validate


def endpoint(router, path: str, method: str):
    return next(
        route.endpoint for route in router.routes if route.path == path and method in route.methods
    )


class AgentService:
    def __init__(self):
        self.run = replace(
            run(),
            agent_type="creative_strategist",
            input_context_kind="creative_strategy.v1",
            selected_evidence=(),
            input_context_refs=({"kind": "creative_context", "digest": "sha256:" + "a" * 64},),
        )
        self.error = None

    async def request_creative_strategist(self, _context, **_values):
        if self.error:
            raise self.error
        return self.run

    async def list_runs(self, _context, product_id):
        return (self.run, run())

    async def get_snapshot(self, _context, snapshot_id):
        return object()

    async def snapshot_freshness(self, _context, snapshot):
        return "current"


class CreativeService:
    def __init__(self):
        value = strategy_context()
        self.value = validate(output(value), value)
        self.error = None

    async def list_sets(self, _context, product_id):
        return (self.value,)

    async def get_set(self, _context, set_id):
        if self.error or set_id != self.value.id:
            raise self.error or CreativeNotFound("missing")
        return self.value

    async def get_concept(self, _context, concept_id):
        concept = next((item for item in self.value.concepts if item.id == concept_id), None)
        if self.error or concept is None:
            raise self.error or CreativeNotFound("missing")
        return concept, None

    async def decide(self, context, concept_id, state, **values):
        if self.error:
            raise self.error
        return CreativeConceptDecision(
            self.value.tenant_id,
            self.value.product_id,
            concept_id,
            state,
            uuid4(),
            values.get("reason_code"),
            values.get("note"),
        )


def router(agent: AgentService, creative: CreativeService):
    return create_creative_router(None, None, agent, creative, "test", None)


@pytest.mark.asyncio
async def test_creative_routes_expose_runs_results_and_decisions() -> None:
    agent, creative, ctx = AgentService(), CreativeService(), object()
    value = router(agent, creative)
    product_id = agent.run.product_id
    started = await endpoint(value, "/v1/products/{product_id}/creative/runs", "POST")(
        product_id, CreativeRunStart(idempotency_key="creative-browser"), ctx
    )
    runs = await endpoint(value, "/v1/products/{product_id}/creative/runs", "GET")(product_id, ctx)
    sets = await endpoint(value, "/v1/products/{product_id}/creative/concept-sets", "GET")(
        product_id, ctx
    )
    loaded_set = await endpoint(value, "/v1/creative/concept-sets/{set_id}", "GET")(
        creative.value.id, ctx
    )
    concept = creative.value.concepts[0]
    loaded_concept = await endpoint(value, "/v1/creative/concepts/{concept_id}", "GET")(
        concept.id, ctx
    )
    decision = await endpoint(value, "/v1/creative/concepts/{concept_id}/decision", "POST")(
        concept.id,
        CreativeDecisionRequest(
            state=CreativeDecisionState.APPROVED_FOR_PRODUCTION,
            reason_code="READY",
            note="Proceed",
        ),
        ctx,
    )
    assert started.id == runs[0].id and len(runs) == 1
    assert sets[0].freshness == "CURRENT"
    assert loaded_set.id == creative.value.id
    assert loaded_concept.id == concept.id
    assert decision.state is CreativeDecisionState.APPROVED_FOR_PRODUCTION


@pytest.mark.asyncio
async def test_creative_routes_map_bounded_errors() -> None:
    agent, creative = AgentService(), CreativeService()
    value = router(agent, creative)
    agent.error = AgentRunNotReady("missing")
    with pytest.raises(HTTPException) as start_error:
        await endpoint(value, "/v1/products/{product_id}/creative/runs", "POST")(
            agent.run.product_id, CreativeRunStart(idempotency_key="missing"), object()
        )
    assert start_error.value.status_code == 409
    creative.error = CreativeNotFound("missing")
    with pytest.raises(HTTPException) as get_error:
        await endpoint(value, "/v1/creative/concept-sets/{set_id}", "GET")(uuid4(), object())
    assert get_error.value.status_code == 404
    creative.error = CreativePermissionDenied("denied")
    with pytest.raises(HTTPException) as decision_error:
        await endpoint(value, "/v1/creative/concepts/{concept_id}/decision", "POST")(
            uuid4(),
            CreativeDecisionRequest(state=CreativeDecisionState.REJECTED),
            object(),
        )
    assert decision_error.value.status_code == 403
