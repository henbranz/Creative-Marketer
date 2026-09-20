from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import TracebackType
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from creative_marketer.agent_runtime.application import AgentRunService
from creative_marketer.agent_runtime.domain import AgentRun
from creative_marketer.assembly.application import AssemblyPlanRecord, AssemblyReadinessResult
from creative_marketer.audit.application import AuditWriter
from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditOutcome
from creative_marketer.audit.safety import safe_metadata
from creative_marketer.catalog.application import CatalogService
from creative_marketer.creative.domain import ChannelIntent, CreativeStrategyRequest
from creative_marketer.events.application import OutboxWriter
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import tenant_event
from creative_marketer.identity.application.authentication import Actor, ActorKind, ExecutionContext
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.production.domain import ProductionPlanningRequest

from .domain import (
    CreativeCycle,
    CreativeCycleStep,
    CycleConflict,
    CycleNotFound,
    CycleNotReady,
    CyclePermissionDenied,
    CycleReadiness,
    CycleStage,
    CycleStatus,
    CycleTransition,
    ReadinessRequirement,
    ReadinessState,
    StepStatus,
    SupervisorAction,
    SupervisorContextManifest,
    SupervisorReport,
)


@dataclass(frozen=True, slots=True)
class CyclePreflight:
    product_exists: bool
    brief_completeness: int
    product_snapshot_id: UUID | None
    product_snapshot_digest: str | None
    evidence_count: int
    researcher_available: bool


@dataclass(frozen=True, slots=True)
class CanonicalCycleState:
    cycle: CreativeCycle
    current_product_snapshot_id: UUID | None = None
    research_run: AgentRun | None = None
    research_snapshot_id: UUID | None = None
    creative_run: AgentRun | None = None
    concept_set_id: UUID | None = None
    approved_concept_id: UUID | None = None
    producer_run: AgentRun | None = None
    production_plan_id: UUID | None = None
    production_plan_approved: bool = False
    generation_complete: bool = False
    generation_failed: bool = False
    generation_outcome_unknown: bool = False
    assembly_failed: bool = False
    assembly_input_required: bool = False
    final_creative_id: UUID | None = None
    final_creative_approved: bool = False
    publication_draft_id: UUID | None = None
    publication_approved: bool = False
    publication_id: UUID | None = None
    publication_failed: bool = False
    publication_outcome_unknown: bool = False
    performance_snapshot_id: UUID | None = None
    intelligence_run: AgentRun | None = None
    intelligence_report_id: UUID | None = None
    experiment_proposal_id: UUID | None = None
    experiment_decided: bool = False


@dataclass(frozen=True, slots=True)
class ExperimentHandoff:
    proposal_id: UUID
    product_id: UUID
    proposal_digest: str
    decision: str | None
    decision_proposal_digest: str | None


class OrchestrationRepository(Protocol):
    async def preflight(self, product_id: UUID) -> CyclePreflight: ...
    async def active_for_product(
        self, product_id: UUID, *, for_update: bool = False
    ) -> CreativeCycle | None: ...
    async def parent_for_experiment(self, proposal_id: UUID) -> CreativeCycle | None: ...
    async def child_for_experiment(self, proposal_id: UUID) -> CreativeCycle | None: ...
    async def experiment_handoff(self, proposal_id: UUID) -> ExperimentHandoff | None: ...
    async def get(self, cycle_id: UUID, *, for_update: bool = False) -> CreativeCycle | None: ...
    async def add(self, cycle: CreativeCycle) -> None: ...
    async def update(self, cycle: CreativeCycle, *, expected_version: int) -> bool: ...
    async def add_transition(self, value: CycleTransition) -> None: ...
    async def add_step(self, value: CreativeCycleStep) -> None: ...
    async def step(self, cycle_id: UUID, step_key: str) -> CreativeCycleStep | None: ...
    async def steps(self, cycle_id: UUID) -> tuple[CreativeCycleStep, ...]: ...
    async def transitions(self, cycle_id: UUID) -> tuple[CycleTransition, ...]: ...
    async def canonical_state(self, cycle: CreativeCycle) -> CanonicalCycleState: ...
    async def add_supervisor_manifest(self, value: SupervisorContextManifest) -> None: ...
    async def add_supervisor_report(self, value: SupervisorReport) -> None: ...
    async def latest_supervisor_report(self, cycle_id: UUID) -> SupervisorReport | None: ...


class OrchestrationUnitOfWork(Protocol):
    cycles: OrchestrationRepository
    audit: AuditWriter
    outbox: OutboxWriter

    async def __aenter__(self) -> OrchestrationUnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class OrchestrationUnitOfWorkFactory(Protocol):
    def __call__(self, tenant_id: UUID) -> OrchestrationUnitOfWork: ...


class AssemblyCoordinator(Protocol):
    async def readiness(
        self, context: ExecutionContext, production_plan_id: UUID
    ) -> AssemblyReadinessResult: ...

    async def create_plan(
        self, context: ExecutionContext, production_plan_id: UUID
    ) -> AssemblyPlanRecord: ...


class CycleWorkflowCoordinator(Protocol):
    async def start_cycle(self, tenant_id: UUID, cycle_id: UUID, correlation_id: UUID) -> None: ...

    async def wake_cycle(self, tenant_id: UUID, cycle_id: UUID) -> None: ...


class PublicationWorkflowCoordinator(Protocol):
    async def start_publication(
        self, tenant_id: UUID, draft_id: UUID, correlation_id: UUID
    ) -> None: ...


class MeasurementWorkflowCoordinator(Protocol):
    async def start_measurement(
        self, tenant_id: UUID, publication_id: UUID, correlation_id: UUID
    ) -> None: ...


def require_cycle_mutation(context: ExecutionContext) -> None:
    if context.membership_status is not MembershipStatus.ACTIVE or context.membership_role not in {
        MembershipRole.OWNER,
        MembershipRole.ADMIN,
    }:
        raise CyclePermissionDenied("only an active owner or admin may control a Creative Cycle")


class CycleReadinessEngine:
    """Pure, deterministic user-facing readiness. It never authorizes an action."""

    def start(self, value: CyclePreflight) -> CycleReadiness:
        requirements = (
            ReadinessRequirement(
                "product_brief",
                ReadinessState.READY if value.brief_completeness >= 80 else ReadinessState.BLOCKED,
                (
                    f"Product Brief is {value.brief_completeness}% complete."
                    if value.brief_completeness >= 80
                    else (
                        "Complete the Product Brief to at least 80% "
                        f"(currently {value.brief_completeness}%)."
                    )
                ),
            ),
            ReadinessRequirement(
                "research_evidence",
                ReadinessState.READY if value.evidence_count else ReadinessState.BLOCKED,
                (
                    f"{value.evidence_count} research evidence snapshot(s) are available."
                    if value.evidence_count
                    else "Add at least one research source with captured evidence."
                ),
            ),
            ReadinessRequirement(
                "researcher",
                ReadinessState.READY if value.researcher_available else ReadinessState.BLOCKED,
                "Researcher is ready."
                if value.researcher_available
                else "Researcher is unavailable.",
            ),
            ReadinessRequirement(
                "product_snapshot",
                ReadinessState.READY if value.product_snapshot_id else ReadinessState.WAITING,
                (
                    "A current immutable Product snapshot is ready."
                    if value.product_snapshot_id
                    else "A Product snapshot will be prepared when the cycle starts."
                ),
                required=False,
            ),
        )
        blocked = any(
            item.required and item.state is ReadinessState.BLOCKED for item in requirements
        )
        actions: tuple[SupervisorAction, ...] = ()
        if value.brief_completeness < 80:
            actions += (SupervisorAction.COMPLETE_BRIEF,)
        if not value.evidence_count:
            actions += (SupervisorAction.ADD_RESEARCH_SOURCE,)
        return CycleReadiness(
            ReadinessState.BLOCKED if blocked else ReadinessState.READY,
            requirements,
            actions,
        )

    def cycle(self, value: CanonicalCycleState) -> CycleReadiness:
        stage = value.cycle.current_stage
        requirements: list[ReadinessRequirement] = []
        actions: tuple[SupervisorAction, ...] = ()
        if stage is CycleStage.AWAITING_CONCEPT_APPROVAL:
            requirements.append(
                ReadinessRequirement(
                    "concept_approval", ReadinessState.WAITING, "Choose a concept for production."
                )
            )
            actions = (SupervisorAction.APPROVE_CONCEPT,)
        elif stage is CycleStage.AWAITING_PRODUCTION_APPROVAL:
            requirements.append(
                ReadinessRequirement(
                    "production_approval",
                    ReadinessState.WAITING,
                    "Review the plan, generation rights, and estimated cost before approval.",
                )
            )
            actions = (SupervisorAction.APPROVE_PRODUCTION_PLAN,)
        elif stage is CycleStage.ASSEMBLING_FINAL_CREATIVE and value.assembly_input_required:
            requirements.append(
                ReadinessRequirement(
                    "assembly_input",
                    ReadinessState.WAITING,
                    "Bind an approved source Asset to every manual-capture shot.",
                )
            )
            actions = (SupervisorAction.PREPARE_FINAL_ASSEMBLY,)
        elif stage is CycleStage.AWAITING_FINAL_CREATIVE_APPROVAL:
            requirements.append(
                ReadinessRequirement(
                    "final_creative_approval",
                    ReadinessState.WAITING,
                    "Review and approve the Final Creative before preparing publication.",
                )
            )
            actions = (SupervisorAction.REVIEW_FINAL_CREATIVE,)
        elif stage is CycleStage.AWAITING_PUBLICATION_INPUT:
            requirements.append(
                ReadinessRequirement(
                    "publication_input",
                    ReadinessState.WAITING,
                    "Prepare the destination, caption, settings, and schedule.",
                )
            )
            actions = (SupervisorAction.PREPARE_PUBLICATION,)
        elif stage is CycleStage.AWAITING_PUBLICATION_APPROVAL:
            requirements.append(
                ReadinessRequirement(
                    "publication_approval",
                    ReadinessState.WAITING,
                    "Approve this exact R4 publication action.",
                )
            )
            actions = (SupervisorAction.APPROVE_PUBLICATION,)
        elif stage is CycleStage.MEASURING:
            requirements.append(
                ReadinessRequirement(
                    "performance_snapshot",
                    ReadinessState.WAITING,
                    "Waiting for the next governed measurement checkpoint.",
                )
            )
            actions = (SupervisorAction.WAIT_FOR_MEASUREMENT,)
        elif stage is CycleStage.AWAITING_EXPERIMENT_DECISION:
            requirements.append(
                ReadinessRequirement(
                    "experiment_decision",
                    ReadinessState.WAITING,
                    "Review the proposed next experiment. It will not start automatically.",
                )
            )
            actions = (SupervisorAction.REVIEW_EXPERIMENT,)
        elif value.cycle.status is CycleStatus.NEEDS_RECOVERY:
            requirements.append(
                ReadinessRequirement(
                    "operator_recovery",
                    ReadinessState.BLOCKED,
                    "An ambiguous Agent or provider outcome needs explicit operator recovery.",
                )
            )
            actions = (SupervisorAction.RECOVER_AGENT_RUN,)
        else:
            requirements.append(
                ReadinessRequirement(
                    "orchestrator",
                    ReadinessState.READY,
                    "Creative Manager can safely continue this cycle.",
                )
            )
        state = (
            ReadinessState.COMPLETED
            if value.cycle.status is CycleStatus.COMPLETED
            else ReadinessState.BLOCKED
            if value.cycle.status
            in {CycleStatus.BLOCKED, CycleStatus.FAILED, CycleStatus.NEEDS_RECOVERY}
            else ReadinessState.WAITING
            if actions
            else ReadinessState.READY
        )
        return CycleReadiness(state, tuple(requirements), actions)


@dataclass(slots=True)
class CreativeCycleService:
    uow_factory: OrchestrationUnitOfWorkFactory
    catalog: CatalogService
    agent_runtime: AgentRunService
    assembly: AssemblyCoordinator | None = None
    cycle_workflows: CycleWorkflowCoordinator | None = None
    publication_workflows: PublicationWorkflowCoordinator | None = None
    measurement_workflows: MeasurementWorkflowCoordinator | None = None
    readiness_engine: CycleReadinessEngine = field(default_factory=CycleReadinessEngine)

    async def preflight(self, context: ExecutionContext, product_id: UUID) -> CycleReadiness:
        async with self.uow_factory(context.tenant_id) as uow:
            return self.readiness_engine.start(await uow.cycles.preflight(product_id))

    async def start(
        self,
        context: ExecutionContext,
        product_id: UUID,
        *,
        parent_cycle_id: UUID | None = None,
        source_experiment_proposal_id: UUID | None = None,
    ) -> CreativeCycle:
        require_cycle_mutation(context)
        if (parent_cycle_id is None) != (source_experiment_proposal_id is None):
            raise CycleNotReady(
                "parent cycle and source ExperimentProposal must be supplied together"
            )
        if source_experiment_proposal_id is not None:
            assert parent_cycle_id is not None
            async with self.uow_factory(context.tenant_id) as uow:
                existing = await uow.cycles.child_for_experiment(source_experiment_proposal_id)
                if existing is not None:
                    if (
                        existing.product_id != product_id
                        or existing.parent_cycle_id != parent_cycle_id
                    ):
                        raise CycleConflict(
                            "ExperimentProposal is already bound to a different child cycle"
                        )
                    return existing
                parent = await uow.cycles.get(parent_cycle_id, for_update=True)
                handoff = await uow.cycles.experiment_handoff(source_experiment_proposal_id)
                if parent is None or handoff is None:
                    raise CycleNotReady("approved experiment lineage is unavailable")
                if (
                    parent.product_id != product_id
                    or handoff.product_id != product_id
                    or parent.status is not CycleStatus.COMPLETED
                    or parent.current_stage is not CycleStage.COMPLETED
                    or parent.artifacts.experiment_proposal_id != source_experiment_proposal_id
                    or handoff.decision != "APPROVED_FOR_CREATIVE"
                    or handoff.decision_proposal_digest != handoff.proposal_digest
                ):
                    raise CycleNotReady(
                        "exact approved ExperimentProposal from a completed "
                        "parent cycle is required"
                    )
        preflight = await self.preflight(context, product_id)
        if preflight.state is not ReadinessState.READY:
            raise CycleNotReady("Creative Cycle prerequisites are incomplete")
        workspace = await self.catalog.get_workspace(context, product_id)
        snapshot = workspace.latest_snapshot
        if snapshot is None or snapshot.source_revision != workspace.brief.revision:
            snapshot = await self.catalog.create_snapshot(context, product_id)
        cycle = CreativeCycle(
            context.tenant_id,
            product_id,
            context.user_id,
            snapshot.id,
            snapshot.digest,
            parent_cycle_id=parent_cycle_id,
            source_experiment_proposal_id=source_experiment_proposal_id,
        )
        async with self.uow_factory(context.tenant_id) as uow:
            if await uow.cycles.active_for_product(product_id, for_update=True):
                raise CycleConflict("this Product already has an active Creative Cycle")
            await uow.cycles.add(cycle)
            await uow.cycles.add_transition(
                CycleTransition(
                    context.tenant_id,
                    cycle.id,
                    None,
                    cycle.current_stage,
                    cycle.status,
                    "CYCLE_STARTED",
                    context.actor.kind.value,
                    context.actor.id,
                    context.correlation_id,
                )
            )
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="orchestration.cycle.started",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="creative_cycle",
                    resource_id=str(cycle.id),
                    metadata=safe_metadata(
                        {
                            "product_id": str(product_id),
                            "state_machine_version": cycle.state_machine_version,
                            "parent_cycle_id": str(parent_cycle_id) if parent_cycle_id else None,
                            "source_experiment_proposal_id": str(source_experiment_proposal_id)
                            if source_experiment_proposal_id
                            else None,
                        }
                    ),
                )
            )
            await self._event(
                uow, context, cycle, "orchestration.cycle.created.v1", "CYCLE_STARTED"
            )
            await uow.commit()
        if self.cycle_workflows is not None:
            await self.cycle_workflows.start_cycle(
                context.tenant_id, cycle.id, context.correlation_id
            )
        return cycle

    async def start_next_from_experiment(
        self, context: ExecutionContext, proposal_id: UUID
    ) -> CreativeCycle:
        """Start, or replay, the one explicit child cycle for an approved experiment."""

        require_cycle_mutation(context)
        async with self.uow_factory(context.tenant_id) as uow:
            existing = await uow.cycles.child_for_experiment(proposal_id)
            if existing is not None:
                return existing
            parent = await uow.cycles.parent_for_experiment(proposal_id)
            if parent is None:
                raise CycleNotReady(
                    "a completed Creative Cycle bound to this ExperimentProposal is required"
                )
        return await self.start(
            context,
            parent.product_id,
            parent_cycle_id=parent.id,
            source_experiment_proposal_id=proposal_id,
        )

    async def get(
        self, context: ExecutionContext, cycle_id: UUID
    ) -> tuple[
        CanonicalCycleState,
        CycleReadiness,
        tuple[CreativeCycleStep, ...],
        tuple[CycleTransition, ...],
        SupervisorReport | None,
    ]:
        async with self.uow_factory(context.tenant_id) as uow:
            cycle = await uow.cycles.get(cycle_id)
            if cycle is None:
                raise CycleNotFound("Creative Cycle not found")
            state = await uow.cycles.canonical_state(cycle)
            return (
                state,
                self.readiness_engine.cycle(state),
                await uow.cycles.steps(cycle_id),
                await uow.cycles.transitions(cycle_id),
                await uow.cycles.latest_supervisor_report(cycle_id),
            )

    async def active(self, context: ExecutionContext, product_id: UUID) -> CreativeCycle | None:
        async with self.uow_factory(context.tenant_id) as uow:
            return await uow.cycles.active_for_product(product_id)

    async def reconcile(self, context: ExecutionContext, cycle_id: UUID) -> CreativeCycle:
        require_cycle_mutation(context)
        async with self.uow_factory(context.tenant_id) as uow:
            cycle = await uow.cycles.get(cycle_id, for_update=True)
            if cycle is None:
                raise CycleNotFound("Creative Cycle not found")
            state = await uow.cycles.canonical_state(cycle)
        if cycle.status in {CycleStatus.CANCELLED, CycleStatus.COMPLETED, CycleStatus.FAILED}:
            return cycle
        target, action = self._next(state)
        if action is not None:
            return await self._start_action(context, state, action)
        if target is None:
            return cycle
        return await self._transition(context, cycle, target, state)

    def _next(self, state: CanonicalCycleState) -> tuple[CycleStage | None, str | None]:
        stage = state.cycle.current_stage
        if stage is CycleStage.CREATED:
            return CycleStage.CHECKING_READINESS, None
        if stage in {CycleStage.CHECKING_READINESS, CycleStage.BLOCKED}:
            return CycleStage.RESEARCHING, None
        if stage is CycleStage.RESEARCHING:
            if state.research_snapshot_id:
                return CycleStage.CREATIVE_STRATEGY, None
            if state.research_run is None:
                return None, "research"
            if state.research_run.status.value == "FAILED":
                return CycleStage.FAILED, None
        if stage is CycleStage.CREATIVE_STRATEGY:
            if state.concept_set_id:
                return CycleStage.AWAITING_CONCEPT_APPROVAL, None
            if state.creative_run is None:
                return None, "creative"
            if state.creative_run.status.value == "FAILED":
                return CycleStage.FAILED, None
        if stage is CycleStage.AWAITING_CONCEPT_APPROVAL and state.approved_concept_id:
            return CycleStage.PRODUCTION_PLANNING, None
        if stage is CycleStage.PRODUCTION_PLANNING:
            if state.production_plan_id:
                return CycleStage.AWAITING_PRODUCTION_APPROVAL, None
            if state.producer_run is None and state.approved_concept_id:
                return None, "producer"
            if state.producer_run and state.producer_run.status.value == "FAILED":
                return CycleStage.FAILED, None
        if stage is CycleStage.AWAITING_PRODUCTION_APPROVAL and state.production_plan_approved:
            return CycleStage.GENERATING_MEDIA, None
        if stage is CycleStage.GENERATING_MEDIA:
            if state.generation_outcome_unknown:
                return None, "needs_recovery"
            if state.generation_failed:
                return CycleStage.FAILED, None
            if state.generation_complete:
                return CycleStage.ASSEMBLING_FINAL_CREATIVE, None
        if stage is CycleStage.ASSEMBLING_FINAL_CREATIVE:
            if state.final_creative_id:
                return CycleStage.AWAITING_FINAL_CREATIVE_APPROVAL, None
            if state.assembly_failed:
                return CycleStage.FAILED, None
            return None, "assembly"
        if stage is CycleStage.AWAITING_FINAL_CREATIVE_APPROVAL and state.final_creative_approved:
            return CycleStage.AWAITING_PUBLICATION_INPUT, None
        if stage is CycleStage.AWAITING_PUBLICATION_INPUT and state.publication_draft_id:
            return CycleStage.AWAITING_PUBLICATION_APPROVAL, None
        if stage is CycleStage.AWAITING_PUBLICATION_APPROVAL and state.publication_approved:
            return CycleStage.PUBLISHING, None
        if stage is CycleStage.PUBLISHING:
            if state.publication_outcome_unknown:
                return None, "needs_recovery"
            if state.publication_failed:
                return CycleStage.FAILED, None
            if state.publication_id:
                return CycleStage.MEASURING, None
            if state.publication_approved:
                return None, "publishing"
        if stage is CycleStage.MEASURING:
            if state.performance_snapshot_id:
                return CycleStage.INTELLIGENCE, None
            if state.publication_id:
                return None, "measurement"
        if stage is CycleStage.INTELLIGENCE:
            if state.intelligence_report_id:
                if state.experiment_proposal_id:
                    return CycleStage.AWAITING_EXPERIMENT_DECISION, None
                return CycleStage.COMPLETED, None
            if state.intelligence_run is None:
                return None, "intelligence"
            if state.intelligence_run.status.value == "FAILED":
                return CycleStage.FAILED, None
        if stage is CycleStage.AWAITING_EXPERIMENT_DECISION and state.experiment_decided:
            return CycleStage.COMPLETED, None
        return None, None

    async def _start_action(
        self, context: ExecutionContext, state: CanonicalCycleState, action: str
    ) -> CreativeCycle:
        cycle = state.cycle
        if action == "needs_recovery":
            async with self.uow_factory(context.tenant_id) as uow:
                current = await uow.cycles.get(cycle.id, for_update=True)
                assert current is not None
                changed = replace(
                    current,
                    status=CycleStatus.NEEDS_RECOVERY,
                    failure_code="AMBIGUOUS_EXTERNAL_OUTCOME",
                    cycle_version=current.cycle_version + 1,
                )
                if not await uow.cycles.update(changed, expected_version=current.cycle_version):
                    raise CycleConflict("cycle changed during reconciliation")
                await uow.commit()
                return changed
        step_key = action
        idempotency_key = f"cycle:{cycle.id}:{step_key}:v1"
        async with self.uow_factory(context.tenant_id) as uow:
            existing = await uow.cycles.step(cycle.id, step_key)
        if existing is not None:
            return cycle
        if action == "assembly" and state.production_plan_id and self.assembly is not None:
            readiness = await self.assembly.readiness(context, state.production_plan_id)
            if not readiness.ready:
                return cycle
            record = await self.assembly.create_plan(context, state.production_plan_id)
            plan = record.plan
            job = record.job
            return await self._record_workflow_step(
                context,
                cycle,
                step_key,
                idempotency_key,
                workflow_ref=f"assembly-job://{job.id}",
                artifact_ref=f"assembly-plan://{plan.id}",
            )
        if (
            action == "publishing"
            and state.publication_draft_id
            and self.publication_workflows is not None
        ):
            await self.publication_workflows.start_publication(
                context.tenant_id, state.publication_draft_id, context.correlation_id
            )
            return await self._record_workflow_step(
                context,
                cycle,
                step_key,
                idempotency_key,
                workflow_ref=f"publication-draft://{state.publication_draft_id}",
            )
        if (
            action == "measurement"
            and state.publication_id
            and self.measurement_workflows is not None
        ):
            await self.measurement_workflows.start_measurement(
                context.tenant_id, state.publication_id, context.correlation_id
            )
            return await self._record_workflow_step(
                context,
                cycle,
                step_key,
                idempotency_key,
                workflow_ref=f"publication-measurement://{state.publication_id}",
            )
        # Reconciliation is performed by the durable workload, but every billed
        # AgentRun is delegated from the human who explicitly started the cycle.
        # Preserve the user as authority provenance; AgentRuntime records the
        # workload that later claims and executes the run independently.
        agent_context = replace(context, actor=Actor(ActorKind.USER, context.user_id))
        run: AgentRun
        if action == "research":
            run = await self.agent_runtime.request_researcher(
                agent_context, product_id=cycle.product_id, idempotency_key=idempotency_key
            )
            artifacts = replace(cycle.artifacts, research_run_id=run.id)
        elif action == "creative":
            run = await self.agent_runtime.request_creative_strategist(
                agent_context,
                product_id=cycle.product_id,
                request=CreativeStrategyRequest(5, ChannelIntent.ORGANIC_SHORT_FORM),
                idempotency_key=idempotency_key,
                approved_experiment_proposal_id=cycle.source_experiment_proposal_id,
            )
            artifacts = replace(cycle.artifacts, creative_run_id=run.id)
        elif action == "producer" and state.approved_concept_id:
            run = await self.agent_runtime.request_producer(
                agent_context,
                concept_id=state.approved_concept_id,
                request=ProductionPlanningRequest(),
                idempotency_key=idempotency_key,
            )
            artifacts = replace(cycle.artifacts, producer_run_id=run.id)
        elif action == "intelligence":
            run = await self.agent_runtime.request_intelligence(
                agent_context, product_id=cycle.product_id, idempotency_key=idempotency_key
            )
            artifacts = replace(cycle.artifacts, intelligence_run_id=run.id)
        else:
            return cycle
        if run.idempotency_key != idempotency_key:
            # AgentRuntime correctly de-duplicates against an already-active run.
            # A cycle must never claim that unrelated run as its own provenance.
            return await self._start_action(context, state, "needs_recovery")
        async with self.uow_factory(context.tenant_id) as uow:
            current = await uow.cycles.get(cycle.id, for_update=True)
            if current is None:
                raise CycleNotFound("Creative Cycle not found")
            updated = replace(current, artifacts=artifacts, cycle_version=current.cycle_version + 1)
            if not await uow.cycles.update(updated, expected_version=current.cycle_version):
                raise CycleConflict("cycle changed during reconciliation")
            await uow.cycles.add_step(
                CreativeCycleStep(
                    context.tenant_id,
                    cycle.id,
                    step_key,
                    1,
                    StepStatus.STARTED,
                    idempotency_key,
                    agent_run_id=run.id,
                )
            )
            await uow.commit()
            return updated

    async def _record_workflow_step(
        self,
        context: ExecutionContext,
        cycle: CreativeCycle,
        step_key: str,
        idempotency_key: str,
        *,
        workflow_ref: str,
        artifact_ref: str | None = None,
    ) -> CreativeCycle:
        async with self.uow_factory(context.tenant_id) as uow:
            current = await uow.cycles.get(cycle.id, for_update=True)
            if current is None:
                raise CycleNotFound("Creative Cycle not found")
            existing = await uow.cycles.step(cycle.id, step_key)
            if existing is not None:
                return current
            await uow.cycles.add_step(
                CreativeCycleStep(
                    context.tenant_id,
                    cycle.id,
                    step_key,
                    1,
                    StepStatus.STARTED,
                    idempotency_key,
                    workflow_ref=workflow_ref,
                    artifact_ref=artifact_ref,
                )
            )
            await uow.commit()
            return current

    async def _transition(
        self,
        context: ExecutionContext,
        cycle: CreativeCycle,
        target: CycleStage,
        state: CanonicalCycleState,
    ) -> CreativeCycle:
        artifacts = replace(
            cycle.artifacts,
            research_snapshot_id=state.research_snapshot_id or cycle.artifacts.research_snapshot_id,
            creative_concept_set_id=state.concept_set_id or cycle.artifacts.creative_concept_set_id,
            approved_concept_id=state.approved_concept_id or cycle.artifacts.approved_concept_id,
            production_plan_id=state.production_plan_id or cycle.artifacts.production_plan_id,
            final_creative_id=state.final_creative_id or cycle.artifacts.final_creative_id,
            publication_draft_id=state.publication_draft_id or cycle.artifacts.publication_draft_id,
            publication_id=state.publication_id or cycle.artifacts.publication_id,
            performance_snapshot_id=state.performance_snapshot_id
            or cycle.artifacts.performance_snapshot_id,
            intelligence_report_id=state.intelligence_report_id
            or cycle.artifacts.intelligence_report_id,
            experiment_proposal_id=state.experiment_proposal_id
            or cycle.artifacts.experiment_proposal_id,
        )
        reason = f"ADVANCED_TO_{target.value}"
        async with self.uow_factory(context.tenant_id) as uow:
            current = await uow.cycles.get(cycle.id, for_update=True)
            if current is None:
                raise CycleNotFound("Creative Cycle not found")
            changed = current.transition(
                target,
                artifacts=artifacts,
                failure_code="DOWNSTREAM_FAILED" if target is CycleStage.FAILED else None,
            )
            if not await uow.cycles.update(changed, expected_version=current.cycle_version):
                raise CycleConflict("cycle changed during reconciliation")
            await uow.cycles.add_transition(
                CycleTransition(
                    context.tenant_id,
                    cycle.id,
                    current.current_stage,
                    target,
                    changed.status,
                    reason,
                    context.actor.kind.value,
                    context.actor.id,
                    context.correlation_id,
                )
            )
            event_type = (
                "orchestration.cycle.completed.v1"
                if target is CycleStage.COMPLETED
                else "orchestration.cycle.stage_changed.v1"
            )
            await self._event(uow, context, changed, event_type, reason)
            await uow.commit()
            return changed

    async def cancel(self, context: ExecutionContext, cycle_id: UUID) -> CreativeCycle:
        require_cycle_mutation(context)
        async with self.uow_factory(context.tenant_id) as uow:
            cycle = await uow.cycles.get(cycle_id, for_update=True)
            if cycle is None:
                raise CycleNotFound("Creative Cycle not found")
            if cycle.status is CycleStatus.CANCELLED:
                return cycle
            changed = cycle.transition(CycleStage.CANCELLED)
            if not await uow.cycles.update(changed, expected_version=cycle.cycle_version):
                raise CycleConflict("cycle changed during cancellation")
            await uow.cycles.add_transition(
                CycleTransition(
                    context.tenant_id,
                    cycle.id,
                    cycle.current_stage,
                    CycleStage.CANCELLED,
                    CycleStatus.CANCELLED,
                    "USER_CANCELLED",
                    context.actor.kind.value,
                    context.actor.id,
                    context.correlation_id,
                )
            )
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="orchestration.cycle.cancelled",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="creative_cycle",
                    resource_id=str(cycle.id),
                    metadata=safe_metadata(
                        {"stage": cycle.current_stage.value, "external_effects_reversed": False}
                    ),
                )
            )
            await uow.commit()
            return changed

    async def create_supervisor_report(self, context: ExecutionContext, cycle_id: UUID) -> AgentRun:
        require_cycle_mutation(context)
        state, readiness, _, _, _ = await self.get(context, cycle_id)
        manifest = SupervisorContextManifest(
            context.tenant_id,
            state.cycle.product_id,
            cycle_id,
            state.cycle.cycle_version,
            state.cycle.current_stage,
            readiness,
            state.cycle.product_snapshot_id,
            state.cycle.product_snapshot_digest,
            state.cycle.artifacts,
            tuple(
                item.key for item in readiness.requirements if item.state is ReadinessState.WAITING
            ),
            id=uuid5(
                NAMESPACE_URL,
                f"creative-marketer:cycle:{cycle_id}:supervisor:{state.cycle.cycle_version}",
            ),
        )
        async with self.uow_factory(context.tenant_id) as uow:
            await uow.cycles.add_supervisor_manifest(manifest)
            await uow.commit()
        return await self.agent_runtime.request_supervisor(
            context,
            context_manifest_id=manifest.id,
            idempotency_key=(f"cycle:{cycle_id}:supervisor:{state.cycle.cycle_version}:v1"),
        )

    async def _event(
        self,
        uow: OrchestrationUnitOfWork,
        context: ExecutionContext,
        cycle: CreativeCycle,
        event_type: str,
        reason_code: str,
    ) -> None:
        contracts = EventContractRegistry()
        await uow.outbox.append(
            tenant_event(
                context,
                event_type=event_type,
                schema_version=1,
                aggregate_type="creative_cycle",
                aggregate_id=cycle.id,
                payload={
                    "cycle_id": str(cycle.id),
                    "product_id": str(cycle.product_id),
                    "status": cycle.status.value,
                    "stage": cycle.current_stage.value,
                    "cycle_version": cycle.cycle_version,
                    "reason_code": reason_code,
                },
                payload_schema_digest=contracts.schema_digest(event_type),
                occurred_at=cycle.updated_at,
            )
        )
