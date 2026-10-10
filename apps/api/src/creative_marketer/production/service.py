from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from types import TracebackType
from typing import Protocol
from uuid import UUID

from creative_marketer.agent_runtime.domain import canonical_digest
from creative_marketer.audit.application import AuditWriter
from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditOutcome
from creative_marketer.audit.safety import safe_metadata
from creative_marketer.events.application import OutboxWriter
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import tenant_event
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.identity.domain import MembershipRole, MembershipStatus

from .application import ImageReservationPricing, MediaRoute, MediaRouter, SeedancePricing
from .domain import (
    FrozenAssetReference,
    GenerationJob,
    GenerationJobStatus,
    InvalidProductionPlan,
    MediaKind,
    MediaSpendRequirement,
    ProductionDecisionConflict,
    ProductionNotFound,
    ProductionPermissionDenied,
    ProductionPlan,
    ProductionPlanDecision,
    ProductionPlanDecisionState,
    ProductionPlanningRequest,
    ProductionPricingChanged,
    ProductionRejectionFeedbackRequired,
)


@dataclass(frozen=True, slots=True)
class ProductionPlanRecord:
    plan: ProductionPlan
    decision: ProductionPlanDecision | None
    planning_cost: Decimal


class ProductionRepository(Protocol):
    async def get_plan(
        self, plan_id: UUID, *, for_update: bool = False
    ) -> ProductionPlanRecord | None: ...

    async def list_plans(self, product_id: UUID) -> tuple[ProductionPlanRecord, ...]: ...
    async def add_decision(self, value: ProductionPlanDecision) -> None: ...
    async def add_jobs(self, values: tuple[GenerationJob, ...]) -> None: ...
    async def list_jobs(self, plan_id: UUID) -> tuple[GenerationJob, ...]: ...
    async def get_job(self, job_id: UUID) -> GenerationJob | None: ...
    async def spend_requirement(
        self, plan_id: UUID, configured_cap: Decimal
    ) -> MediaSpendRequirement: ...
    async def requeue_spend_cap_jobs(self, plan_id: UUID) -> tuple[GenerationJob, ...]: ...
    async def validate_spend_cap_retry(self, plan: ProductionPlan) -> None: ...
    async def recover_stranded_starts(
        self, plan_id: UUID, job_ids: tuple[UUID, ...]
    ) -> tuple[GenerationJob, ...]: ...
    async def retry_unknown_media_jobs(
        self, plan_id: UUID, job_ids: tuple[UUID, ...]
    ) -> tuple[GenerationJob, ...]: ...


class ProductionUnitOfWork(Protocol):
    production: ProductionRepository
    audit: AuditWriter
    outbox: OutboxWriter

    async def __aenter__(self) -> ProductionUnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class ProductionUnitOfWorkFactory(Protocol):
    def __call__(self, tenant_id: UUID) -> ProductionUnitOfWork: ...


@dataclass(slots=True)
class ProductionService:
    uow_factory: ProductionUnitOfWorkFactory
    media_router: MediaRouter
    maximum_plan_cost: Decimal = Decimal("100")
    live_spend_cap: Decimal | None = None
    live_product_id: UUID | None = None

    @staticmethod
    def _can_spend(context: ExecutionContext) -> bool:
        return context.membership_status is MembershipStatus.ACTIVE and context.membership_role in {
            MembershipRole.OWNER,
            MembershipRole.ADMIN,
        }

    async def get_plan(self, context: ExecutionContext, plan_id: UUID) -> ProductionPlanRecord:
        async with self.uow_factory(context.tenant_id) as uow:
            value = await uow.production.get_plan(plan_id)
            if value is None:
                raise ProductionNotFound("ProductionPlan not found")
            return value

    async def list_plans(
        self, context: ExecutionContext, product_id: UUID
    ) -> tuple[ProductionPlanRecord, ...]:
        async with self.uow_factory(context.tenant_id) as uow:
            return await uow.production.list_plans(product_id)

    async def list_jobs(
        self, context: ExecutionContext, plan_id: UUID
    ) -> tuple[GenerationJob, ...]:
        async with self.uow_factory(context.tenant_id) as uow:
            if await uow.production.get_plan(plan_id) is None:
                raise ProductionNotFound("ProductionPlan not found")
            return await uow.production.list_jobs(plan_id)

    async def get_job(self, context: ExecutionContext, job_id: UUID) -> GenerationJob:
        async with self.uow_factory(context.tenant_id) as uow:
            value = await uow.production.get_job(job_id)
            if value is None:
                raise ProductionNotFound("GenerationJob not found")
            return value

    async def decide(
        self,
        context: ExecutionContext,
        plan_id: UUID,
        state: ProductionPlanDecisionState,
        rejection_feedback: str | None = None,
    ) -> ProductionPlanRecord:
        if not self._can_spend(context):
            raise ProductionPermissionDenied("production decisions require owner or admin")
        if state is ProductionPlanDecisionState.REJECTED:
            try:
                ProductionPlanningRequest(rejection_feedback=rejection_feedback)
            except ValueError:
                raise ProductionRejectionFeedbackRequired(
                    "bounded rejection feedback is required before a Producer rerun"
                ) from None
            if rejection_feedback is None:
                raise ProductionRejectionFeedbackRequired(
                    "bounded rejection feedback is required before a Producer rerun"
                )
        elif rejection_feedback is not None:
            raise ProductionRejectionFeedbackRequired(
                "rejection feedback is valid only for a rejected Production Plan"
            )
        async with self.uow_factory(context.tenant_id) as uow:
            record = await uow.production.get_plan(plan_id, for_update=True)
            if record is None:
                raise ProductionNotFound("ProductionPlan not found")
            if record.decision is not None:
                if record.decision.state is state:
                    return record
                raise ProductionDecisionConflict("ProductionPlan already has a decision")
            plan = record.plan
            video = self.media_router.resolve("production_video")
            image = self.media_router.resolve("production_image")
            if (
                video.pricing_version != plan.cost.video_pricing_version
                or image.pricing_version != plan.cost.image_pricing_version
            ):
                raise ProductionPricingChanged("media pricing changed after Plan creation")
            if plan.cost.estimated_total_cost > self.maximum_plan_cost:
                raise ProductionPermissionDenied("ProductionPlan exceeds the configured cost cap")
            decision = ProductionPlanDecision(
                context.tenant_id,
                plan.id,
                plan.semantic_digest,
                state,
                context.actor.id,
                video.route_version,
                image.route_version,
                video.pricing_version,
                image.pricing_version,
                plan.cost.estimated_total_cost,
                plan.cost.currency,
                rejection_feedback=rejection_feedback,
            )
            jobs = (
                self._jobs(plan, video, image)
                if state is ProductionPlanDecisionState.APPROVED_FOR_GENERATION
                else ()
            )
            await uow.production.add_decision(decision)
            if jobs:
                await uow.production.add_jobs(jobs)
            action = "production.plan.approved" if jobs else "production.plan.rejected"
            await uow.audit.append(
                tenant_audit(
                    context,
                    action=action,
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="production_plan",
                    resource_id=str(plan.id),
                    agent_run_id=plan.agent_run_id,
                    metadata=safe_metadata(
                        {
                            "plan_digest": plan.semantic_digest,
                            "estimated_max_cost": str(plan.cost.estimated_total_cost),
                            "currency": plan.cost.currency,
                            "generation_job_count": len(jobs),
                            "rejection_feedback_length": (
                                len(rejection_feedback) if rejection_feedback is not None else 0
                            ),
                        }
                    ),
                )
            )
            if jobs:
                payload = {
                    "production_plan_id": str(plan.id),
                    "decision_id": str(decision.id),
                    "plan_digest": plan.semantic_digest,
                    "video_route_version": video.route_version,
                    "image_route_version": image.route_version,
                    "video_pricing_version": video.pricing_version,
                    "image_pricing_version": image.pricing_version,
                    "estimated_max_cost": str(plan.cost.estimated_total_cost),
                    "currency": plan.cost.currency,
                }
                await uow.outbox.append(
                    tenant_event(
                        context,
                        event_type="production.plan.approved_for_generation.v1",
                        schema_version=1,
                        aggregate_type="production_plan",
                        aggregate_id=plan.id,
                        payload=payload,
                        payload_schema_digest=EventContractRegistry().schema_digest(
                            "production.plan.approved_for_generation.v1"
                        ),
                        occurred_at=decision.created_at,
                        agent_run_id=plan.agent_run_id,
                    )
                )
            await uow.commit()
            return ProductionPlanRecord(plan, decision, record.planning_cost)

    async def retry_after_spend_cap_increase(
        self,
        context: ExecutionContext,
        plan_id: UUID,
        *,
        transition_id: UUID,
    ) -> tuple[GenerationJob, ...]:
        """Re-admit the exact reserved jobs; never create jobs or call a provider."""

        if not self._can_spend(context):
            raise ProductionPermissionDenied("media recovery requires owner or admin")
        if self.live_spend_cap is None:
            raise ProductionPermissionDenied("live media spend cap is not configured")
        async with self.uow_factory(context.tenant_id) as uow:
            record = await uow.production.get_plan(plan_id, for_update=True)
            if record is None or record.decision is None:
                raise ProductionNotFound("ProductionPlan not found")
            plan, decision = record.plan, record.decision
            if self.live_product_id is not None and plan.product_id != self.live_product_id:
                raise ProductionPermissionDenied("ProductionPlan is outside LIVE_E2E_PRODUCT_ID")
            if decision.state is not ProductionPlanDecisionState.APPROVED_FOR_GENERATION:
                raise ProductionPermissionDenied("ProductionPlan is not approved")
            image = self.media_router.resolve("production_image")
            video = self.media_router.resolve("production_video")
            if (
                image.route_version != decision.image_route_version
                or image.pricing_version != decision.image_pricing_version
                or video.route_version != decision.video_route_version
                or video.pricing_version != decision.video_pricing_version
            ):
                raise ProductionPricingChanged("approved media route or pricing changed")
            jobs = await uow.production.list_jobs(plan_id)
            if any(
                (route := image if job.kind is MediaKind.IMAGE else video).route_version
                != job.route_version
                or route.pricing_version != job.pricing_version
                or route.provider != job.provider
                or route.model != job.model
                for job in jobs
            ):
                raise ProductionPricingChanged("GenerationJob route or pricing changed")
            await uow.production.validate_spend_cap_retry(plan)
            allowed = {
                GenerationJobStatus.BLOCKED_SPEND_CAP,
                GenerationJobStatus.READY,
                GenerationJobStatus.SUCCEEDED,
            }
            if not jobs or any(job.status not in allowed for job in jobs):
                raise ProductionPermissionDenied(
                    "media jobs are not safely retryable after a spend-cap block"
                )
            affected = tuple(
                job
                for job in jobs
                if job.status in {GenerationJobStatus.BLOCKED_SPEND_CAP, GenerationJobStatus.READY}
            )
            if affected and all(
                job.failure_code == "LIVE_E2E_SPEND_CAP_RETRY_APPROVED" for job in affected
            ):
                return affected
            if not affected or any(
                job.provider_operation_ref is not None
                or job.actual_cost != 0
                or job.unknown_cost != 0
                or job.failure_code not in {None, "LIVE_E2E_SPEND_CAP_REACHED"}
                for job in affected
            ):
                raise ProductionPermissionDenied(
                    "media jobs do not prove a before-provider retry boundary"
                )
            requirement = await uow.production.spend_requirement(plan_id, self.live_spend_cap)
            if requirement.committed_product_spend > requirement.configured_cap:
                raise ProductionPermissionDenied("LIVE_E2E_MAX_USD is still below required spend")
            requeued = await uow.production.requeue_spend_cap_jobs(plan_id)
            await uow.outbox.append(
                tenant_event(
                    context,
                    event_type="production.media.retry_requested.v1",
                    schema_version=1,
                    aggregate_type="production_plan",
                    aggregate_id=plan.id,
                    payload={
                        "production_plan_id": str(plan.id),
                        "transition_id": str(transition_id),
                        "generation_job_ids": [str(job.id) for job in requeued],
                    },
                    payload_schema_digest=EventContractRegistry().schema_digest(
                        "production.media.retry_requested.v1"
                    ),
                    occurred_at=datetime.now(UTC),
                    agent_run_id=plan.agent_run_id,
                    event_id=transition_id,
                )
            )
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="production.media.spend_cap_retry_approved",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="production_plan",
                    resource_id=str(plan.id),
                    agent_run_id=plan.agent_run_id,
                    metadata=safe_metadata(
                        {
                            "job_count": len(requeued),
                            "configured_cap": str(requirement.configured_cap),
                            "committed_product_spend": str(requirement.committed_product_spend),
                        }
                    ),
                )
            )
            await uow.commit()
            return requeued

    async def recover_stranded_media_start(
        self,
        context: ExecutionContext,
        plan_id: UUID,
        *,
        job_ids: tuple[UUID, ...],
    ) -> tuple[GenerationJob, ...]:
        """Converge a proven stranded pre-task start without external execution."""

        if not self._can_spend(context):
            raise ProductionPermissionDenied("media recovery requires owner or admin")
        if not job_ids:
            raise ProductionPermissionDenied("stranded media recovery requires exact jobs")
        async with self.uow_factory(context.tenant_id) as uow:
            record = await uow.production.get_plan(plan_id, for_update=True)
            if record is None or record.decision is None:
                raise ProductionNotFound("ProductionPlan not found")
            if record.decision.state is not ProductionPlanDecisionState.APPROVED_FOR_GENERATION:
                raise ProductionPermissionDenied("ProductionPlan is not approved")
            jobs = await uow.production.list_jobs(plan_id)
            selected = tuple(job for job in jobs if job.id in set(job_ids))
            if len(selected) != len(job_ids) or any(
                job.status is not GenerationJobStatus.STARTING
                or job.provider_operation_ref is not None
                or job.actual_cost != 0
                or job.unknown_cost != 0
                or job.output_asset_id is not None
                for job in selected
            ):
                already = tuple(
                    job
                    for job in jobs
                    if job.id in set(job_ids)
                    and job.status is GenerationJobStatus.OUTCOME_UNKNOWN
                    and job.failure_code == "STRANDED_MEDIA_START_OUTCOME_UNKNOWN"
                )
                if len(already) == len(job_ids):
                    return already
                raise ProductionPermissionDenied("GenerationJob is not a stranded media start")
            recovered = await uow.production.recover_stranded_starts(plan_id, job_ids)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="production.media.stranded_start_reconciled",
                    outcome=AuditOutcome.FAILED,
                    reason_code="STRANDED_MEDIA_START_OUTCOME_UNKNOWN",
                    resource_type="production_plan",
                    resource_id=str(plan_id),
                    agent_run_id=record.plan.agent_run_id,
                    metadata=safe_metadata(
                        {
                            "generation_job_ids": [str(value) for value in job_ids],
                            "provider_operation_present": False,
                            "provider_execution_occurred": False,
                            "actual_cost": "0",
                            "prior_unknown_cost": "0",
                        }
                    ),
                )
            )
            await uow.commit()
            return recovered

    async def abandon_unknown_media_and_retry(
        self,
        context: ExecutionContext,
        plan_id: UUID,
        *,
        job_ids: tuple[UUID, ...],
        transition_id: UUID,
    ) -> tuple[GenerationJob, ...]:
        """Accept bounded duplicate risk and re-admit the same immutable jobs."""

        if not self._can_spend(context):
            raise ProductionPermissionDenied("media ambiguity acceptance requires owner or admin")
        if self.live_spend_cap is None:
            raise ProductionPermissionDenied("live media spend cap is not configured")
        if not job_ids:
            raise ProductionPermissionDenied("media retry requires exact unknown jobs")
        async with self.uow_factory(context.tenant_id) as uow:
            record = await uow.production.get_plan(plan_id, for_update=True)
            if record is None or record.decision is None:
                raise ProductionNotFound("ProductionPlan not found")
            plan, decision = record.plan, record.decision
            if self.live_product_id is not None and plan.product_id != self.live_product_id:
                raise ProductionPermissionDenied("ProductionPlan is outside LIVE_E2E_PRODUCT_ID")
            if decision.state is not ProductionPlanDecisionState.APPROVED_FOR_GENERATION:
                raise ProductionPermissionDenied("ProductionPlan is not approved")
            jobs = await uow.production.list_jobs(plan_id)
            selected = tuple(job for job in jobs if job.id in set(job_ids))
            already = tuple(
                job
                for job in selected
                if job.status is GenerationJobStatus.READY
                and job.failure_code == "UNKNOWN_MEDIA_DUPLICATE_RISK_ACCEPTED"
            )
            if len(already) == len(job_ids):
                return already
            if len(selected) != len(job_ids) or any(
                job.status is not GenerationJobStatus.OUTCOME_UNKNOWN
                or job.provider_operation_ref is not None
                or job.actual_cost != 0
                or job.unknown_cost <= 0
                or job.output_asset_id is not None
                for job in selected
            ):
                raise ProductionPermissionDenied("media outcome is not safely retryable")
            for job in jobs:
                try:
                    route = self.media_router.resolve_execution(
                        job.media_profile,
                        job.route_version,
                        job.provider,
                        job.model,
                        job.pricing_version,
                    )
                except InvalidProductionPlan as error:
                    raise ProductionPricingChanged(
                        "approved media execution route is unavailable"
                    ) from error
                if route.capabilities.media_kind is not job.kind:
                    raise ProductionPricingChanged("approved media route kind changed")
            await uow.production.validate_spend_cap_retry(plan)
            requirement = await uow.production.spend_requirement(plan_id, self.live_spend_cap)
            retry_reservation = sum((job.reserved_cost for job in selected), Decimal(0))
            if requirement.committed_product_spend + retry_reservation > requirement.configured_cap:
                raise ProductionPermissionDenied("LIVE_E2E_MAX_USD is below ambiguity retry spend")
            requeued = await uow.production.retry_unknown_media_jobs(plan_id, job_ids)
            await uow.outbox.append(
                tenant_event(
                    context,
                    event_type="production.media.ambiguity_retry_requested.v1",
                    schema_version=1,
                    aggregate_type="production_plan",
                    aggregate_id=plan.id,
                    payload={
                        "production_plan_id": str(plan.id),
                        "transition_id": str(transition_id),
                        "generation_job_ids": [str(job.id) for job in requeued],
                        "duplicate_provider_risk_accepted": True,
                    },
                    payload_schema_digest=EventContractRegistry().schema_digest(
                        "production.media.ambiguity_retry_requested.v1"
                    ),
                    occurred_at=datetime.now(UTC),
                    agent_run_id=plan.agent_run_id,
                    event_id=transition_id,
                )
            )
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="production.media.unknown_outcome_abandoned_and_retry_approved",
                    outcome=AuditOutcome.SUCCESS,
                    reason_code="UNKNOWN_MEDIA_DUPLICATE_RISK_ACCEPTED",
                    resource_type="production_plan",
                    resource_id=str(plan.id),
                    agent_run_id=plan.agent_run_id,
                    metadata=safe_metadata(
                        {
                            "generation_job_ids": [str(value) for value in job_ids],
                            "duplicate_provider_risk_accepted": True,
                            "prior_provider_operation_present": False,
                            "prior_asset_present": False,
                            "retry_reservation": str(retry_reservation),
                        }
                    ),
                )
            )
            await uow.commit()
            return requeued

    @staticmethod
    def _jobs(
        plan: ProductionPlan, video: MediaRoute, image: MediaRoute
    ) -> tuple[GenerationJob, ...]:
        routes = {MediaKind.VIDEO: video, MediaKind.IMAGE: image}
        video_pricing = SeedancePricing()
        image_pricing = ImageReservationPricing()
        assets = {item.asset_id: item for item in plan.context.selected_assets}
        shots = {shot.shot_key: shot for scene in plan.scenes for shot in scene.shots}
        jobs: list[GenerationJob] = []
        for segment in plan.generation_segments:
            route = routes[segment.media_kind]
            refs: tuple[FrozenAssetReference, ...] = tuple(
                assets[value] for value in segment.reference_asset_ids
            )
            if segment.media_kind is MediaKind.VIDEO:
                reserved = video_pricing.cost(
                    output_duration_seconds=segment.duration_seconds or 0,
                    resolution=str(segment.generation_spec["resolution"]),
                )
                profile = "production_video"
            else:
                quality = next(
                    str(shots[key].specification["image_generation_spec"]["quality_intent"])  # type: ignore[index]
                    for key in segment.shot_keys
                )
                reserved = image_pricing.reserve(quality)
                profile = "production_image"
            jobs.append(
                GenerationJob(
                    plan.tenant_id,
                    plan.id,
                    segment.segment_key,
                    segment.media_kind,
                    profile,
                    route.route_version,
                    route.provider,
                    route.model,
                    route.pricing_version,
                    canonical_digest(dict(segment.generation_spec)),
                    refs,
                    reserved,
                    plan.cost.currency,
                    status=GenerationJobStatus.READY,
                )
            )
        return tuple(jobs)
