# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from creative_marketer.intelligence.domain import (
    DataTrustLevel,
    ExperimentDecisionKind,
    InsightDecisionKind,
    IntelligenceNotFound,
)
from creative_marketer_api.intelligence_routes import (
    AnalyzeRequest,
    DecisionRequest,
    GenerateConceptsRequest,
    create_intelligence_router,
)
from tests.test_agent_runtime_application import context
from tests.test_agent_runtime_domain import run


class Runtime:
    fail = False

    async def request_intelligence(self, *_args, **_kwargs):
        if self.fail:
            raise ValueError("invalid")
        return run()

    async def request_creative_strategist(self, *_args, **_kwargs):
        if self.fail:
            raise ValueError("invalid")
        return run()


class Service:
    fail = False

    def __init__(self):
        now = datetime.now(UTC)
        self.report = SimpleNamespace(
            id=uuid4(),
            product_id=uuid4(),
            agent_run_id=uuid4(),
            context_manifest_id=uuid4(),
            context_manifest_digest="sha256:" + "a" * 64,
            data_trust_level=DataTrustLevel.SYNTHETIC,
            summary="Synthetic workflow report.",
            observations=(),
            comparative_findings=(),
            limitations=("Synthetic demo data.",),
            semantic_digest="sha256:" + "b" * 64,
            created_at=now,
        )
        self.proposal = SimpleNamespace(id=uuid4(), product_id=self.report.product_id)

    async def list_reports(self, *_args):
        return (self.report,)

    async def get_report(self, *_args):
        if self.fail:
            raise IntelligenceNotFound("missing")
        return self.report, (), ()

    async def decide_insight(self, _ctx, candidate_id, decision):
        if self.fail:
            raise IntelligenceNotFound("missing")
        return SimpleNamespace(
            id=uuid4(), candidate_id=candidate_id, decision=decision, created_at=datetime.now(UTC)
        )

    async def decide_experiment(self, _ctx, proposal_id, decision):
        if self.fail:
            raise IntelligenceNotFound("missing")
        return SimpleNamespace(
            id=uuid4(), proposal_id=proposal_id, decision=decision, created_at=datetime.now(UTC)
        )

    async def approved_proposal(self, *_args):
        if self.fail:
            raise IntelligenceNotFound("missing")
        return self.proposal


def endpoint(router, name):
    return next(route.endpoint for route in router.routes if route.name == name)


@pytest.mark.asyncio
async def test_intelligence_route_handlers_success_and_safe_errors() -> None:
    runtime, service = Runtime(), Service()
    router = create_intelligence_router(None, None, runtime, service, "test", None)
    ctx = context(uuid4())
    product_id, candidate_id, proposal_id = uuid4(), uuid4(), service.proposal.id

    assert (
        await endpoint(router, "analyze")(
            product_id, AnalyzeRequest(idempotency_key="analysis"), ctx
        )
    ).id
    assert len(await endpoint(router, "list_reports")(product_id, ctx)) == 1
    assert (await endpoint(router, "get_report")(service.report.id, ctx)).id == service.report.id
    assert (
        await endpoint(router, "decide_insight")(
            candidate_id,
            DecisionRequest(decision=InsightDecisionKind.REJECT.value),
            ctx,
        )
    )["decision"] == InsightDecisionKind.REJECT.value
    assert (
        await endpoint(router, "decide_experiment")(
            proposal_id,
            DecisionRequest(decision=ExperimentDecisionKind.REJECTED.value),
            ctx,
        )
    )["decision"] == ExperimentDecisionKind.REJECTED.value
    assert (
        await endpoint(router, "generate_concepts")(
            proposal_id,
            GenerateConceptsRequest(idempotency_key="creative", concept_count=3),
            ctx,
        )
    ).id

    runtime.fail = service.fail = True
    for name, args in (
        ("analyze", (product_id, AnalyzeRequest(idempotency_key="bad"), ctx)),
        ("get_report", (uuid4(), ctx)),
        (
            "decide_insight",
            (candidate_id, DecisionRequest(decision=InsightDecisionKind.REJECT.value), ctx),
        ),
        (
            "decide_experiment",
            (proposal_id, DecisionRequest(decision=ExperimentDecisionKind.REJECTED.value), ctx),
        ),
        (
            "generate_concepts",
            (proposal_id, GenerateConceptsRequest(idempotency_key="bad", concept_count=3), ctx),
        ),
    ):
        with pytest.raises(HTTPException):
            await endpoint(router, name)(*args)
