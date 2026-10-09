# mypy: disable-error-code="no-untyped-def,arg-type"

from dataclasses import replace
from typing import Any, cast
from uuid import uuid4

import pytest

from creative_marketer.agent_runtime.domain import AgentRunStatus
from creative_marketer.infrastructure.database.agent_runtime_repositories import (
    SqlAlchemyAgentRunRepository,
)
from creative_marketer.infrastructure.database.orchestration_repositories import (
    SqlAlchemyOrchestrationRepository,
)
from creative_marketer.orchestration.pipeline import (
    PipelineLocator,
    ResearchPipelineState,
)
from tests.test_agent_runtime_domain import run


class _MappingResult:
    def __init__(self, row: dict[str, object] | None) -> None:
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


class _Session:
    def __init__(
        self,
        *,
        scalars: list[object] | None = None,
        rows: list[dict[str, object] | None] | None = None,
    ) -> None:
        self.scalars = list(scalars or [])
        self.rows = list(rows or [])

    async def scalar(self, _query):
        return self.scalars.pop(0)

    async def execute(self, _query):
        return _MappingResult(self.rows.pop(0))


@pytest.mark.asyncio
async def test_failed_research_projection_uses_latest_persisted_attempt(monkeypatch) -> None:
    product_id = uuid4()
    failed = replace(
        run(),
        product_id=product_id,
        status=AgentRunStatus.FAILED,
        failure_code="MODEL_PROVIDER_KNOWN_NO_RESPONSE",
    )
    session = _Session(
        scalars=[product_id, failed.id],
        rows=[{"status": "FAILED_NO_RESPONSE"}],
    )

    async def get(_repository, identifier):
        return failed if identifier == failed.id else None

    monkeypatch.setattr(SqlAlchemyAgentRunRepository, "get", get)
    repository = SqlAlchemyOrchestrationRepository(cast(Any, session))

    observation = await repository.observe_pipeline(PipelineLocator(product_id))

    assert observation is not None
    assert observation.research is ResearchPipelineState.FAILED_NO_RESPONSE
    assert observation.research_run_id == failed.id
    assert not session.scalars and not session.rows


@pytest.mark.asyncio
async def test_experiment_handoff_projects_latest_immutable_decision() -> None:
    proposal_id = uuid4()
    product_id = uuid4()
    digest = "sha256:" + "a" * 64
    session = _Session(
        rows=[
            {
                "id": proposal_id,
                "product_id": product_id,
                "semantic_digest": digest,
            },
            {
                "decision": "APPROVED_FOR_CREATIVE",
                "proposal_digest": digest,
            },
        ]
    )
    repository = SqlAlchemyOrchestrationRepository(cast(Any, session))

    handoff = await repository.experiment_handoff(proposal_id)

    assert handoff is not None
    assert handoff.proposal_id == proposal_id
    assert handoff.product_id == product_id
    assert handoff.decision == "APPROVED_FOR_CREATIVE"
    assert handoff.decision_proposal_digest == digest
    assert not session.rows
