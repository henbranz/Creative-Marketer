from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.infrastructure.database.agent_runtime_schema import (
    agent_runs,
    research_snapshots,
)
from creative_marketer.infrastructure.database.catalog_schema import (
    assets,
    product_knowledge_snapshots,
)
from creative_marketer.infrastructure.database.creative_schema import (
    concept_decisions,
    concepts,
)
from creative_marketer.production.domain import (
    FrozenAssetReference,
    GenerationJob,
    GenerationJobStatus,
    GenerationSegment,
    MediaKind,
    MediaSpendRequirement,
    ProductionCost,
    ProductionCreativeRefreshRequired,
    ProductionPermissionDenied,
    ProductionPlan,
    ProductionPlanDecision,
    ProductionPlanDecisionState,
    ProductionPlanningContext,
    ProductionPlanningRequest,
    ProductionRightsChanged,
    ProductionScene,
    ProductionShot,
    SourceStrategy,
)
from creative_marketer.production.service import ProductionPlanRecord

from .production_schema import (
    generation_jobs,
    generation_segments,
    media_budget_usage,
    plan_decisions,
    production_plans,
    production_scenes,
    production_shots,
)
from .production_spend import product_media_spend_requirement


def _asset(value: object) -> FrozenAssetReference:
    item = value if isinstance(value, dict) else {}
    return FrozenAssetReference(
        UUID(str(item["asset_id"])),
        str(item["digest"]),
        str(item["kind"]),
        str(item["role"]),
        tuple(str(use) for use in item["allowed_uses"]),
    )


class SqlAlchemyProductionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _record(self, row: object) -> ProductionPlanRecord:
        p = row._mapping  # type: ignore[attr-defined]
        run_row = (
            await self._session.execute(
                select(agent_runs).where(agent_runs.c.id == p["agent_run_id"])
            )
        ).one()
        run = run_row._mapping
        concept_ref = next(
            item for item in run["input_context_refs"] if item.get("kind") == "approved_concept"
        )
        asset_ref = next(
            item for item in run["input_context_refs"] if item.get("kind") == "selected_assets"
        )
        request_ref = next(
            item for item in run["input_context_refs"] if item.get("kind") == "production_request"
        )
        context = ProductionPlanningContext(
            p["concept_id"],
            p["concept_digest"],
            p["concept_set_id"],
            p["concept_set_digest"],
            p["product_snapshot_id"],
            p["product_snapshot_digest"],
            p["research_snapshot_id"],
            p["research_snapshot_digest"],
            p["creative_decision_id"],
            tuple(_asset(item) for item in asset_ref["assets"]),
            ProductionPlanningRequest(
                str(request_ref["target_format"]), str(request_ref["aspect_ratio"])
            ),
            p["context_digest"],
        )
        scene_rows = (
            await self._session.execute(
                select(production_scenes)
                .where(production_scenes.c.production_plan_id == p["id"])
                .order_by(production_scenes.c.ordinal)
            )
        ).all()
        shot_rows = (
            await self._session.execute(
                select(production_shots)
                .where(production_shots.c.production_plan_id == p["id"])
                .order_by(production_shots.c.production_scene_id, production_shots.c.ordinal)
            )
        ).all()
        shots_by_scene: dict[UUID, list[ProductionShot]] = {}
        for shot_row in shot_rows:
            shot = shot_row._mapping
            shots_by_scene.setdefault(shot["production_scene_id"], []).append(
                ProductionShot(
                    shot["shot_key"],
                    next(
                        scene._mapping["scene_key"]
                        for scene in scene_rows
                        if scene._mapping["id"] == shot["production_scene_id"]
                    ),
                    shot["ordinal"],
                    SourceStrategy(shot["source_strategy"]),
                    shot["specification"],
                )
            )
        scenes = tuple(
            ProductionScene(
                scene._mapping["scene_key"],
                scene._mapping["ordinal"],
                scene._mapping["content"]["purpose"],
                scene._mapping["duration_seconds"],
                scene._mapping["content"]["message"],
                scene._mapping["content"].get("voiceover"),
                scene._mapping["content"].get("on_screen_text"),
                tuple(shots_by_scene.get(scene._mapping["id"], ())),
            )
            for scene in scene_rows
        )
        segment_rows = (
            await self._session.execute(
                select(generation_segments)
                .where(generation_segments.c.production_plan_id == p["id"])
                .order_by(generation_segments.c.ordinal)
            )
        ).all()
        segments = tuple(
            GenerationSegment(
                item._mapping["segment_key"],
                tuple(item._mapping["shot_keys"]),
                MediaKind(item._mapping["media_kind"]),
                item._mapping["duration_seconds"],
                tuple(item._mapping["continuity"]),
                tuple(UUID(value) for value in item._mapping["reference_assets"]),
                item._mapping["generation_spec"],
            )
            for item in segment_rows
        )
        plan = ProductionPlan(
            p["tenant_id"],
            p["product_id"],
            p["agent_run_id"],
            context,
            p["strategy"],
            scenes,
            segments,
            tuple(p["required_assets"]),
            ProductionCost(
                p["estimated_max_video_cost"],
                p["estimated_max_image_cost"],
                p["currency"],
                p["video_pricing_version"],
                p["image_pricing_version"],
            ),
            p["semantic_digest"],
            id=p["id"],
            schema_version=p["schema_version"],
            created_at=p["created_at"],
        )
        decision_row = (
            await self._session.execute(
                select(plan_decisions)
                .where(plan_decisions.c.production_plan_id == p["id"])
                .order_by(plan_decisions.c.created_at.desc(), plan_decisions.c.id.desc())
                .limit(1)
            )
        ).first()
        decision = None
        if decision_row is not None:
            d = decision_row._mapping
            decision = ProductionPlanDecision(
                d["tenant_id"],
                d["production_plan_id"],
                d["plan_digest"],
                ProductionPlanDecisionState(d["state"]),
                d["decided_by"],
                d["video_route_version"],
                d["image_route_version"],
                d["video_pricing_version"],
                d["image_pricing_version"],
                d["estimated_max_cost"],
                d["currency"],
                d["id"],
                d["created_at"],
                d["rejection_feedback"],
            )
        if concept_ref["digest"] != plan.context.concept_digest:
            raise ValueError("ProductionPlan Concept provenance mismatch")
        return ProductionPlanRecord(plan, decision, Decimal(run["estimated_cost"]))

    async def get_plan(
        self, plan_id: UUID, *, for_update: bool = False
    ) -> ProductionPlanRecord | None:
        if for_update:
            await self._session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"production-plan-decision:{plan_id}"},
            )
        row = (
            await self._session.execute(
                select(production_plans).where(production_plans.c.id == plan_id)
            )
        ).first()
        return await self._record(row) if row else None

    async def list_plans(self, product_id: UUID) -> tuple[ProductionPlanRecord, ...]:
        rows = (
            await self._session.execute(
                select(production_plans)
                .where(production_plans.c.product_id == product_id)
                .order_by(production_plans.c.created_at.desc(), production_plans.c.id.desc())
            )
        ).all()
        return tuple([await self._record(row) for row in rows])

    async def add_decision(self, value: ProductionPlanDecision) -> None:
        await self._session.execute(
            insert(plan_decisions).values(
                id=value.id,
                tenant_id=value.tenant_id,
                production_plan_id=value.production_plan_id,
                plan_digest=value.plan_digest,
                state=value.state.value,
                decided_by=value.decided_by,
                video_route_version=value.video_route_version,
                image_route_version=value.image_route_version,
                video_pricing_version=value.video_pricing_version,
                image_pricing_version=value.image_pricing_version,
                estimated_max_cost=value.estimated_max_cost,
                currency=value.currency,
                rejection_feedback=value.rejection_feedback,
                created_at=value.created_at,
            )
        )

    async def add_jobs(self, values: tuple[GenerationJob, ...]) -> None:
        for value in values:
            segment_id = await self._session.scalar(
                select(generation_segments.c.id).where(
                    generation_segments.c.production_plan_id == value.production_plan_id,
                    generation_segments.c.segment_key == value.segment_key,
                )
            )
            if segment_id is None:
                raise ValueError("GenerationJob segment is unavailable")
            await self._session.execute(
                insert(generation_jobs).values(
                    id=value.id,
                    tenant_id=value.tenant_id,
                    production_plan_id=value.production_plan_id,
                    generation_segment_id=segment_id,
                    kind=value.kind.value,
                    status=value.status.value,
                    media_profile=value.media_profile,
                    route_version=value.route_version,
                    provider=value.provider,
                    model=value.model,
                    pricing_version=value.pricing_version,
                    generation_spec_digest=value.generation_spec_digest,
                    input_assets=[item.primitive() for item in value.input_assets],
                    provider_operation_ref=value.provider_operation_ref,
                    reserved_cost=value.reserved_cost,
                    actual_cost=value.actual_cost,
                    unknown_cost=value.unknown_cost,
                    currency=value.currency,
                    output_asset_id=value.output_asset_id,
                    failure_code=value.failure_code,
                    initiated_by_user_id=value.initiated_by_user_id,
                    executed_by_workload_id=value.executed_by_workload_id,
                    created_at=value.created_at,
                    updated_at=value.updated_at,
                )
            )
            await self._session.execute(
                insert(media_budget_usage).values(
                    id=uuid4(),
                    tenant_id=value.tenant_id,
                    generation_job_id=value.id,
                    entry_kind="RESERVED",
                    amount=value.reserved_cost,
                    currency=value.currency,
                    created_at=datetime.now(UTC),
                )
            )

    async def list_jobs(self, plan_id: UUID) -> tuple[GenerationJob, ...]:
        rows = (
            await self._session.execute(
                select(generation_jobs, generation_segments.c.segment_key)
                .join(
                    generation_segments,
                    generation_segments.c.id == generation_jobs.c.generation_segment_id,
                )
                .where(generation_jobs.c.production_plan_id == plan_id)
                .order_by(generation_jobs.c.created_at, generation_jobs.c.id)
            )
        ).all()
        return tuple(self._job(row) for row in rows)

    async def get_job(self, job_id: UUID) -> GenerationJob | None:
        row = (
            await self._session.execute(
                select(generation_jobs, generation_segments.c.segment_key)
                .join(
                    generation_segments,
                    generation_segments.c.id == generation_jobs.c.generation_segment_id,
                )
                .where(generation_jobs.c.id == job_id)
            )
        ).first()
        return self._job(row) if row else None

    async def spend_requirement(
        self, plan_id: UUID, configured_cap: Decimal
    ) -> MediaSpendRequirement:
        row = (
            await self._session.execute(
                select(production_plans.c.tenant_id, production_plans.c.product_id).where(
                    production_plans.c.id == plan_id
                )
            )
        ).one_or_none()
        if row is None:
            raise ValueError("ProductionPlan is unavailable")
        return await product_media_spend_requirement(
            self._session, row.tenant_id, row.product_id, configured_cap
        )

    async def requeue_spend_cap_jobs(self, plan_id: UUID) -> tuple[GenerationJob, ...]:
        await self._session.execute(
            update(generation_jobs)
            .where(
                generation_jobs.c.production_plan_id == plan_id,
                generation_jobs.c.status.in_(
                    (
                        GenerationJobStatus.BLOCKED_SPEND_CAP.value,
                        GenerationJobStatus.READY.value,
                    )
                ),
                generation_jobs.c.provider_operation_ref.is_(None),
                generation_jobs.c.actual_cost == 0,
                generation_jobs.c.unknown_cost == 0,
                generation_jobs.c.failure_code.is_(None)
                | (generation_jobs.c.failure_code == "LIVE_E2E_SPEND_CAP_REACHED"),
            )
            .values(
                status=GenerationJobStatus.READY.value,
                failure_code="LIVE_E2E_SPEND_CAP_RETRY_APPROVED",
                updated_at=datetime.now(UTC),
            )
        )
        jobs = await self.list_jobs(plan_id)
        return tuple(job for job in jobs if job.status is GenerationJobStatus.READY)

    async def recover_stranded_starts(
        self, plan_id: UUID, job_ids: tuple[UUID, ...]
    ) -> tuple[GenerationJob, ...]:
        now = datetime.now(UTC)
        rows = (
            await self._session.execute(
                update(generation_jobs)
                .where(
                    generation_jobs.c.production_plan_id == plan_id,
                    generation_jobs.c.id.in_(job_ids),
                    generation_jobs.c.status == GenerationJobStatus.STARTING.value,
                    generation_jobs.c.provider_operation_ref.is_(None),
                    generation_jobs.c.actual_cost == 0,
                    generation_jobs.c.unknown_cost == 0,
                    generation_jobs.c.output_asset_id.is_(None),
                )
                .values(
                    status=GenerationJobStatus.OUTCOME_UNKNOWN.value,
                    unknown_cost=generation_jobs.c.reserved_cost,
                    failure_code="STRANDED_MEDIA_START_OUTCOME_UNKNOWN",
                    updated_at=now,
                )
                .returning(
                    generation_jobs.c.id,
                    generation_jobs.c.tenant_id,
                    generation_jobs.c.reserved_cost,
                    generation_jobs.c.currency,
                )
            )
        ).all()
        if len(rows) != len(job_ids):
            raise ProductionPermissionDenied("stale stranded media transition rejected")
        await self._session.execute(
            insert(media_budget_usage),
            [
                {
                    "id": uuid4(),
                    "tenant_id": row.tenant_id,
                    "generation_job_id": row.id,
                    "entry_kind": "UNKNOWN",
                    "amount": row.reserved_cost,
                    "currency": row.currency,
                    "created_at": now,
                }
                for row in rows
            ],
        )
        jobs = await self.list_jobs(plan_id)
        selected = set(job_ids)
        return tuple(job for job in jobs if job.id in selected)

    async def retry_unknown_media_jobs(
        self, plan_id: UUID, job_ids: tuple[UUID, ...]
    ) -> tuple[GenerationJob, ...]:
        rows = (
            await self._session.execute(
                update(generation_jobs)
                .where(
                    generation_jobs.c.production_plan_id == plan_id,
                    generation_jobs.c.id.in_(job_ids),
                    generation_jobs.c.status == GenerationJobStatus.OUTCOME_UNKNOWN.value,
                    generation_jobs.c.provider_operation_ref.is_(None),
                    generation_jobs.c.actual_cost == 0,
                    generation_jobs.c.unknown_cost > 0,
                    generation_jobs.c.output_asset_id.is_(None),
                )
                .values(
                    status=GenerationJobStatus.READY.value,
                    failure_code="UNKNOWN_MEDIA_DUPLICATE_RISK_ACCEPTED",
                    initiated_by_user_id=None,
                    executed_by_workload_id=None,
                    updated_at=datetime.now(UTC),
                )
                .returning(generation_jobs.c.id)
            )
        ).all()
        if len(rows) != len(job_ids):
            raise ProductionPermissionDenied("stale unknown media retry transition rejected")
        jobs = await self.list_jobs(plan_id)
        selected = set(job_ids)
        return tuple(job for job in jobs if job.id in selected)

    async def validate_spend_cap_retry(self, plan: ProductionPlan) -> None:
        product_snapshot = (
            await self._session.execute(
                select(product_knowledge_snapshots.c.id, product_knowledge_snapshots.c.digest)
                .where(product_knowledge_snapshots.c.product_id == plan.product_id)
                .order_by(
                    product_knowledge_snapshots.c.created_at.desc(),
                    product_knowledge_snapshots.c.id.desc(),
                )
                .limit(1)
            )
        ).first()
        research = (
            await self._session.execute(
                select(research_snapshots.c.id, research_snapshots.c.semantic_digest)
                .where(research_snapshots.c.product_id == plan.product_id)
                .order_by(research_snapshots.c.created_at.desc(), research_snapshots.c.id.desc())
                .limit(1)
            )
        ).first()
        concept = (
            await self._session.execute(
                select(concepts.c.semantic_digest).where(concepts.c.id == plan.context.concept_id)
            )
        ).first()
        decision = (
            await self._session.execute(
                select(concept_decisions.c.id, concept_decisions.c.state)
                .where(concept_decisions.c.concept_id == plan.context.concept_id)
                .order_by(concept_decisions.c.created_at.desc(), concept_decisions.c.id.desc())
                .limit(1)
            )
        ).first()
        if (
            product_snapshot is None
            or product_snapshot.id != plan.context.product_snapshot_id
            or product_snapshot.digest != plan.context.product_snapshot_digest
            or research is None
            or research.id != plan.context.research_snapshot_id
            or research.semantic_digest != plan.context.research_snapshot_digest
            or concept is None
            or concept.semantic_digest != plan.context.concept_digest
            or decision is None
            or decision.id != plan.context.creative_decision_id
            or decision.state != "APPROVED_FOR_PRODUCTION"
        ):
            raise ProductionCreativeRefreshRequired("ProductionPlan input provenance is outdated")
        for job in await self.list_jobs(plan.id):
            reserved = await self._session.scalar(
                select(func.coalesce(func.sum(media_budget_usage.c.amount), 0)).where(
                    media_budget_usage.c.generation_job_id == job.id,
                    media_budget_usage.c.entry_kind == "RESERVED",
                    media_budget_usage.c.currency == job.currency,
                )
            )
            if Decimal(reserved or 0) != job.reserved_cost:
                raise ProductionPermissionDenied("GenerationJob spend reservation is unavailable")
            for reference in job.input_assets:
                row = (
                    await self._session.execute(
                        select(
                            assets.c.status,
                            assets.c.rights_status,
                            assets.c.allowed_uses,
                            assets.c.digest,
                        ).where(assets.c.id == reference.asset_id)
                    )
                ).first()
                if (
                    row is None
                    or row.status != "ready"
                    or row.rights_status != "confirmed"
                    or "generation_input" not in (row.allowed_uses or ())
                    or row.digest != reference.digest
                ):
                    raise ProductionRightsChanged("generation reference rights or digest changed")

    @staticmethod
    def _job(row: object) -> GenerationJob:
        d = row._mapping  # type: ignore[attr-defined]
        return GenerationJob(
            d["tenant_id"],
            d["production_plan_id"],
            d["segment_key"],
            MediaKind(d["kind"]),
            d["media_profile"],
            d["route_version"],
            d["provider"],
            d["model"],
            d["pricing_version"],
            d["generation_spec_digest"],
            tuple(_asset(item) for item in d["input_assets"]),
            d["reserved_cost"],
            d["currency"],
            id=d["id"],
            status=GenerationJobStatus(d["status"]),
            provider_operation_ref=d["provider_operation_ref"],
            actual_cost=d["actual_cost"],
            unknown_cost=d["unknown_cost"],
            output_asset_id=d["output_asset_id"],
            failure_code=d["failure_code"],
            initiated_by_user_id=d.get("initiated_by_user_id"),
            executed_by_workload_id=d.get("executed_by_workload_id"),
            created_at=d["created_at"],
            updated_at=d["updated_at"],
        )
