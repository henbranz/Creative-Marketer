from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.agent_runtime.domain import AgentRun
from creative_marketer.catalog.domain import evaluate_semantic_completeness
from creative_marketer.infrastructure.database.agent_governance_schema import agent_definitions
from creative_marketer.infrastructure.database.agent_runtime_repositories import (
    SqlAlchemyAgentRunRepository,
)
from creative_marketer.infrastructure.database.agent_runtime_schema import (
    agent_runs,
    model_attempts,
    research_snapshots,
)
from creative_marketer.infrastructure.database.assembly_schema import (
    assembly_jobs,
    assembly_plans,
    final_creative_decisions,
    final_creatives,
    manual_source_bindings,
)
from creative_marketer.infrastructure.database.catalog_schema import (
    product_briefs,
    product_knowledge_snapshots,
    product_profiles,
    products,
)
from creative_marketer.infrastructure.database.creative_schema import (
    concept_decisions,
    concept_revalidations,
    concept_sets,
    concepts,
)
from creative_marketer.infrastructure.database.intelligence_schema import (
    experiment_decisions,
    experiment_proposals,
    reports,
)
from creative_marketer.infrastructure.database.measurement_schema import performance_snapshots
from creative_marketer.infrastructure.database.production_schema import (
    generation_jobs,
    plan_decisions,
    production_plans,
    production_shots,
)
from creative_marketer.infrastructure.database.publishing_schema import (
    publication_decisions,
    publication_drafts,
    publication_jobs,
    publications,
)
from creative_marketer.infrastructure.database.research_schema import (
    evidence_snapshots,
    social_evidence_snapshots,
)
from creative_marketer.orchestration.application import (
    CanonicalCycleState,
    CyclePreflight,
    ExperimentHandoff,
)
from creative_marketer.orchestration.domain import (
    CreativeCycle,
    CreativeCycleStep,
    CycleArtifacts,
    CycleMode,
    CycleStage,
    CycleStatus,
    CycleTransition,
    StepStatus,
    SupervisorAction,
    SupervisorContextManifest,
    SupervisorReport,
)
from creative_marketer.orchestration.pipeline import (
    CreativePipelineState,
    PipelineFailureCategory,
    PipelineLocator,
    PipelineObservation,
    ProducerPipelineState,
    ResearchPipelineState,
    classify_creative_failure,
    classify_pipeline_failure,
    classify_producer_failure,
)
from creative_marketer.production.application import PRODUCTION_CONTRACT_VERSION

from .orchestration_schema import (
    creative_cycles,
    cycle_steps,
    cycle_transitions,
    supervisor_context_manifests,
    supervisor_reports,
)


def _uuid(value: object) -> UUID | None:
    return UUID(str(value)) if value else None


def _run_input_ref(run: AgentRun, kind: str) -> Mapping[str, object] | None:
    return next((item for item in run.input_context_refs if item.get("kind") == kind), None)


def _cycle(row: Mapping[str, Any]) -> CreativeCycle:
    bindings = row["artifact_bindings"] or {}
    return CreativeCycle(
        row["tenant_id"],
        row["product_id"],
        row["initiated_by"],
        row["product_snapshot_id"],
        row["product_snapshot_digest"],
        id=row["id"],
        status=CycleStatus(row["status"]),
        current_stage=CycleStage(row["current_stage"]),
        mode=CycleMode(row["mode"]),
        artifacts=CycleArtifacts(
            **{name: _uuid(bindings.get(name)) for name in CycleArtifacts.__dataclass_fields__}
        ),
        parent_cycle_id=row["parent_cycle_id"],
        source_experiment_proposal_id=row["source_experiment_proposal_id"],
        blocker_code=row["blocker_code"],
        failure_code=row["failure_code"],
        state_machine_version=row["state_machine_version"],
        cycle_version=row["cycle_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _cycle_values(value: CreativeCycle) -> dict[str, object]:
    return {
        "id": value.id,
        "tenant_id": value.tenant_id,
        "product_id": value.product_id,
        "initiated_by": value.initiated_by,
        "status": value.status.value,
        "current_stage": value.current_stage.value,
        "mode": value.mode.value,
        "product_snapshot_id": value.product_snapshot_id,
        "product_snapshot_digest": value.product_snapshot_digest,
        "artifact_bindings": value.artifacts.as_dict(),
        "parent_cycle_id": value.parent_cycle_id,
        "source_experiment_proposal_id": value.source_experiment_proposal_id,
        "blocker_code": value.blocker_code,
        "failure_code": value.failure_code,
        "state_machine_version": value.state_machine_version,
        "cycle_version": value.cycle_version,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
    }


def _step(row: Mapping[str, Any]) -> CreativeCycleStep:
    return CreativeCycleStep(
        row["tenant_id"],
        row["cycle_id"],
        row["step_key"],
        row["attempt"],
        StepStatus(row["status"]),
        row["idempotency_key"],
        row["agent_run_id"],
        row["workflow_ref"],
        row["artifact_ref"],
        row["failure_code"],
        row["id"],
        row["created_at"],
        row["completed_at"],
    )


def _transition(row: Mapping[str, Any]) -> CycleTransition:
    return CycleTransition(
        row["tenant_id"],
        row["cycle_id"],
        CycleStage(row["from_stage"]) if row["from_stage"] else None,
        CycleStage(row["to_stage"]),
        CycleStatus(row["status"]),
        row["reason_code"],
        row["actor_kind"],
        row["actor_id"],
        row["correlation_id"],
        row["id"],
        row["occurred_at"],
    )


class SqlAlchemyOrchestrationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def preflight(self, product_id: UUID) -> CyclePreflight:
        product = (
            await self.session.execute(select(products.c.id).where(products.c.id == product_id))
        ).scalar_one_or_none()
        profile = (
            (
                await self.session.execute(
                    select(product_profiles).where(product_profiles.c.product_id == product_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        brief = (
            (
                await self.session.execute(
                    select(product_briefs).where(product_briefs.c.product_id == product_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        snapshot = (
            (
                await self.session.execute(
                    select(product_knowledge_snapshots)
                    .where(product_knowledge_snapshots.c.product_id == product_id)
                    .order_by(product_knowledge_snapshots.c.created_at.desc())
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        web_count = int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(evidence_snapshots)
                    .where(evidence_snapshots.c.product_id == product_id)
                )
            ).scalar_one()
        )
        social_count = int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(social_evidence_snapshots)
                    .where(social_evidence_snapshots.c.product_id == product_id)
                )
            ).scalar_one()
        )
        researcher_count = int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(agent_definitions)
                    .where(
                        agent_definitions.c.agent_type == "researcher",
                        agent_definitions.c.status == "active",
                        agent_definitions.c.tenant_id.is_not(None),
                    )
                )
            ).scalar_one()
        )
        score = evaluate_semantic_completeness(
            dict(profile) if profile else {}, dict(brief["content"]) if brief else {}
        ).score
        return CyclePreflight(
            product is not None,
            score,
            snapshot["id"] if snapshot else None,
            snapshot["digest"] if snapshot else None,
            web_count + social_count,
            researcher_count >= 1,
        )

    async def active_for_product(
        self, product_id: UUID, *, for_update: bool = False
    ) -> CreativeCycle | None:
        query = (
            select(creative_cycles)
            .where(
                creative_cycles.c.product_id == product_id,
                creative_cycles.c.status.in_(("ACTIVE", "BLOCKED", "NEEDS_RECOVERY")),
            )
            .order_by(creative_cycles.c.created_at.desc())
            .limit(1)
        )
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(query)).mappings().one_or_none()
        return _cycle(cast(Mapping[str, Any], row)) if row else None

    async def parent_for_experiment(self, proposal_id: UUID) -> CreativeCycle | None:
        row = (
            (
                await self.session.execute(
                    select(creative_cycles)
                    .where(
                        creative_cycles.c.artifact_bindings["experiment_proposal_id"].astext
                        == str(proposal_id)
                    )
                    .order_by(creative_cycles.c.created_at.desc())
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        return _cycle(cast(Mapping[str, Any], row)) if row else None

    async def child_for_experiment(self, proposal_id: UUID) -> CreativeCycle | None:
        row = (
            (
                await self.session.execute(
                    select(creative_cycles)
                    .where(creative_cycles.c.source_experiment_proposal_id == proposal_id)
                    .order_by(creative_cycles.c.created_at)
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        return _cycle(cast(Mapping[str, Any], row)) if row else None

    async def experiment_handoff(self, proposal_id: UUID) -> ExperimentHandoff | None:
        proposal = (
            (
                await self.session.execute(
                    select(experiment_proposals).where(experiment_proposals.c.id == proposal_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if proposal is None:
            return None
        decision = (
            (
                await self.session.execute(
                    select(experiment_decisions)
                    .where(experiment_decisions.c.proposal_id == proposal_id)
                    .order_by(
                        experiment_decisions.c.created_at.desc(),
                        experiment_decisions.c.id.desc(),
                    )
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        return ExperimentHandoff(
            proposal_id,
            proposal["product_id"],
            proposal["semantic_digest"],
            decision["decision"] if decision else None,
            decision["proposal_digest"] if decision else None,
        )

    async def get(self, cycle_id: UUID, *, for_update: bool = False) -> CreativeCycle | None:
        query = select(creative_cycles).where(creative_cycles.c.id == cycle_id)
        if for_update:
            query = query.with_for_update()
        row = (await self.session.execute(query)).mappings().one_or_none()
        return _cycle(cast(Mapping[str, Any], row)) if row else None

    async def add(self, cycle: CreativeCycle) -> None:
        await self.session.execute(insert(creative_cycles).values(**_cycle_values(cycle)))

    async def update(self, cycle: CreativeCycle, *, expected_version: int) -> bool:
        values = _cycle_values(cycle)
        values.pop("id")
        values.pop("tenant_id")
        values.pop("created_at")
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(creative_cycles)
                .where(
                    creative_cycles.c.id == cycle.id,
                    creative_cycles.c.cycle_version == expected_version,
                )
                .values(**values)
            ),
        )
        return result.rowcount == 1

    async def add_transition(self, value: CycleTransition) -> None:
        await self.session.execute(
            insert(cycle_transitions).values(
                id=value.id,
                tenant_id=value.tenant_id,
                cycle_id=value.cycle_id,
                from_stage=value.from_stage.value if value.from_stage else None,
                to_stage=value.to_stage.value,
                status=value.status.value,
                reason_code=value.reason_code,
                actor_kind=value.actor_kind,
                actor_id=value.actor_id,
                correlation_id=value.correlation_id,
                occurred_at=value.occurred_at,
            )
        )

    async def add_step(self, value: CreativeCycleStep) -> None:
        await self.session.execute(
            insert(cycle_steps).values(
                id=value.id,
                tenant_id=value.tenant_id,
                cycle_id=value.cycle_id,
                step_key=value.step_key,
                attempt=value.attempt,
                status=value.status.value,
                idempotency_key=value.idempotency_key,
                agent_run_id=value.agent_run_id,
                workflow_ref=value.workflow_ref,
                artifact_ref=value.artifact_ref,
                failure_code=value.failure_code,
                created_at=value.created_at,
                completed_at=value.completed_at,
            )
        )

    async def step(self, cycle_id: UUID, step_key: str) -> CreativeCycleStep | None:
        row = (
            (
                await self.session.execute(
                    select(cycle_steps)
                    .where(cycle_steps.c.cycle_id == cycle_id, cycle_steps.c.step_key == step_key)
                    .order_by(cycle_steps.c.attempt.desc())
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        return _step(cast(Mapping[str, Any], row)) if row else None

    async def steps(self, cycle_id: UUID) -> tuple[CreativeCycleStep, ...]:
        rows = (
            await self.session.execute(
                select(cycle_steps)
                .where(cycle_steps.c.cycle_id == cycle_id)
                .order_by(cycle_steps.c.created_at)
            )
        ).mappings()
        return tuple(_step(cast(Mapping[str, Any], row)) for row in rows)

    async def transitions(self, cycle_id: UUID) -> tuple[CycleTransition, ...]:
        rows = (
            await self.session.execute(
                select(cycle_transitions)
                .where(cycle_transitions.c.cycle_id == cycle_id)
                .order_by(cycle_transitions.c.occurred_at)
            )
        ).mappings()
        return tuple(_transition(cast(Mapping[str, Any], row)) for row in rows)

    async def _run(self, cycle: CreativeCycle, kind: str) -> AgentRun | None:
        run_id = getattr(cycle.artifacts, f"{kind}_run_id")
        if run_id:
            return await SqlAlchemyAgentRunRepository(self.session).get(run_id)
        row = (
            await self.session.execute(
                select(agent_runs.c.id)
                .where(agent_runs.c.idempotency_key == f"cycle:{cycle.id}:{kind}:v1")
                .limit(1)
            )
        ).scalar_one_or_none()
        return await SqlAlchemyAgentRunRepository(self.session).get(row) if row else None

    async def canonical_state(self, cycle: CreativeCycle) -> CanonicalCycleState:
        research_run, creative_run, producer_run, intelligence_run = (
            await self._run(cycle, "research"),
            await self._run(cycle, "creative"),
            await self._run(cycle, "producer"),
            await self._run(cycle, "intelligence"),
        )

        async def latest_id(table: Any, *where: Any) -> UUID | None:
            return (
                await self.session.execute(
                    select(table.c.id).where(*where).order_by(table.c.created_at.desc()).limit(1)
                )
            ).scalar_one_or_none()

        research_id = cycle.artifacts.research_snapshot_id or (
            await latest_id(
                research_snapshots, research_snapshots.c.agent_run_id == research_run.id
            )
            if research_run
            else None
        )
        concept_set_id = cycle.artifacts.creative_concept_set_id or (
            await latest_id(concept_sets, concept_sets.c.agent_run_id == creative_run.id)
            if creative_run
            else None
        )
        approved_concept_id = cycle.artifacts.approved_concept_id
        if concept_set_id and not approved_concept_id:
            approved_concept_id = (
                await self.session.execute(
                    select(concept_decisions.c.concept_id)
                    .where(
                        concept_decisions.c.state == "APPROVED_FOR_PRODUCTION",
                        concept_decisions.c.concept_id.in_(
                            select(concepts.c.id).where(concepts.c.concept_set_id == concept_set_id)
                        ),
                    )
                    .order_by(concept_decisions.c.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        plan_id = cycle.artifacts.production_plan_id or (
            await latest_id(production_plans, production_plans.c.agent_run_id == producer_run.id)
            if producer_run
            else None
        )
        plan_approved = bool(
            plan_id
            and (
                await latest_id(
                    plan_decisions,
                    plan_decisions.c.production_plan_id == plan_id,
                    plan_decisions.c.state == "APPROVED_FOR_GENERATION",
                )
            )
        )
        job_statuses = (
            tuple(
                (
                    await self.session.execute(
                        select(generation_jobs.c.status).where(
                            generation_jobs.c.production_plan_id == plan_id
                        )
                    )
                ).scalars()
            )
            if plan_id
            else ()
        )
        final_id = cycle.artifacts.final_creative_id or (
            await latest_id(final_creatives, final_creatives.c.production_plan_id == plan_id)
            if plan_id
            else None
        )
        assembly_state = (
            (
                await self.session.execute(
                    select(assembly_jobs.c.status)
                    .join(
                        assembly_plans,
                        assembly_plans.c.id == assembly_jobs.c.assembly_plan_id,
                    )
                    .where(assembly_plans.c.production_plan_id == plan_id)
                    .order_by(assembly_jobs.c.updated_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if plan_id
            else None
        )
        manual_shot_ids = (
            set(
                (
                    await self.session.execute(
                        select(production_shots.c.id).where(
                            production_shots.c.production_plan_id == plan_id,
                            production_shots.c.source_strategy == "MANUAL_CAPTURE",
                        )
                    )
                ).scalars()
            )
            if plan_id
            else set()
        )
        bound_manual_shot_ids = (
            set(
                (
                    await self.session.execute(
                        select(manual_source_bindings.c.production_shot_id).where(
                            manual_source_bindings.c.production_shot_id.in_(manual_shot_ids)
                        )
                    )
                ).scalars()
            )
            if manual_shot_ids
            else set()
        )
        final_approved = bool(
            final_id
            and (
                await latest_id(
                    final_creative_decisions,
                    final_creative_decisions.c.final_creative_id == final_id,
                    final_creative_decisions.c.state == "APPROVED_FOR_PUBLISHING",
                )
            )
        )
        draft_id = cycle.artifacts.publication_draft_id or (
            await latest_id(publication_drafts, publication_drafts.c.final_creative_id == final_id)
            if final_id
            else None
        )
        pub_approved = bool(
            draft_id
            and (
                await latest_id(
                    publication_decisions,
                    publication_decisions.c.publication_draft_id == draft_id,
                    publication_decisions.c.state == "APPROVED",
                )
            )
        )
        publication_id = cycle.artifacts.publication_id or (
            await latest_id(publications, publications.c.publication_draft_id == draft_id)
            if draft_id
            else None
        )
        job_state = (
            (
                await self.session.execute(
                    select(publication_jobs.c.status)
                    .where(publication_jobs.c.publication_draft_id == draft_id)
                    .order_by(publication_jobs.c.updated_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if draft_id
            else None
        )
        performance_id = cycle.artifacts.performance_snapshot_id or (
            await latest_id(
                performance_snapshots, performance_snapshots.c.publication_id == publication_id
            )
            if publication_id
            else None
        )
        report_id = cycle.artifacts.intelligence_report_id or (
            await latest_id(reports, reports.c.agent_run_id == intelligence_run.id)
            if intelligence_run
            else None
        )
        proposal_id = cycle.artifacts.experiment_proposal_id or (
            await latest_id(experiment_proposals, experiment_proposals.c.report_id == report_id)
            if report_id
            else None
        )
        decided = bool(
            proposal_id
            and await latest_id(
                experiment_decisions, experiment_decisions.c.proposal_id == proposal_id
            )
        )
        current_snapshot = await latest_id(
            product_knowledge_snapshots,
            product_knowledge_snapshots.c.product_id == cycle.product_id,
        )
        pipeline = await self.observe_pipeline(
            PipelineLocator(
                cycle.product_id,
                research_run.id if research_run else cycle.artifacts.research_run_id,
                creative_run.id if creative_run else cycle.artifacts.creative_run_id,
                approved_concept_id,
                None,
                producer_run.id if producer_run else cycle.artifacts.producer_run_id,
                plan_id,
            )
        )
        return CanonicalCycleState(
            cycle,
            current_snapshot,
            research_run,
            research_id,
            creative_run,
            concept_set_id,
            approved_concept_id,
            producer_run,
            plan_id,
            plan_approved,
            bool(job_statuses and all(item == "SUCCEEDED" for item in job_statuses)),
            any(item == "FAILED" for item in job_statuses),
            any(item == "OUTCOME_UNKNOWN" for item in job_statuses),
            assembly_state == "FAILED",
            bool(manual_shot_ids - bound_manual_shot_ids),
            final_id,
            final_approved,
            draft_id,
            pub_approved,
            publication_id,
            job_state == "FAILED",
            job_state == "OUTCOME_UNKNOWN",
            performance_id,
            intelligence_run,
            report_id,
            proposal_id,
            decided,
            pipeline,
        )

    async def observe_pipeline(self, locator: PipelineLocator) -> PipelineObservation | None:
        """Project exact persisted authority into the pre-generation state machine.

        RLS scopes every table. Optional IDs bind a live session without turning the
        local checkpoint into authority; omitted IDs select the latest canonical row.
        """

        product_exists = await self.session.scalar(
            select(products.c.id).where(products.c.id == locator.product_id)
        )
        if product_exists is None:
            return None

        async def run(kind: str, identifier: UUID | None) -> AgentRun | None:
            if identifier is not None:
                value = await SqlAlchemyAgentRunRepository(self.session).get(identifier)
                if (
                    value is None
                    or value.product_id != locator.product_id
                    or value.agent_type != kind
                ):
                    return None
                return value
            if not locator.use_latest_when_unbound:
                return None
            run_id = await self.session.scalar(
                select(agent_runs.c.id)
                .where(
                    agent_runs.c.product_id == locator.product_id,
                    agent_runs.c.agent_type == kind,
                )
                .order_by(agent_runs.c.created_at.desc(), agent_runs.c.id.desc())
                .limit(1)
            )
            return (
                await SqlAlchemyAgentRunRepository(self.session).get(run_id)
                if run_id is not None
                else None
            )

        async def attempt(value: AgentRun) -> Mapping[str, Any] | None:
            row = (
                (
                    await self.session.execute(
                        select(model_attempts)
                        .where(model_attempts.c.agent_run_id == value.id)
                        .order_by(model_attempts.c.attempt_number.desc())
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
            return cast(Mapping[str, Any], row) if row else None

        research_run = await run("researcher", locator.research_run_id)
        if locator.research_run_id is not None and research_run is None:
            return None
        research_snapshot = None
        research_state = ResearchPipelineState.NOT_STARTED
        blocking_reason: str | None = None
        if research_run is not None:
            if research_run.status.value == "PENDING":
                research_state = ResearchPipelineState.PENDING
            elif research_run.status.value == "RUNNING":
                research_state = ResearchPipelineState.RUNNING
            elif research_run.status.value == "SUCCEEDED":
                research_snapshot = (
                    (
                        await self.session.execute(
                            select(research_snapshots)
                            .where(research_snapshots.c.agent_run_id == research_run.id)
                            .order_by(research_snapshots.c.created_at.desc())
                            .limit(1)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if research_snapshot is None:
                    research_state = ResearchPipelineState.FAILED_RESPONSE
                    blocking_reason = "RESEARCH_SUCCESS_MISSING_SNAPSHOT"
                else:
                    snapshot = await SqlAlchemyAgentRunRepository(self.session).get_snapshot(
                        research_snapshot["id"]
                    )
                    freshness = (
                        await SqlAlchemyAgentRunRepository(self.session).snapshot_freshness(
                            snapshot
                        )
                        if snapshot is not None
                        else "stale"
                    )
                    research_state = (
                        ResearchPipelineState.SUCCEEDED_CURRENT
                        if freshness == "current"
                        else ResearchPipelineState.SUCCEEDED_EXPIRED
                    )
            else:
                item = await attempt(research_run)
                category = classify_pipeline_failure(
                    research_run.operational_status,
                    str(item["status"]) if item is not None else None,
                )
                research_state = {
                    PipelineFailureCategory.BEFORE_PROVIDER: (
                        ResearchPipelineState.FAILED_BEFORE_PROVIDER
                    ),
                    PipelineFailureCategory.NO_RESPONSE: (ResearchPipelineState.FAILED_NO_RESPONSE),
                    PipelineFailureCategory.RESPONSE: ResearchPipelineState.FAILED_RESPONSE,
                    PipelineFailureCategory.OUTCOME_UNKNOWN: (
                        ResearchPipelineState.OUTCOME_UNKNOWN
                    ),
                }[category]

        observation = PipelineObservation(
            locator.product_id,
            research_state,
            research_run_id=research_run.id if research_run else None,
            research_snapshot_id=research_snapshot["id"] if research_snapshot else None,
            blocking_reason=blocking_reason,
        )
        if research_state is not ResearchPipelineState.SUCCEEDED_CURRENT:
            return observation
        assert research_snapshot is not None

        creative_run = await run("creative_strategist", locator.creative_run_id)
        if locator.creative_run_id is not None and creative_run is None:
            return None
        creative_state = CreativePipelineState.NOT_STARTED
        concept_id: UUID | None = None
        revalidation_id: UUID | None = None
        concept_set = None
        decision = None
        if creative_run is not None:
            if creative_run.status.value == "PENDING":
                creative_state = CreativePipelineState.PENDING
            elif creative_run.status.value == "RUNNING":
                creative_state = CreativePipelineState.RUNNING
            elif creative_run.status.value == "FAILED":
                item = await attempt(creative_run)
                creative_state = classify_creative_failure(
                    classify_pipeline_failure(
                        creative_run.operational_status,
                        str(item["status"]) if item is not None else None,
                    ),
                    str(item["provider_failure_reason"])
                    if item is not None and item.get("provider_failure_reason") is not None
                    else None,
                )
            elif creative_run.status.value == "SUCCEEDED":
                concept_set = (
                    (
                        await self.session.execute(
                            select(concept_sets)
                            .where(concept_sets.c.agent_run_id == creative_run.id)
                            .order_by(concept_sets.c.created_at.desc())
                            .limit(1)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if concept_set is None:
                    creative_state = CreativePipelineState.FAILED
                    blocking_reason = "CREATIVE_SUCCESS_MISSING_CONCEPT_SET"
                else:
                    concept_id = locator.concept_id
                    if concept_id is None:
                        concept_id = await self.session.scalar(
                            select(concept_decisions.c.concept_id)
                            .join(
                                concepts,
                                and_(
                                    concepts.c.id == concept_decisions.c.concept_id,
                                    concepts.c.tenant_id == concept_decisions.c.tenant_id,
                                ),
                            )
                            .where(
                                concepts.c.concept_set_id == concept_set["id"],
                                concept_decisions.c.state == "APPROVED_FOR_PRODUCTION",
                            )
                            .order_by(concept_decisions.c.created_at.desc())
                            .limit(1)
                        )
                    if concept_id is None:
                        creative_state = CreativePipelineState.SUCCEEDED
                    else:
                        belongs = await self.session.scalar(
                            select(concepts.c.id).where(
                                concepts.c.id == concept_id,
                                concepts.c.concept_set_id == concept_set["id"],
                            )
                        )
                        if belongs is None:
                            return None
                        decision = (
                            (
                                await self.session.execute(
                                    select(concept_decisions)
                                    .where(concept_decisions.c.concept_id == concept_id)
                                    .order_by(
                                        concept_decisions.c.created_at.desc(),
                                        concept_decisions.c.id.desc(),
                                    )
                                    .limit(1)
                                )
                            )
                            .mappings()
                            .one_or_none()
                        )
                        if decision is None or decision["state"] == "SHORTLISTED":
                            creative_state = CreativePipelineState.APPROVAL_REQUIRED
                        elif decision["state"] == "REJECTED":
                            creative_state = CreativePipelineState.REJECTED
                        elif concept_set["research_snapshot_id"] == research_snapshot["id"]:
                            creative_state = CreativePipelineState.APPROVED_FOR_PRODUCTION
                        else:
                            query = select(concept_revalidations).where(
                                concept_revalidations.c.concept_id == concept_id,
                                concept_revalidations.c.current_research_snapshot_id
                                == research_snapshot["id"],
                                concept_revalidations.c.original_decision_id == decision["id"],
                            )
                            if locator.creative_revalidation_id is not None:
                                query = query.where(
                                    concept_revalidations.c.id == locator.creative_revalidation_id
                                )
                            revalidation = (
                                (
                                    await self.session.execute(
                                        query.order_by(
                                            concept_revalidations.c.created_at.desc()
                                        ).limit(1)
                                    )
                                )
                                .mappings()
                                .one_or_none()
                            )
                            if revalidation is None:
                                creative_state = CreativePipelineState.STALE_RESEARCH
                            else:
                                revalidation_id = revalidation["id"]
                                creative_state = CreativePipelineState(revalidation["result"])
            else:
                creative_state = CreativePipelineState.FAILED
                blocking_reason = f"CREATIVE_RUN_{creative_run.status.value}"

        observation = PipelineObservation(
            locator.product_id,
            research_state,
            creative_state,
            research_run_id=research_run.id if research_run else None,
            research_snapshot_id=research_snapshot["id"] if research_snapshot else None,
            creative_run_id=creative_run.id if creative_run else None,
            concept_id=concept_id,
            creative_revalidation_id=revalidation_id,
            blocking_reason=blocking_reason,
        )
        if creative_state not in {
            CreativePipelineState.APPROVED_FOR_PRODUCTION,
            CreativePipelineState.REVALIDATED_FOR_PRODUCTION,
        }:
            return observation

        producer_run = await run("producer", locator.producer_run_id)
        if locator.producer_run_id is not None and producer_run is None:
            return None
        producer_state = ProducerPipelineState.NOT_STARTED
        plan_id: UUID | None = None
        producer_authority_current = True
        if producer_run is not None:
            selected_concept_digest = await self.session.scalar(
                select(concepts.c.semantic_digest).where(concepts.c.id == concept_id)
            )

            concept_ref = _run_input_ref(producer_run, "approved_concept")
            producer_matches_concept = (
                concept_ref is not None
                and concept_ref.get("id") == str(concept_id)
                and concept_ref.get("digest") == selected_concept_digest
            )
            if not producer_matches_concept:
                # An explicitly bound run with different lineage is an invalid locator.
                # A product-level lookup simply has no Producer for the selected Concept yet.
                if locator.producer_run_id is not None:
                    return None
                producer_run = None

        if producer_run is not None:
            research_ref = _run_input_ref(producer_run, "research_snapshot")
            product_ref = _run_input_ref(producer_run, "product_snapshot")
            revalidation_ref = _run_input_ref(producer_run, "creative_concept_revalidation")
            producer_authority_current = (
                research_ref is not None
                and research_ref.get("id") == str(research_snapshot["id"])
                and product_ref is not None
                and product_ref.get("id") == str(research_snapshot["product_snapshot_id"])
                and (
                    (
                        creative_state is CreativePipelineState.REVALIDATED_FOR_PRODUCTION
                        and revalidation_ref is not None
                        and revalidation_ref.get("id") == str(revalidation_id)
                    )
                    or (
                        creative_state is CreativePipelineState.APPROVED_FOR_PRODUCTION
                        and revalidation_ref is None
                    )
                )
            )
            if producer_run.status.value in {"PENDING", "RUNNING"}:
                producer_state = ProducerPipelineState.PENDING
                if producer_run.status.value == "RUNNING":
                    producer_state = ProducerPipelineState.RUNNING
                if not producer_authority_current:
                    blocking_reason = "PRODUCER_AUTHORITY_CHANGED_DURING_EXECUTION"
            elif producer_run.status.value == "FAILED":
                item = await attempt(producer_run)
                producer_state = classify_producer_failure(
                    classify_pipeline_failure(
                        producer_run.operational_status,
                        str(item["status"]) if item is not None else None,
                    ),
                    producer_run.failure_code,
                    producer_run.output_contract_version,
                    PRODUCTION_CONTRACT_VERSION,
                )
            elif producer_run.status.value == "SUCCEEDED":
                if not producer_authority_current:
                    producer_state = (
                        ProducerPipelineState.CONTRACT_UPGRADE_REQUIRED
                        if producer_run.output_contract_version < PRODUCTION_CONTRACT_VERSION
                        else ProducerPipelineState.PRODUCTION_PLAN_INVALID
                    )
                    blocking_reason = "PRODUCER_OUTPUT_USES_HISTORICAL_AUTHORITY"
                else:
                    plan_query = select(production_plans).where(
                        production_plans.c.agent_run_id == producer_run.id
                    )
                    if locator.production_plan_id is not None:
                        plan_query = plan_query.where(
                            production_plans.c.id == locator.production_plan_id
                        )
                    plan = (
                        (
                            await self.session.execute(
                                plan_query.order_by(production_plans.c.created_at.desc()).limit(1)
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if plan is None:
                        producer_state = ProducerPipelineState.SUCCEEDED
                        blocking_reason = "PRODUCER_SUCCESS_MISSING_PRODUCTION_PLAN"
                    else:
                        plan_id = plan["id"]
                        plan_decision = (
                            (
                                await self.session.execute(
                                    select(plan_decisions)
                                    .where(plan_decisions.c.production_plan_id == plan_id)
                                    .order_by(
                                        plan_decisions.c.created_at.desc(),
                                        plan_decisions.c.id.desc(),
                                    )
                                    .limit(1)
                                )
                            )
                            .mappings()
                            .one_or_none()
                        )
                        if plan_decision is None:
                            producer_state = ProducerPipelineState.PRODUCTION_PLAN_REVIEW_REQUIRED
                        elif plan_decision["state"] == "APPROVED_FOR_GENERATION":
                            producer_state = ProducerPipelineState.APPROVED_FOR_GENERATION
                        else:
                            producer_state = ProducerPipelineState.PRODUCTION_PLAN_REJECTED
            else:
                producer_state = ProducerPipelineState.FAILED_RESPONSE
                blocking_reason = f"PRODUCER_RUN_{producer_run.status.value}"

        return PipelineObservation(
            locator.product_id,
            research_state,
            creative_state,
            producer_state,
            research_run.id if research_run else None,
            research_snapshot["id"] if research_snapshot else None,
            creative_run.id if creative_run else None,
            concept_id,
            revalidation_id,
            producer_run.id if producer_run else None,
            plan_id,
            blocking_reason,
        )

    async def add_supervisor_manifest(self, value: SupervisorContextManifest) -> None:
        await self.session.execute(
            pg_insert(supervisor_context_manifests)
            .values(
                id=value.id,
                tenant_id=value.tenant_id,
                product_id=value.product_id,
                cycle_id=value.cycle_id,
                cycle_version=value.cycle_version,
                current_stage=value.current_stage.value,
                manifest={
                    "readiness": {
                        "state": value.readiness.state.value,
                        "requirements": [
                            {
                                "key": item.key,
                                "state": item.state.value,
                                "message": item.message,
                                "required": item.required,
                                "resource_ref": item.resource_ref,
                            }
                            for item in value.readiness.requirements
                        ],
                        "allowed_actions": [item.value for item in value.readiness.allowed_actions],
                    },
                    "product_snapshot_id": str(value.product_snapshot_id),
                    "product_snapshot_digest": value.product_snapshot_digest,
                    "artifacts": value.artifacts.as_dict(),
                    "pending_approvals": list(value.pending_approvals),
                    "provider_mode": value.provider_mode,
                },
                semantic_digest=value.semantic_digest,
                schema_version=value.schema_version,
                created_at=value.created_at,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )

    async def add_supervisor_report(self, value: SupervisorReport) -> None:
        await self.session.execute(
            insert(supervisor_reports).values(
                id=value.id,
                tenant_id=value.tenant_id,
                product_id=value.product_id,
                cycle_id=value.cycle_id,
                context_manifest_id=value.context_manifest_id,
                context_manifest_digest=value.context_manifest_digest,
                agent_run_id=value.agent_run_id,
                summary=value.summary,
                current_stage_explanation=value.current_stage_explanation,
                blockers=list(value.blockers),
                attention_items=list(value.attention_items),
                suggested_next_actions=[item.value for item in value.suggested_next_actions],
                completion_summary=value.completion_summary,
                semantic_digest=value.semantic_digest,
                schema_version=value.schema_version,
                created_at=value.created_at,
            )
        )

    async def latest_supervisor_report(self, cycle_id: UUID) -> SupervisorReport | None:
        row = (
            (
                await self.session.execute(
                    select(supervisor_reports)
                    .where(supervisor_reports.c.cycle_id == cycle_id)
                    .order_by(supervisor_reports.c.created_at.desc())
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        if not row:
            return None
        return SupervisorReport(
            row["tenant_id"],
            row["product_id"],
            row["cycle_id"],
            row["context_manifest_id"],
            row["context_manifest_digest"],
            row["summary"],
            row["current_stage_explanation"],
            tuple(row["blockers"]),
            tuple(row["attention_items"]),
            tuple(SupervisorAction(item) for item in row["suggested_next_actions"]),
            row["completion_summary"],
            row["agent_run_id"],
            row["schema_version"],
            row["id"],
            row["semantic_digest"],
            row["created_at"],
        )
