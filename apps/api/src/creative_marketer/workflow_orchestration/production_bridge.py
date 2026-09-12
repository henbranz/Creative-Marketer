from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from creative_marketer.events.application import ConsumerUnitOfWork
from creative_marketer.events.domain import DomainEvent, EventScopeKind
from creative_marketer.production.domain import MediaKind
from creative_marketer.production.service import ProductionUnitOfWorkFactory
from creative_marketer.workflow_orchestration.contracts import MediaProductionWorkflowInput


class MediaProductionWorkflowStarter(Protocol):
    async def start_media_production(self, request: MediaProductionWorkflowInput) -> None: ...


class ProductionJobSetResolver(Protocol):
    async def resolve(
        self, tenant_id: UUID, production_plan_id: UUID
    ) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]: ...


@dataclass(slots=True)
class DatabaseProductionJobSetResolver:
    """Loads the committed job set under tenant RLS; event payload never supplies job IDs."""

    factory: ProductionUnitOfWorkFactory

    async def resolve(
        self, tenant_id: UUID, production_plan_id: UUID
    ) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
        async with self.factory(tenant_id) as uow:
            record = await uow.production.get_plan(production_plan_id)
            if record is None or record.decision is None:
                raise ValueError("approved ProductionPlan is unavailable")
            jobs = await uow.production.list_jobs(production_plan_id)
        images = tuple(job.id for job in jobs if job.kind is MediaKind.IMAGE)
        videos = tuple(job.id for job in jobs if job.kind is MediaKind.VIDEO)
        if not jobs:
            raise ValueError("approved ProductionPlan has no generation jobs")
        return images, videos


@dataclass(slots=True)
class StartMediaProductionWorkflow:
    """Inbox handler: start is idempotent and succeeds before the receipt commits."""

    client: MediaProductionWorkflowStarter
    resolver: ProductionJobSetResolver

    async def __call__(self, event: DomainEvent, _uow: ConsumerUnitOfWork) -> None:
        if (
            event.event_type != "production.plan.approved_for_generation.v1"
            or event.scope_kind is not EventScopeKind.TENANT
            or event.tenant_id is None
            or event.aggregate_type != "production_plan"
        ):
            raise ValueError("unsupported Production workflow event")
        plan_id = event.payload.get("production_plan_id")
        if not isinstance(plan_id, str) or str(event.aggregate_id) != plan_id:
            raise ValueError("ProductionPlan event identity mismatch")
        parsed_plan_id = UUID(plan_id)
        image_jobs, video_jobs = await self.resolver.resolve(event.tenant_id, parsed_plan_id)
        await self.client.start_media_production(
            MediaProductionWorkflowInput(
                str(event.tenant_id),
                plan_id,
                tuple(str(value) for value in image_jobs),
                tuple(str(value) for value in video_jobs),
                str(event.correlation_id),
            )
        )
