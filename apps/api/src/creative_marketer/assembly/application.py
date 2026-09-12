from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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

from .domain import (
    AssemblyItem,
    AssemblyJob,
    AssemblyNotFound,
    AssemblyNotReady,
    AssemblyPermissionDenied,
    AssemblyPlan,
    AssemblyReadiness,
    AudioBehavior,
    CaptionCue,
    FinalCreative,
    FinalCreativeDecision,
    FinalCreativeDecisionState,
    FitMode,
    OverlayInstruction,
    OverlayStyle,
    RenderProfile,
    SourceKind,
)


@dataclass(frozen=True, slots=True)
class SourceCandidate:
    asset_id: UUID | None
    digest: str | None
    media_kind: str
    status: str
    rights_status: str
    allowed_uses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AssemblyShotInput:
    id: UUID
    key: str
    scene_key: str
    source_strategy: str
    existing_asset_id: UUID | None
    manual_source: SourceCandidate | None


@dataclass(frozen=True, slots=True)
class AssemblySceneInput:
    key: str
    ordinal: int
    duration_ms: int
    voiceover: str | None
    on_screen_text: str | None
    shots: tuple[AssemblyShotInput, ...]


@dataclass(frozen=True, slots=True)
class AssemblySegmentInput:
    id: UUID
    key: str
    media_kind: str
    duration_ms: int | None
    shot_ids: tuple[UUID, ...]
    shot_keys: tuple[str, ...]
    output: SourceCandidate | None
    job_status: str | None


@dataclass(frozen=True, slots=True)
class ProductionAssemblyInput:
    tenant_id: UUID
    product_id: UUID
    production_plan_id: UUID
    production_plan_digest: str
    creative_concept_id: UUID
    creative_concept_digest: str
    production_is_current: bool
    scenes: tuple[AssemblySceneInput, ...]
    segments: tuple[AssemblySegmentInput, ...]
    assets: Mapping[UUID, SourceCandidate]
    cta_text: str | None
    disclaimers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AssemblyReadinessResult:
    status: AssemblyReadiness
    details: tuple[tuple[str, AssemblyReadiness], ...]

    @property
    def ready(self) -> bool:
        return self.status is AssemblyReadiness.READY


@dataclass(frozen=True, slots=True)
class AssemblyPlanRecord:
    plan: AssemblyPlan
    job: AssemblyJob


@dataclass(frozen=True, slots=True)
class FinalCreativeRecord:
    final_creative: FinalCreative
    decision: FinalCreativeDecision | None


class AssemblyRepository(Protocol):
    async def production_input(
        self, production_plan_id: UUID
    ) -> ProductionAssemblyInput | None: ...
    async def production_input_for_shot(self, shot_id: UUID) -> ProductionAssemblyInput | None: ...
    async def bind_manual_source(self, shot_id: UUID, asset_id: UUID, user_id: UUID) -> None: ...
    async def add_plan(self, plan: AssemblyPlan, job: AssemblyJob) -> None: ...
    async def list_plans(self, production_plan_id: UUID) -> tuple[AssemblyPlanRecord, ...]: ...
    async def get_plan(self, plan_id: UUID) -> AssemblyPlanRecord | None: ...
    async def get_job(self, job_id: UUID) -> AssemblyJob | None: ...
    async def get_final(
        self, final_id: UUID, *, for_update: bool = False
    ) -> FinalCreativeRecord | None: ...
    async def add_decision(self, decision: FinalCreativeDecision) -> None: ...


class AssemblyUnitOfWork(Protocol):
    assembly: AssemblyRepository
    audit: AuditWriter
    outbox: OutboxWriter

    async def __aenter__(self) -> AssemblyUnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class AssemblyUnitOfWorkFactory(Protocol):
    def __call__(self, tenant_id: UUID) -> AssemblyUnitOfWork: ...


def _candidate_readiness(candidate: SourceCandidate | None) -> AssemblyReadiness:
    if candidate is None or candidate.asset_id is None:
        return AssemblyReadiness.MISSING_GENERATED_MEDIA
    if candidate.status == "archived":
        return AssemblyReadiness.SOURCE_ARCHIVED
    if candidate.status != "ready":
        return AssemblyReadiness.MISSING_GENERATED_MEDIA
    if candidate.rights_status != "confirmed" or "generation_input" not in candidate.allowed_uses:
        return AssemblyReadiness.RIGHTS_CHANGED
    if candidate.digest is None:
        return AssemblyReadiness.MISSING_GENERATED_MEDIA
    return AssemblyReadiness.READY


def evaluate_readiness(value: ProductionAssemblyInput) -> AssemblyReadinessResult:
    if not value.production_is_current:
        return AssemblyReadinessResult(
            AssemblyReadiness.PRODUCTION_OUTDATED,
            (("production_plan", AssemblyReadiness.PRODUCTION_OUTDATED),),
        )
    segments = {shot_id: segment for segment in value.segments for shot_id in segment.shot_ids}
    details: list[tuple[str, AssemblyReadiness]] = []
    for scene in value.scenes:
        for shot in scene.shots:
            if shot.source_strategy == "USE_EXISTING_ASSET":
                state = _candidate_readiness(
                    value.assets.get(shot.existing_asset_id) if shot.existing_asset_id else None
                )
            elif shot.source_strategy == "MANUAL_CAPTURE":
                state = _candidate_readiness(shot.manual_source)
                if state is AssemblyReadiness.MISSING_GENERATED_MEDIA:
                    state = AssemblyReadiness.MISSING_MANUAL_MEDIA
            else:
                segment = segments.get(shot.id)
                if segment is None or segment.job_status != "SUCCEEDED":
                    state = AssemblyReadiness.MISSING_GENERATED_MEDIA
                else:
                    state = _candidate_readiness(segment.output)
            details.append((shot.key, state))
    priority = (
        AssemblyReadiness.PRODUCTION_OUTDATED,
        AssemblyReadiness.RIGHTS_CHANGED,
        AssemblyReadiness.SOURCE_ARCHIVED,
        AssemblyReadiness.MISSING_MANUAL_MEDIA,
        AssemblyReadiness.MISSING_GENERATED_MEDIA,
    )
    status = next(
        (candidate for candidate in priority if any(state is candidate for _, state in details)),
        AssemblyReadiness.READY,
    )
    return AssemblyReadinessResult(status, tuple(details))


def build_assembly_plan(value: ProductionAssemblyInput, user_id: UUID) -> AssemblyPlan:
    readiness = evaluate_readiness(value)
    if not readiness.ready:
        raise AssemblyNotReady(readiness.status.value)
    segments = {shot_id: segment for segment in value.segments for shot_id in segment.shot_ids}
    handled_segments: set[UUID] = set()
    items: list[AssemblyItem] = []
    captions: list[CaptionCue] = []
    overlays: list[OverlayInstruction] = []
    cursor = 0
    production_cursor = 0
    for scene in sorted(value.scenes, key=lambda item: item.ordinal):
        scene_start = production_cursor
        scene_end = scene_start + scene.duration_ms
        production_cursor = scene_end
        unique_units: list[AssemblyShotInput] = []
        seen_units: set[str] = set()
        for shot in scene.shots:
            segment = segments.get(shot.id)
            unit = f"segment:{segment.id}" if segment else f"shot:{shot.id}"
            if unit not in seen_units:
                unique_units.append(shot)
                seen_units.add(unit)
        base_duration = scene.duration_ms // max(len(unique_units), 1)
        remainder = scene.duration_ms - base_duration * len(unique_units)
        scene_item_index = 0
        for shot in scene.shots:
            segment = segments.get(shot.id)
            if segment and segment.id in handled_segments:
                continue
            scene_item_index += 1
            duration = base_duration + (remainder if scene_item_index == len(unique_units) else 0)
            if segment:
                handled_segments.add(segment.id)
                source = segment.output
                assert (
                    source is not None and source.asset_id is not None and source.digest is not None
                )
                if segment.media_kind == "VIDEO" and segment.duration_ms is not None:
                    duration = segment.duration_ms
                source_kind = (
                    SourceKind.GENERATED_VIDEO
                    if segment.media_kind == "VIDEO"
                    else SourceKind.GENERATED_IMAGE
                )
                shot_ids, shot_keys, segment_id = segment.shot_ids, segment.shot_keys, segment.id
            elif shot.source_strategy == "MANUAL_CAPTURE":
                source = shot.manual_source
                assert (
                    source is not None and source.asset_id is not None and source.digest is not None
                )
                source_kind = SourceKind.MANUAL_VIDEO
                shot_ids, shot_keys, segment_id = (shot.id,), (shot.key,), None
            else:
                source = value.assets[shot.existing_asset_id]  # type: ignore[index]
                assert source.asset_id is not None and source.digest is not None
                source_kind = SourceKind.EXISTING_ASSET
                shot_ids, shot_keys, segment_id = (shot.id,), (shot.key,), None
            items.append(
                AssemblyItem(
                    item_key=f"item_{len(items) + 1}",
                    ordinal=len(items) + 1,
                    source_kind=source_kind,
                    source_asset_id=source.asset_id,
                    source_asset_digest=source.digest,
                    source_media_kind=source.media_kind,
                    production_shot_ids=shot_ids,
                    production_shot_keys=shot_keys,
                    generation_segment_id=segment_id,
                    timeline_start_ms=cursor,
                    timeline_duration_ms=duration,
                    fit_mode=FitMode.CROP_FILL,
                    audio_behavior=AudioBehavior.KEEP,
                )
            )
            cursor += duration
        if scene.voiceover:
            captions.append(CaptionCue(len(captions) + 1, scene.voiceover, scene_start, scene_end))
        if scene.on_screen_text:
            overlays.append(
                OverlayInstruction(
                    len(overlays) + 1,
                    scene.on_screen_text,
                    scene_start,
                    scene_end,
                    "TOP_SAFE" if scene.ordinal == 1 else "CENTER_SAFE",
                    OverlayStyle.HOOK_PRIMARY if scene.ordinal == 1 else OverlayStyle.BODY_SUBTITLE,
                    f"production_scene:{scene.key}",
                )
            )
    intended_duration = sum(scene.duration_ms for scene in value.scenes)
    if abs(cursor - intended_duration) > 250:
        from .domain import AssemblyInvalidTimeline

        raise AssemblyInvalidTimeline("generated segment duration conflicts with ProductionPlan")
    if value.cta_text:
        start = max(0, cursor - min(3000, max(1500, cursor // 5)))
        overlays.append(
            OverlayInstruction(
                len(overlays) + 1,
                value.cta_text,
                start,
                cursor,
                "BOTTOM_SAFE",
                OverlayStyle.CTA_PRIMARY,
                f"creative_concept:{value.creative_concept_id}",
            )
        )
    for text in value.disclaimers:
        overlays.append(
            OverlayInstruction(
                len(overlays) + 1,
                text,
                max(0, cursor - min(3000, cursor)),
                cursor,
                "BOTTOM_SAFE",
                OverlayStyle.DISCLAIMER,
                f"creative_concept:{value.creative_concept_id}",
            )
        )
    profile = RenderProfile()
    semantic = {
        "schema_version": 1,
        "production_plan_digest": value.production_plan_digest,
        "creative_concept_digest": value.creative_concept_digest,
        "render_profile": profile.primitive(),
        "timeline_duration_ms": cursor,
        "items": [item.primitive() for item in items],
        "captions": [cue.primitive() for cue in captions],
        "overlays": [item.primitive() for item in overlays],
        "audio_policy": {"version": 1, "source_audio": "KEEP", "silent_allowed": True},
    }
    return AssemblyPlan(
        value.tenant_id,
        value.product_id,
        value.production_plan_id,
        value.production_plan_digest,
        value.creative_concept_id,
        value.creative_concept_digest,
        tuple(items),
        tuple(captions),
        tuple(overlays),
        cursor,
        canonical_digest(semantic),
        user_id,
        render_profile=profile,
    )


@dataclass(slots=True)
class AssemblyService:
    uow_factory: AssemblyUnitOfWorkFactory

    @staticmethod
    def _require_mutation(context: ExecutionContext) -> None:
        if (
            context.user_id is None
            or context.membership_status is not MembershipStatus.ACTIVE
            or context.membership_role
            not in {
                MembershipRole.OWNER,
                MembershipRole.ADMIN,
            }
        ):
            raise AssemblyPermissionDenied("assembly mutation requires owner or admin")

    async def readiness(
        self, context: ExecutionContext, production_plan_id: UUID
    ) -> AssemblyReadinessResult:
        async with self.uow_factory(context.tenant_id) as uow:
            value = await uow.assembly.production_input(production_plan_id)
            if value is None:
                raise AssemblyNotFound("ProductionPlan not found")
            return evaluate_readiness(value)

    async def bind_manual_source(
        self, context: ExecutionContext, shot_id: UUID, asset_id: UUID
    ) -> AssemblyReadinessResult:
        self._require_mutation(context)
        async with self.uow_factory(context.tenant_id) as uow:
            await uow.assembly.bind_manual_source(shot_id, asset_id, context.user_id)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="assembly.manual_source.bound",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="production_shot",
                    resource_id=str(shot_id),
                    metadata=safe_metadata({"asset_id": str(asset_id)}),
                )
            )
            await uow.commit()
        async with self.uow_factory(context.tenant_id) as uow:
            value = await uow.assembly.production_input_for_shot(shot_id)
            if value is None:
                raise AssemblyNotFound("ProductionShot not found")
            return evaluate_readiness(value)

    async def create_plan(
        self, context: ExecutionContext, production_plan_id: UUID
    ) -> AssemblyPlanRecord:
        self._require_mutation(context)
        async with self.uow_factory(context.tenant_id) as uow:
            source = await uow.assembly.production_input(production_plan_id)
            if source is None:
                raise AssemblyNotFound("ProductionPlan not found")
            plan = build_assembly_plan(source, context.user_id)
            existing = await uow.assembly.list_plans(production_plan_id)
            for record in existing:
                if record.plan.semantic_digest == plan.semantic_digest:
                    return record
            job = AssemblyJob(
                context.tenant_id,
                plan.id,
                f"{context.tenant_id}:{plan.id}:social_vertical_v1@1",
                context.user_id,
            )
            await uow.assembly.add_plan(plan, job)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="assembly.plan.created",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="assembly_plan",
                    resource_id=str(plan.id),
                    after_digest=plan.semantic_digest,
                    metadata=safe_metadata({"production_plan_id": str(production_plan_id)}),
                )
            )
            await uow.outbox.append(
                tenant_event(
                    context,
                    event_type="assembly.plan.created.v1",
                    schema_version=1,
                    aggregate_type="assembly_plan",
                    aggregate_id=plan.id,
                    payload={
                        "assembly_plan_id": str(plan.id),
                        "assembly_job_id": str(job.id),
                        "assembly_plan_digest": plan.semantic_digest,
                    },
                    payload_schema_digest=EventContractRegistry().schema_digest(
                        "assembly.plan.created.v1"
                    ),
                    occurred_at=plan.created_at,
                )
            )
            await uow.commit()
            return AssemblyPlanRecord(plan, job)

    async def list_plans(
        self, context: ExecutionContext, production_plan_id: UUID
    ) -> tuple[AssemblyPlanRecord, ...]:
        async with self.uow_factory(context.tenant_id) as uow:
            return await uow.assembly.list_plans(production_plan_id)

    async def get_plan(self, context: ExecutionContext, plan_id: UUID) -> AssemblyPlanRecord:
        async with self.uow_factory(context.tenant_id) as uow:
            value = await uow.assembly.get_plan(plan_id)
            if value is None:
                raise AssemblyNotFound("AssemblyPlan not found")
            return value

    async def get_job(self, context: ExecutionContext, job_id: UUID) -> AssemblyJob:
        async with self.uow_factory(context.tenant_id) as uow:
            value = await uow.assembly.get_job(job_id)
            if value is None:
                raise AssemblyNotFound("AssemblyJob not found")
            return value

    async def get_final(self, context: ExecutionContext, final_id: UUID) -> FinalCreativeRecord:
        async with self.uow_factory(context.tenant_id) as uow:
            value = await uow.assembly.get_final(final_id)
            if value is None:
                raise AssemblyNotFound("FinalCreative not found")
            return value

    async def decide_final(
        self,
        context: ExecutionContext,
        final_id: UUID,
        state: FinalCreativeDecisionState,
    ) -> FinalCreativeRecord:
        self._require_mutation(context)
        async with self.uow_factory(context.tenant_id) as uow:
            record = await uow.assembly.get_final(final_id, for_update=True)
            if record is None:
                raise AssemblyNotFound("FinalCreative not found")
            if record.decision and record.decision.state is state:
                return record
            final = record.final_creative
            decision = FinalCreativeDecision(
                context.tenant_id,
                final.id,
                final.semantic_digest,
                final.output_asset_id,
                final.output_asset_digest,
                state,
                context.user_id,
            )
            await uow.assembly.add_decision(decision)
            action = (
                "final_creative.approved_for_publishing"
                if state is FinalCreativeDecisionState.APPROVED_FOR_PUBLISHING
                else "final_creative.rejected"
            )
            await uow.audit.append(
                tenant_audit(
                    context,
                    action=action,
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="final_creative",
                    resource_id=str(final.id),
                    after_digest=final.semantic_digest,
                    metadata=safe_metadata({"output_asset_id": str(final.output_asset_id)}),
                )
            )
            if state is FinalCreativeDecisionState.APPROVED_FOR_PUBLISHING:
                await uow.outbox.append(
                    tenant_event(
                        context,
                        event_type="assembly.final_creative.approved_for_publishing.v1",
                        schema_version=1,
                        aggregate_type="final_creative",
                        aggregate_id=final.id,
                        payload={
                            "final_creative_id": str(final.id),
                            "final_creative_digest": final.semantic_digest,
                            "output_asset_id": str(final.output_asset_id),
                            "output_asset_digest": final.output_asset_digest,
                            "decision_id": str(decision.id),
                        },
                        payload_schema_digest=EventContractRegistry().schema_digest(
                            "assembly.final_creative.approved_for_publishing.v1"
                        ),
                        occurred_at=decision.created_at,
                    )
                )
            await uow.commit()
            return FinalCreativeRecord(final, decision)
