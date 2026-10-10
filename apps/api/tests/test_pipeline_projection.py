# mypy: disable-error-code="no-untyped-def,arg-type"

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from creative_marketer.agent_runtime.domain import AgentRun, AgentRunStatus
from creative_marketer.assembly.domain import AssemblyReadiness
from creative_marketer.infrastructure.database.agent_runtime_repositories import (
    SqlAlchemyAgentRunRepository,
)
from creative_marketer.infrastructure.database.assembly_repositories import (
    SqlAlchemyAssemblyRepository,
)
from creative_marketer.infrastructure.database.orchestration_repositories import (
    SqlAlchemyOrchestrationRepository,
)
from creative_marketer.orchestration.pipeline import (
    AssemblyPipelineState,
    CreativePipelineState,
    FinalCreativePipelineState,
    MediaPipelineState,
    PipelineLocator,
    ProducerPipelineState,
    ResearchPipelineState,
)
from tests.test_agent_runtime_domain import run


class _MappingResult:
    def __init__(self, row: dict[str, object] | tuple[object, ...] | None) -> None:
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self.row if isinstance(self.row, tuple) else ())


class _Session:
    def __init__(
        self,
        *,
        scalars: list[object] | None = None,
        rows: list[dict[str, object] | tuple[object, ...] | None] | None = None,
    ) -> None:
        self.scalars = list(scalars or [])
        self.rows = list(rows or [])

    async def scalar(self, _query):
        return self.scalars.pop(0)

    async def execute(self, _query):
        return _MappingResult(self.rows.pop(0))


def _repository(monkeypatch, session: _Session, *runs: AgentRun):
    by_id = {value.id: value for value in runs}

    async def get(_repository, identifier):
        return by_id.get(identifier)

    async def get_snapshot(_repository, _identifier):
        return SimpleNamespace()

    async def snapshot_freshness(_repository, _snapshot):
        return "current"

    monkeypatch.setattr(SqlAlchemyAgentRunRepository, "get", get)
    monkeypatch.setattr(SqlAlchemyAgentRunRepository, "get_snapshot", get_snapshot)
    monkeypatch.setattr(
        SqlAlchemyAgentRunRepository,
        "snapshot_freshness",
        snapshot_freshness,
    )
    return SqlAlchemyOrchestrationRepository(cast(Any, session))


def _agent_run(product_id, agent_type: str, status: AgentRunStatus) -> AgentRun:
    return replace(
        run(),
        product_id=product_id,
        agent_type=agent_type,
        status=status,
    )


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
@pytest.mark.parametrize(
    ("status", "rows", "expected", "reason"),
    [
        (AgentRunStatus.RUNNING, [], ResearchPipelineState.RUNNING, None),
        (
            AgentRunStatus.SUCCEEDED,
            [None],
            ResearchPipelineState.FAILED_RESPONSE,
            "RESEARCH_SUCCESS_MISSING_SNAPSHOT",
        ),
    ],
)
async def test_research_projection_handles_running_and_missing_snapshot(
    monkeypatch,
    status: AgentRunStatus,
    rows: list[dict[str, object] | None],
    expected: ResearchPipelineState,
    reason: str | None,
) -> None:
    product_id = uuid4()
    research = _agent_run(product_id, "researcher", status)
    session = _Session(scalars=[product_id], rows=rows)
    repository = _repository(monkeypatch, session, research)

    observation = await repository.observe_pipeline(
        PipelineLocator(product_id, research_run_id=research.id)
    )

    assert observation is not None
    assert observation.research is expected
    assert observation.blocking_reason == reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "extra_rows", "concept_id", "expected"),
    [
        (AgentRunStatus.RUNNING, [], None, CreativePipelineState.RUNNING),
        (
            AgentRunStatus.FAILED,
            [{"status": "FAILED_RESPONSE", "provider_failure_reason": "MAX_OUTPUT_TOKENS"}],
            None,
            CreativePipelineState.OUTPUT_LIMITED,
        ),
        (AgentRunStatus.CANCELLED, [], None, CreativePipelineState.FAILED),
        (AgentRunStatus.SUCCEEDED, [None], None, CreativePipelineState.FAILED),
        (
            AgentRunStatus.SUCCEEDED,
            [
                {"id": uuid4(), "research_snapshot_id": uuid4()},
                None,
            ],
            uuid4(),
            CreativePipelineState.APPROVAL_REQUIRED,
        ),
        (
            AgentRunStatus.SUCCEEDED,
            [
                {"id": uuid4(), "research_snapshot_id": uuid4()},
                {"id": uuid4(), "state": "REJECTED"},
            ],
            uuid4(),
            CreativePipelineState.REJECTED,
        ),
    ],
)
async def test_creative_projection_preserves_each_governed_runtime_state(
    monkeypatch,
    status: AgentRunStatus,
    extra_rows: list[dict[str, object] | None],
    concept_id,
    expected: CreativePipelineState,
) -> None:
    product_id = uuid4()
    snapshot_id = uuid4()
    research = _agent_run(product_id, "researcher", AgentRunStatus.SUCCEEDED)
    creative = _agent_run(product_id, "creative_strategist", status)
    rows: list[dict[str, object] | None] = [
        {"id": snapshot_id, "product_snapshot_id": research.product_snapshot_id},
        *extra_rows,
    ]
    scalars: list[object] = [product_id]
    if concept_id is not None:
        assert rows[1] is not None
        rows[1]["research_snapshot_id"] = snapshot_id
        scalars.append(concept_id)
    session = _Session(scalars=scalars, rows=rows)
    repository = _repository(monkeypatch, session, research, creative)

    observation = await repository.observe_pipeline(
        PipelineLocator(
            product_id,
            research_run_id=research.id,
            creative_run_id=creative.id,
            concept_id=concept_id,
        )
    )

    assert observation is not None
    assert observation.creative is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("revalidation_row", "expected"),
    [
        (None, CreativePipelineState.STALE_RESEARCH),
        (
            {"id": uuid4(), "result": "REQUIRES_RESTRATEGY"},
            CreativePipelineState.REQUIRES_RESTRATEGY,
        ),
    ],
)
async def test_creative_projection_requires_explicit_current_revalidation(
    monkeypatch,
    revalidation_row: dict[str, object] | None,
    expected: CreativePipelineState,
) -> None:
    product_id = uuid4()
    snapshot_id = uuid4()
    concept_id = uuid4()
    revalidation_id = uuid4()
    research = _agent_run(product_id, "researcher", AgentRunStatus.SUCCEEDED)
    creative = _agent_run(product_id, "creative_strategist", AgentRunStatus.SUCCEEDED)
    session = _Session(
        scalars=[product_id, concept_id],
        rows=[
            {"id": snapshot_id, "product_snapshot_id": research.product_snapshot_id},
            {"id": uuid4(), "research_snapshot_id": uuid4()},
            {"id": uuid4(), "state": "APPROVED_FOR_PRODUCTION"},
            revalidation_row,
        ],
    )
    repository = _repository(monkeypatch, session, research, creative)

    observation = await repository.observe_pipeline(
        PipelineLocator(
            product_id,
            research_run_id=research.id,
            creative_run_id=creative.id,
            concept_id=concept_id,
            creative_revalidation_id=revalidation_id,
        )
    )

    assert observation is not None
    assert observation.creative is expected


def _producer_refs(
    concept_id,
    concept_digest: str,
    research_snapshot_id,
    product_snapshot_id,
    *,
    current_authority: bool,
) -> tuple[dict[str, object], ...]:
    return (
        {
            "kind": "approved_concept",
            "id": str(concept_id),
            "digest": concept_digest,
        },
        {
            "kind": "research_snapshot",
            "id": str(research_snapshot_id if current_authority else uuid4()),
        },
        {"kind": "product_snapshot", "id": str(product_snapshot_id)},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "current_authority", "tail_rows", "expected", "reason"),
    [
        (
            AgentRunStatus.RUNNING,
            False,
            [],
            ProducerPipelineState.RUNNING,
            "PRODUCER_AUTHORITY_CHANGED_DURING_EXECUTION",
        ),
        (
            AgentRunStatus.FAILED,
            True,
            [{"status": "FAILED_RESPONSE"}],
            ProducerPipelineState.CONTRACT_UPGRADE_REQUIRED,
            None,
        ),
        (
            AgentRunStatus.SUCCEEDED,
            True,
            [None],
            ProducerPipelineState.INVARIANT_VIOLATION,
            "PRODUCER_SUCCESS_MISSING_PRODUCTION_PLAN",
        ),
        (
            AgentRunStatus.SUCCEEDED,
            False,
            [],
            ProducerPipelineState.CONTRACT_UPGRADE_REQUIRED,
            "PRODUCER_OUTPUT_USES_HISTORICAL_AUTHORITY",
        ),
        (
            AgentRunStatus.SUCCEEDED,
            True,
            [
                {"id": uuid4()},
                {"state": "REJECTED"},
            ],
            ProducerPipelineState.PRODUCTION_PLAN_REJECTED,
            None,
        ),
        (
            AgentRunStatus.CANCELLED,
            True,
            [],
            ProducerPipelineState.FAILED_RESPONSE,
            "PRODUCER_RUN_CANCELLED",
        ),
    ],
)
async def test_producer_projection_preserves_failure_and_authority_states(
    monkeypatch,
    status: AgentRunStatus,
    current_authority: bool,
    tail_rows: list[dict[str, object] | None],
    expected: ProducerPipelineState,
    reason: str | None,
) -> None:
    product_id = uuid4()
    snapshot_id = uuid4()
    concept_set_id = uuid4()
    concept_id = uuid4()
    concept_digest = "sha256:" + "c" * 64
    research = _agent_run(product_id, "researcher", AgentRunStatus.SUCCEEDED)
    creative = _agent_run(product_id, "creative_strategist", AgentRunStatus.SUCCEEDED)
    producer = replace(
        _agent_run(product_id, "producer", status),
        output_contract_key="production.production_plan",
        output_contract_version=1,
        failure_code="PRODUCTION_PLAN_INVALID" if status is AgentRunStatus.FAILED else None,
        input_context_refs=_producer_refs(
            concept_id,
            concept_digest,
            snapshot_id,
            research.product_snapshot_id,
            current_authority=current_authority,
        ),
    )
    session = _Session(
        scalars=[product_id, concept_id, concept_digest],
        rows=[
            {"id": snapshot_id, "product_snapshot_id": research.product_snapshot_id},
            {"id": concept_set_id, "research_snapshot_id": snapshot_id},
            {"id": uuid4(), "state": "APPROVED_FOR_PRODUCTION"},
            *tail_rows,
        ],
    )
    repository = _repository(monkeypatch, session, research, creative, producer)

    observation = await repository.observe_pipeline(
        PipelineLocator(
            product_id,
            research_run_id=research.id,
            creative_run_id=creative.id,
            concept_id=concept_id,
            producer_run_id=producer.id,
        )
    )

    assert observation is not None
    assert observation.producer is expected
    assert observation.blocking_reason == reason


@pytest.mark.asyncio
async def test_producer_projection_rejects_missing_or_cross_lineage_bound_run(monkeypatch) -> None:
    product_id = uuid4()
    snapshot_id = uuid4()
    concept_id = uuid4()
    concept_digest = "sha256:" + "d" * 64
    research = _agent_run(product_id, "researcher", AgentRunStatus.SUCCEEDED)
    creative = _agent_run(product_id, "creative_strategist", AgentRunStatus.SUCCEEDED)
    missing_producer_id = uuid4()
    session = _Session(
        scalars=[product_id, concept_id],
        rows=[
            {"id": snapshot_id, "product_snapshot_id": research.product_snapshot_id},
            {"id": uuid4(), "research_snapshot_id": snapshot_id},
            {"id": uuid4(), "state": "APPROVED_FOR_PRODUCTION"},
        ],
    )
    repository = _repository(monkeypatch, session, research, creative)
    missing = await repository.observe_pipeline(
        PipelineLocator(
            product_id,
            research_run_id=research.id,
            creative_run_id=creative.id,
            concept_id=concept_id,
            producer_run_id=missing_producer_id,
        )
    )
    assert missing is None

    cross_lineage = replace(
        _agent_run(product_id, "producer", AgentRunStatus.PENDING),
        input_context_refs=_producer_refs(
            uuid4(),
            concept_digest,
            snapshot_id,
            research.product_snapshot_id,
            current_authority=True,
        ),
    )
    session = _Session(
        scalars=[product_id, concept_id, concept_digest],
        rows=[
            {"id": snapshot_id, "product_snapshot_id": research.product_snapshot_id},
            {"id": uuid4(), "research_snapshot_id": snapshot_id},
            {"id": uuid4(), "state": "APPROVED_FOR_PRODUCTION"},
        ],
    )
    repository = _repository(monkeypatch, session, research, creative, cross_lineage)
    mismatched = await repository.observe_pipeline(
        PipelineLocator(
            product_id,
            research_run_id=research.id,
            creative_run_id=creative.id,
            concept_id=concept_id,
            producer_run_id=cross_lineage.id,
        )
    )
    assert mismatched is None


async def _approved_plan_observation(
    monkeypatch,
    *,
    job_statuses: tuple[str, ...],
    assembly_plan_id=None,
    final_creative_id=None,
):
    product_id = uuid4()
    snapshot_id = uuid4()
    concept_set_id = uuid4()
    concept_id = uuid4()
    concept_digest = "sha256:" + "e" * 64
    plan_id = uuid4()
    research = _agent_run(product_id, "researcher", AgentRunStatus.SUCCEEDED)
    creative = _agent_run(product_id, "creative_strategist", AgentRunStatus.SUCCEEDED)
    producer = replace(
        _agent_run(product_id, "producer", AgentRunStatus.SUCCEEDED),
        output_contract_key="production.production_plan",
        output_contract_version=3,
        input_context_refs=_producer_refs(
            concept_id,
            concept_digest,
            snapshot_id,
            research.product_snapshot_id,
            current_authority=True,
        ),
    )
    session = _Session(
        scalars=[product_id, concept_id, concept_digest],
        rows=[
            {"id": snapshot_id, "product_snapshot_id": research.product_snapshot_id},
            {"id": concept_set_id, "research_snapshot_id": snapshot_id},
            {"id": uuid4(), "state": "APPROVED_FOR_PRODUCTION"},
            {"id": plan_id},
            {"state": "APPROVED_FOR_GENERATION"},
            job_statuses,
        ],
    )
    repository = _repository(monkeypatch, session, research, creative, producer)
    observation = await repository.observe_pipeline(
        PipelineLocator(
            product_id,
            research_run_id=research.id,
            creative_run_id=creative.id,
            concept_id=concept_id,
            producer_run_id=producer.id,
            production_plan_id=plan_id,
            use_latest_when_unbound=False,
            assembly_plan_id=assembly_plan_id,
            final_creative_id=final_creative_id,
        )
    )
    return observation, plan_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("job_statuses", "expected", "reason"),
    [
        ((), MediaPipelineState.INVARIANT_VIOLATION, "APPROVED_PLAN_MISSING_GENERATION_JOBS"),
        (("READY",), MediaPipelineState.READY, None),
        (("PROCESSING",), MediaPipelineState.RUNNING, None),
        (("FAILED",), MediaPipelineState.FAILED, "GENERATION_JOB_FAILED"),
        (
            ("OUTCOME_UNKNOWN",),
            MediaPipelineState.OUTCOME_UNKNOWN,
            "GENERATION_PROVIDER_OUTCOME_UNKNOWN",
        ),
    ],
)
async def test_approved_plan_projects_every_non_success_media_state(
    monkeypatch,
    job_statuses: tuple[str, ...],
    expected: MediaPipelineState,
    reason: str | None,
) -> None:
    observation, _plan_id = await _approved_plan_observation(monkeypatch, job_statuses=job_statuses)

    assert observation is not None
    assert observation.media is expected
    assert observation.blocking_reason == reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("readiness", "expected", "reason"),
    [
        (AssemblyReadiness.READY, AssemblyPipelineState.READY_TO_PLAN, None),
        (
            AssemblyReadiness.MISSING_MANUAL_MEDIA,
            AssemblyPipelineState.INPUT_REQUIRED,
            "MANUAL_ASSEMBLY_SOURCE_REQUIRED",
        ),
        (
            AssemblyReadiness.RIGHTS_CHANGED,
            AssemblyPipelineState.BLOCKED,
            "ASSEMBLY_RIGHTS_CHANGED",
        ),
    ],
)
async def test_successful_media_projects_assembly_input_readiness(
    monkeypatch,
    readiness: AssemblyReadiness,
    expected: AssemblyPipelineState,
    reason: str | None,
) -> None:
    async def production_input(_repository, _plan_id):
        return SimpleNamespace()

    async def list_plans(_repository, _plan_id):
        return ()

    monkeypatch.setattr(SqlAlchemyAssemblyRepository, "production_input", production_input)
    monkeypatch.setattr(SqlAlchemyAssemblyRepository, "list_plans", list_plans)
    monkeypatch.setattr(
        "creative_marketer.infrastructure.database.orchestration_repositories.evaluate_readiness",
        lambda _source: SimpleNamespace(status=readiness),
    )

    observation, _plan_id = await _approved_plan_observation(
        monkeypatch, job_statuses=("SUCCEEDED", "SUCCEEDED")
    )

    assert observation is not None
    assert observation.media is MediaPipelineState.SUCCEEDED
    assert observation.assembly is expected
    assert observation.blocking_reason == reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("job_status", "final_id", "decision", "assembly", "final", "reason"),
    [
        (
            "FAILED",
            None,
            None,
            AssemblyPipelineState.FAILED,
            FinalCreativePipelineState.NOT_STARTED,
            "ASSEMBLY_RENDER_FAILED",
        ),
        (
            "SUCCEEDED",
            None,
            None,
            AssemblyPipelineState.INVARIANT_VIOLATION,
            FinalCreativePipelineState.NOT_STARTED,
            "ASSEMBLY_SUCCESS_MISSING_FINAL_CREATIVE",
        ),
        (
            "SUCCEEDED",
            "new",
            None,
            AssemblyPipelineState.SUCCEEDED,
            FinalCreativePipelineState.REVIEW_REQUIRED,
            None,
        ),
        (
            "SUCCEEDED",
            "new",
            "APPROVED_FOR_PUBLISHING",
            AssemblyPipelineState.SUCCEEDED,
            FinalCreativePipelineState.APPROVED,
            None,
        ),
        (
            "SUCCEEDED",
            "new",
            "REJECTED",
            AssemblyPipelineState.SUCCEEDED,
            FinalCreativePipelineState.REJECTED,
            "FINAL_CREATIVE_REJECTED",
        ),
    ],
)
async def test_existing_assembly_plan_projects_job_and_final_review_states(
    monkeypatch,
    job_status: str,
    final_id: str | None,
    decision: str | None,
    assembly: AssemblyPipelineState,
    final: FinalCreativePipelineState,
    reason: str | None,
) -> None:
    assembly_plan_id = uuid4()
    final_creative_id = uuid4() if final_id is not None else None

    async def production_input(_repository, _plan_id):
        return SimpleNamespace()

    async def list_plans(_repository, _plan_id):
        return (
            SimpleNamespace(
                plan=SimpleNamespace(id=assembly_plan_id),
                job=SimpleNamespace(
                    status=SimpleNamespace(value=job_status),
                    failure_code="ASSEMBLY_RENDER_FAILED" if job_status == "FAILED" else None,
                    final_creative_id=final_creative_id,
                ),
            ),
        )

    async def get_final(_repository, _final_id):
        return SimpleNamespace(
            decision=(
                None if decision is None else SimpleNamespace(state=SimpleNamespace(value=decision))
            )
        )

    monkeypatch.setattr(SqlAlchemyAssemblyRepository, "production_input", production_input)
    monkeypatch.setattr(SqlAlchemyAssemblyRepository, "list_plans", list_plans)
    monkeypatch.setattr(SqlAlchemyAssemblyRepository, "get_final", get_final)
    monkeypatch.setattr(
        "creative_marketer.infrastructure.database.orchestration_repositories.evaluate_readiness",
        lambda _source: SimpleNamespace(status=AssemblyReadiness.READY),
    )

    observation, _plan_id = await _approved_plan_observation(
        monkeypatch,
        job_statuses=("SUCCEEDED",),
        assembly_plan_id=assembly_plan_id,
        final_creative_id=final_creative_id,
    )

    assert observation is not None
    assert observation.assembly is assembly
    assert observation.final_creative is final
    assert observation.blocking_reason == reason


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
