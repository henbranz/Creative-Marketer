from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import insert, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.assembly.application import (
    AssemblyPlanRecord,
    AssemblySceneInput,
    AssemblySegmentInput,
    AssemblyShotInput,
    FinalCreativeRecord,
    ProductionAssemblyInput,
    SourceCandidate,
)
from creative_marketer.assembly.domain import (
    AssemblyItem,
    AssemblyJob,
    AssemblyJobStatus,
    AssemblyNotFound,
    AssemblyNotReady,
    AssemblyPlan,
    AudioBehavior,
    CaptionCue,
    CaptionKind,
    FinalCreative,
    FinalCreativeDecision,
    FinalCreativeDecisionState,
    FitMode,
    OverlayInstruction,
    OverlayStyle,
    RenderProfile,
    SourceKind,
    Transition,
)
from creative_marketer.infrastructure.database.agent_runtime_schema import research_snapshots
from creative_marketer.infrastructure.database.catalog_schema import (
    assets,
    product_knowledge_snapshots,
)
from creative_marketer.infrastructure.database.creative_schema import concept_decisions, concepts
from creative_marketer.infrastructure.database.production_schema import (
    generation_jobs,
    generation_segments,
    plan_decisions,
    production_plans,
    production_scenes,
    production_shots,
)

from .assembly_schema import (
    assembly_items,
    assembly_jobs,
    assembly_plans,
    caption_cues,
    final_creative_decisions,
    final_creatives,
    manual_source_bindings,
    overlay_instructions,
)


def _candidate(row: Mapping[str, Any] | None) -> SourceCandidate | None:
    if row is None:
        return None
    return SourceCandidate(
        row["id"],
        row["digest"],
        str(row["kind"]).upper(),
        row["status"],
        row["rights_status"],
        tuple(row["allowed_uses"] or ()),
    )


class SqlAlchemyAssemblyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def production_input_for_shot(self, shot_id: UUID) -> ProductionAssemblyInput | None:
        plan_id = await self._session.scalar(
            select(production_shots.c.production_plan_id).where(production_shots.c.id == shot_id)
        )
        return None if plan_id is None else await self.production_input(plan_id)

    async def production_input(self, production_plan_id: UUID) -> ProductionAssemblyInput | None:
        plan = (
            (
                await self._session.execute(
                    select(production_plans).where(production_plans.c.id == production_plan_id)
                )
            )
            .mappings()
            .first()
        )
        if plan is None:
            return None
        scene_rows = list(
            (
                await self._session.execute(
                    select(production_scenes)
                    .where(production_scenes.c.production_plan_id == production_plan_id)
                    .order_by(production_scenes.c.ordinal)
                )
            ).mappings()
        )
        shot_rows = list(
            (
                await self._session.execute(
                    select(production_shots)
                    .where(production_shots.c.production_plan_id == production_plan_id)
                    .order_by(production_shots.c.production_scene_id, production_shots.c.ordinal)
                )
            ).mappings()
        )
        segment_rows = list(
            (
                await self._session.execute(
                    select(generation_segments)
                    .where(generation_segments.c.production_plan_id == production_plan_id)
                    .order_by(generation_segments.c.ordinal)
                )
            ).mappings()
        )
        job_rows = list(
            (
                await self._session.execute(
                    select(generation_jobs).where(
                        generation_jobs.c.production_plan_id == production_plan_id
                    )
                )
            ).mappings()
        )
        jobs_by_segment = {row["generation_segment_id"]: row for row in job_rows}
        binding_rows = list(
            (
                await self._session.execute(
                    select(manual_source_bindings)
                    .where(
                        manual_source_bindings.c.production_shot_id.in_(
                            [row["id"] for row in shot_rows]
                        )
                    )
                    .order_by(
                        manual_source_bindings.c.bound_at.desc(),
                        manual_source_bindings.c.id.desc(),
                    )
                )
            ).mappings()
        )
        latest_bindings: dict[UUID, UUID] = {}
        for row in binding_rows:
            latest_bindings.setdefault(row["production_shot_id"], row["asset_id"])
        asset_ids: set[UUID] = set(latest_bindings.values())
        for row in shot_rows:
            raw = (row["specification"] or {}).get("existing_asset_id")
            if raw:
                asset_ids.add(UUID(str(raw)))
        for row in job_rows:
            if row["output_asset_id"]:
                asset_ids.add(row["output_asset_id"])
        asset_rows = (
            list(
                (
                    await self._session.execute(select(assets).where(assets.c.id.in_(asset_ids)))
                ).mappings()
            )
            if asset_ids
            else []
        )
        asset_map = {row["id"]: row for row in asset_rows}
        shots_by_scene: dict[UUID, list[AssemblyShotInput]] = {}
        for row in shot_rows:
            raw_existing = (row["specification"] or {}).get("existing_asset_id")
            existing_id = UUID(str(raw_existing)) if raw_existing else None
            manual_id = latest_bindings.get(row["id"])
            shots_by_scene.setdefault(row["production_scene_id"], []).append(
                AssemblyShotInput(
                    row["id"],
                    row["shot_key"],
                    next(
                        item["scene_key"]
                        for item in scene_rows
                        if item["id"] == row["production_scene_id"]
                    ),
                    row["source_strategy"],
                    existing_id,
                    _candidate(cast(Mapping[str, Any] | None, asset_map.get(manual_id)))
                    if manual_id
                    else None,
                )
            )
        scenes = tuple(
            AssemblySceneInput(
                row["scene_key"],
                row["ordinal"],
                row["duration_seconds"] * 1000,
                (row["content"] or {}).get("voiceover"),
                (row["content"] or {}).get("on_screen_text"),
                tuple(shots_by_scene.get(row["id"], ())),
            )
            for row in scene_rows
        )
        shot_ids_by_key = {row["shot_key"]: row["id"] for row in shot_rows}
        segments = []
        for row in segment_rows:
            job = jobs_by_segment.get(row["id"])
            output = (
                asset_map.get(job["output_asset_id"]) if job and job["output_asset_id"] else None
            )
            segments.append(
                AssemblySegmentInput(
                    row["id"],
                    row["segment_key"],
                    row["media_kind"],
                    row["duration_seconds"] * 1000 if row["duration_seconds"] else None,
                    tuple(shot_ids_by_key[key] for key in row["shot_keys"]),
                    tuple(row["shot_keys"]),
                    _candidate(cast(Mapping[str, Any] | None, output)),
                    job["status"] if job else None,
                )
            )
        concept = (
            (
                await self._session.execute(
                    select(concepts).where(concepts.c.id == plan["concept_id"])
                )
            )
            .mappings()
            .first()
        )
        if concept is None:
            return None
        latest_plan_decision = (
            await self._session.execute(
                select(plan_decisions.c.state)
                .where(plan_decisions.c.production_plan_id == production_plan_id)
                .order_by(plan_decisions.c.created_at.desc(), plan_decisions.c.id.desc())
                .limit(1)
            )
        ).first()
        latest_creative = (
            await self._session.execute(
                select(concept_decisions.c.id, concept_decisions.c.state)
                .where(concept_decisions.c.concept_id == plan["concept_id"])
                .order_by(concept_decisions.c.created_at.desc(), concept_decisions.c.id.desc())
                .limit(1)
            )
        ).first()
        latest_product = (
            await self._session.execute(
                select(product_knowledge_snapshots.c.id, product_knowledge_snapshots.c.digest)
                .where(product_knowledge_snapshots.c.product_id == plan["product_id"])
                .order_by(
                    product_knowledge_snapshots.c.created_at.desc(),
                    product_knowledge_snapshots.c.id.desc(),
                )
                .limit(1)
            )
        ).first()
        latest_research = (
            await self._session.execute(
                select(research_snapshots.c.id, research_snapshots.c.semantic_digest)
                .where(research_snapshots.c.product_id == plan["product_id"])
                .order_by(research_snapshots.c.created_at.desc(), research_snapshots.c.id.desc())
                .limit(1)
            )
        ).first()
        current = bool(
            latest_plan_decision
            and latest_plan_decision.state == "APPROVED_FOR_GENERATION"
            and latest_creative
            and latest_creative.id == plan["creative_decision_id"]
            and latest_creative.state == "APPROVED_FOR_PRODUCTION"
            and latest_product
            and latest_product.id == plan["product_snapshot_id"]
            and latest_product.digest == plan["product_snapshot_digest"]
            and latest_research
            and latest_research.id == plan["research_snapshot_id"]
            and latest_research.semantic_digest == plan["research_snapshot_digest"]
            and concept["semantic_digest"] == plan["concept_digest"]
        )
        payload = concept["concept_payload"] or {}
        cta = payload.get("cta") or {}
        return ProductionAssemblyInput(
            plan["tenant_id"],
            plan["product_id"],
            plan["id"],
            plan["semantic_digest"],
            plan["concept_id"],
            plan["concept_digest"],
            current,
            scenes,
            tuple(segments),
            {
                key: value
                for key, row in asset_map.items()
                if (value := _candidate(cast(Mapping[str, Any], row))) is not None
            },
            str(cta.get("text")) if cta.get("text") else None,
            tuple(str(item) for item in payload.get("required_disclaimers", [])),
        )

    async def bind_manual_source(self, shot_id: UUID, asset_id: UUID, user_id: UUID) -> None:
        shot = (
            await self._session.execute(
                select(production_shots.c.id, production_plans.c.product_id)
                .join(
                    production_plans, production_plans.c.id == production_shots.c.production_plan_id
                )
                .where(
                    production_shots.c.id == shot_id,
                    production_shots.c.source_strategy == "MANUAL_CAPTURE",
                )
            )
        ).first()
        asset = (
            (await self._session.execute(select(assets).where(assets.c.id == asset_id)))
            .mappings()
            .first()
        )
        if shot is None or asset is None:
            raise AssemblyNotFound("manual shot or Asset not found")
        if (
            asset["product_id"] != shot.product_id
            or asset["kind"] != "video"
            or asset["status"] != "ready"
            or asset["rights_status"] != "confirmed"
            or "generation_input" not in (asset["allowed_uses"] or [])
        ):
            raise AssemblyNotReady("manual source is not a READY rights-confirmed Product video")
        await self._session.execute(
            insert(manual_source_bindings).values(
                id=uuid4(),
                tenant_id=asset["tenant_id"],
                production_shot_id=shot_id,
                asset_id=asset_id,
                bound_by_user_id=user_id,
                bound_at=datetime.now(UTC),
            )
        )

    async def add_plan(self, plan: AssemblyPlan, job: AssemblyJob) -> None:
        await self._session.execute(
            insert(assembly_plans).values(
                id=plan.id,
                tenant_id=plan.tenant_id,
                product_id=plan.product_id,
                production_plan_id=plan.production_plan_id,
                production_plan_digest=plan.production_plan_digest,
                creative_concept_id=plan.creative_concept_id,
                creative_concept_digest=plan.creative_concept_digest,
                schema_version=plan.schema_version,
                render_profile_key=plan.render_profile.key,
                render_profile_version=plan.render_profile.version,
                timeline_duration_ms=plan.timeline_duration_ms,
                semantic_digest=plan.semantic_digest,
                created_by_user_id=plan.created_by_user_id,
                created_at=plan.created_at,
            )
        )
        for item in plan.items:
            await self._session.execute(
                insert(assembly_items).values(
                    id=uuid4(),
                    tenant_id=plan.tenant_id,
                    assembly_plan_id=plan.id,
                    item_key=item.item_key,
                    ordinal=item.ordinal,
                    source_kind=item.source_kind.value,
                    source_asset_id=item.source_asset_id,
                    source_asset_digest=item.source_asset_digest,
                    source_media_kind=item.source_media_kind,
                    production_shot_ids=[str(v) for v in item.production_shot_ids],
                    production_shot_keys=list(item.production_shot_keys),
                    generation_segment_id=item.generation_segment_id,
                    timeline_start_ms=item.timeline_start_ms,
                    timeline_duration_ms=item.timeline_duration_ms,
                    source_in_ms=item.source_in_ms,
                    source_out_ms=item.source_out_ms,
                    fit_mode=item.fit_mode.value,
                    audio_behavior=item.audio_behavior.value,
                    transition_in=item.transition_in.value,
                    transition_out=item.transition_out.value,
                    transition_duration_ms=item.transition_duration_ms,
                    created_at=plan.created_at,
                )
            )
        for cue in plan.captions:
            await self._session.execute(
                insert(caption_cues).values(
                    id=uuid4(),
                    tenant_id=plan.tenant_id,
                    assembly_plan_id=plan.id,
                    ordinal=cue.ordinal,
                    caption_kind=cue.kind.value,
                    text=cue.text,
                    start_ms=cue.start_ms,
                    end_ms=cue.end_ms,
                    created_at=plan.created_at,
                )
            )
        for overlay in plan.overlays:
            await self._session.execute(
                insert(overlay_instructions).values(
                    id=uuid4(),
                    tenant_id=plan.tenant_id,
                    assembly_plan_id=plan.id,
                    ordinal=overlay.ordinal,
                    text=overlay.text,
                    start_ms=overlay.start_ms,
                    end_ms=overlay.end_ms,
                    position=overlay.position,
                    style_token=overlay.style_token.value,
                    source_provenance=overlay.source_provenance,
                    created_at=plan.created_at,
                )
            )
        await self._session.execute(
            insert(assembly_jobs).values(
                id=job.id,
                tenant_id=job.tenant_id,
                assembly_plan_id=job.assembly_plan_id,
                idempotency_key=job.idempotency_key,
                status=job.status.value,
                failure_code=job.failure_code,
                output_asset_id=job.output_asset_id,
                final_creative_id=job.final_creative_id,
                renderer=job.renderer,
                renderer_version=job.renderer_version,
                created_by_user_id=job.created_by_user_id,
                created_at=job.created_at,
                updated_at=job.updated_at,
            )
        )

    async def list_plans(self, production_plan_id: UUID) -> tuple[AssemblyPlanRecord, ...]:
        rows = list(
            (
                await self._session.execute(
                    select(assembly_plans)
                    .where(assembly_plans.c.production_plan_id == production_plan_id)
                    .order_by(assembly_plans.c.created_at.desc())
                )
            ).mappings()
        )
        records: list[AssemblyPlanRecord] = []
        for row in rows:
            records.append(await self._record(cast(Mapping[str, Any], row)))
        return tuple(records)

    async def get_plan(self, plan_id: UUID) -> AssemblyPlanRecord | None:
        row = (
            (
                await self._session.execute(
                    select(assembly_plans).where(assembly_plans.c.id == plan_id)
                )
            )
            .mappings()
            .first()
        )
        return None if row is None else await self._record(cast(Mapping[str, Any], row))

    async def _record(self, row: Mapping[str, Any]) -> AssemblyPlanRecord:
        item_rows = list(
            (
                await self._session.execute(
                    select(assembly_items)
                    .where(assembly_items.c.assembly_plan_id == row["id"])
                    .order_by(assembly_items.c.ordinal)
                )
            ).mappings()
        )
        cue_rows = list(
            (
                await self._session.execute(
                    select(caption_cues)
                    .where(caption_cues.c.assembly_plan_id == row["id"])
                    .order_by(caption_cues.c.ordinal)
                )
            ).mappings()
        )
        overlay_rows = list(
            (
                await self._session.execute(
                    select(overlay_instructions)
                    .where(overlay_instructions.c.assembly_plan_id == row["id"])
                    .order_by(overlay_instructions.c.ordinal)
                )
            ).mappings()
        )
        plan = AssemblyPlan(
            row["tenant_id"],
            row["product_id"],
            row["production_plan_id"],
            row["production_plan_digest"],
            row["creative_concept_id"],
            row["creative_concept_digest"],
            tuple(
                AssemblyItem(
                    item["item_key"],
                    item["ordinal"],
                    SourceKind(item["source_kind"]),
                    item["source_asset_id"],
                    item["source_asset_digest"],
                    item["source_media_kind"],
                    tuple(UUID(v) for v in item["production_shot_ids"]),
                    tuple(item["production_shot_keys"]),
                    item["generation_segment_id"],
                    item["timeline_start_ms"],
                    item["timeline_duration_ms"],
                    item["source_in_ms"],
                    item["source_out_ms"],
                    FitMode(item["fit_mode"]),
                    AudioBehavior(item["audio_behavior"]),
                    Transition(item["transition_in"]),
                    Transition(item["transition_out"]),
                    item["transition_duration_ms"],
                )
                for item in item_rows
            ),
            tuple(
                CaptionCue(
                    item["ordinal"],
                    item["text"],
                    item["start_ms"],
                    item["end_ms"],
                    CaptionKind(item["caption_kind"]),
                )
                for item in cue_rows
            ),
            tuple(
                OverlayInstruction(
                    item["ordinal"],
                    item["text"],
                    item["start_ms"],
                    item["end_ms"],
                    item["position"],
                    OverlayStyle(item["style_token"]),
                    item["source_provenance"],
                )
                for item in overlay_rows
            ),
            row["timeline_duration_ms"],
            row["semantic_digest"],
            row["created_by_user_id"],
            id=row["id"],
            schema_version=row["schema_version"],
            render_profile=RenderProfile(),
            created_at=row["created_at"],
        )
        job_row = (
            (
                await self._session.execute(
                    select(assembly_jobs).where(assembly_jobs.c.assembly_plan_id == row["id"])
                )
            )
            .mappings()
            .one()
        )
        return AssemblyPlanRecord(plan, self._job(cast(Mapping[str, Any], job_row)))

    async def get_job(self, job_id: UUID) -> AssemblyJob | None:
        row = (
            (await self._session.execute(select(assembly_jobs).where(assembly_jobs.c.id == job_id)))
            .mappings()
            .first()
        )
        return None if row is None else self._job(cast(Mapping[str, Any], row))

    @staticmethod
    def _job(row: Mapping[str, Any]) -> AssemblyJob:
        return AssemblyJob(
            row["tenant_id"],
            row["assembly_plan_id"],
            row["idempotency_key"],
            row["created_by_user_id"],
            id=row["id"],
            status=AssemblyJobStatus(row["status"]),
            failure_code=row["failure_code"],
            output_asset_id=row["output_asset_id"],
            final_creative_id=row["final_creative_id"],
            renderer=row["renderer"],
            renderer_version=row["renderer_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def get_final(
        self, final_id: UUID, *, for_update: bool = False
    ) -> FinalCreativeRecord | None:
        query = select(final_creatives).where(final_creatives.c.id == final_id)
        if for_update:
            await self._session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"final-creative-decision:{final_id}"},
            )
        row = (await self._session.execute(query)).mappings().first()
        if row is None:
            return None
        final = self._final(cast(Mapping[str, Any], row))
        decision_row = (
            (
                await self._session.execute(
                    select(final_creative_decisions)
                    .where(final_creative_decisions.c.final_creative_id == final_id)
                    .order_by(
                        final_creative_decisions.c.created_at.desc(),
                        final_creative_decisions.c.id.desc(),
                    )
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        decision = (
            None
            if decision_row is None
            else FinalCreativeDecision(
                decision_row["tenant_id"],
                decision_row["final_creative_id"],
                decision_row["final_creative_digest"],
                decision_row["output_asset_id"],
                decision_row["output_asset_digest"],
                FinalCreativeDecisionState(decision_row["state"]),
                decision_row["decided_by_user_id"],
                id=decision_row["id"],
                created_at=decision_row["created_at"],
            )
        )
        return FinalCreativeRecord(final, decision)

    @staticmethod
    def _final(row: Mapping[str, Any]) -> FinalCreative:
        return FinalCreative(
            row["tenant_id"],
            row["product_id"],
            row["assembly_plan_id"],
            row["assembly_plan_digest"],
            row["production_plan_id"],
            row["production_plan_digest"],
            row["creative_concept_id"],
            row["creative_concept_digest"],
            row["output_asset_id"],
            row["output_asset_digest"],
            row["renderer"],
            row["renderer_version"],
            row["duration_ms"],
            row["width"],
            row["height"],
            row["fps"],
            row["has_audio"],
            row["source_count"],
            row["semantic_digest"],
            id=row["id"],
            render_profile_key=row["render_profile_key"],
            render_profile_version=row["render_profile_version"],
            created_at=row["created_at"],
        )

    async def add_decision(self, value: FinalCreativeDecision) -> None:
        await self._session.execute(
            insert(final_creative_decisions).values(
                id=value.id,
                tenant_id=value.tenant_id,
                final_creative_id=value.final_creative_id,
                final_creative_digest=value.final_creative_digest,
                output_asset_id=value.output_asset_id,
                output_asset_digest=value.output_asset_digest,
                state=value.state.value,
                decided_by_user_id=value.decided_by_user_id,
                created_at=value.created_at,
            )
        )

    async def update_job(self, value: AssemblyJob, expected: AssemblyJobStatus) -> None:
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(assembly_jobs)
                .where(assembly_jobs.c.id == value.id, assembly_jobs.c.status == expected.value)
                .values(
                    status=value.status.value,
                    failure_code=value.failure_code,
                    output_asset_id=value.output_asset_id,
                    final_creative_id=value.final_creative_id,
                    renderer=value.renderer,
                    renderer_version=value.renderer_version,
                    updated_at=value.updated_at,
                )
            ),
        )
        if result.rowcount != 1:
            raise AssemblyNotReady("AssemblyJob state changed concurrently")

    async def add_final(self, value: FinalCreative) -> None:
        await self._session.execute(
            insert(final_creatives).values(
                **{
                    column.name: getattr(value, column.name)
                    for column in final_creatives.c
                    if hasattr(value, column.name)
                }
            )
        )
