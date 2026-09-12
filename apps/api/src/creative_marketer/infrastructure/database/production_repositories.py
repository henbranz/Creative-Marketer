from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.infrastructure.database.agent_runtime_schema import agent_runs
from creative_marketer.production.domain import (
    FrozenAssetReference,
    GenerationJob,
    GenerationJobStatus,
    GenerationSegment,
    MediaKind,
    ProductionCost,
    ProductionPlan,
    ProductionPlanDecision,
    ProductionPlanDecisionState,
    ProductionPlanningContext,
    ProductionPlanningRequest,
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
