from __future__ import annotations

from dataclasses import dataclass
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
    MediaKind,
    ProductionDecisionConflict,
    ProductionNotFound,
    ProductionPermissionDenied,
    ProductionPlan,
    ProductionPlanDecision,
    ProductionPlanDecisionState,
    ProductionPricingChanged,
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
    ) -> ProductionPlanRecord:
        if not self._can_spend(context):
            raise ProductionPermissionDenied("production decisions require owner or admin")
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
