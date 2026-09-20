from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, insert, select, update
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

from .orchestration_schema import (
    creative_cycles,
    cycle_steps,
    cycle_transitions,
    supervisor_context_manifests,
    supervisor_reports,
)


def _uuid(value: object) -> UUID | None:
    return UUID(str(value)) if value else None


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
