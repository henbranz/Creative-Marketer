# mypy: disable-error-code="arg-type,assignment,import-untyped,no-untyped-def,var-annotated"

import asyncio
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from creative_marketer.assembly.application import (
    AssemblySceneInput,
    AssemblySegmentInput,
    AssemblyService,
    AssemblyShotInput,
    ProductionAssemblyInput,
    SourceCandidate,
    build_assembly_plan,
    evaluate_readiness,
)
from creative_marketer.assembly.domain import (
    AssemblyInvalidTimeline,
    AssemblyJob,
    AssemblyJobStatus,
    AssemblyNotFound,
    AssemblyNotReady,
    AssemblyPermissionDenied,
    AssemblyReadiness,
    AssemblyRenderFailed,
    AssemblyRenderTimeout,
    AssemblySource,
    CaptionCue,
    FinalCreative,
    FinalCreativeDecision,
    FinalCreativeDecisionState,
    FitMode,
    OverlayInstruction,
    OverlayStyle,
    RenderProfile,
    Transition,
    final_creative_digest,
)
from creative_marketer.assembly.infrastructure.ffmpeg import FFmpegAssemblyRenderer
from creative_marketer.assembly.rendering import MaterializedSource
from creative_marketer.identity.domain import MembershipRole
from creative_marketer.workflow_orchestration.contracts import (
    FinalCreativeAssemblyWorkflowInput,
    final_creative_assembly_workflow_id,
)
from tests.integration.test_catalog import owner_context

DIGEST = "sha256:" + "a" * 64


def candidate(
    *, status: str = "ready", rights: str = "confirmed", media_kind: str = "video"
) -> SourceCandidate:
    return SourceCandidate(uuid4(), DIGEST, media_kind, status, rights, ("generation_input",))


def production_input(
    *, current: bool = True, manual: SourceCandidate | None = None
) -> ProductionAssemblyInput:
    tenant, product, production, concept = uuid4(), uuid4(), uuid4(), uuid4()
    generated_shot, manual_shot = uuid4(), uuid4()
    output = candidate()
    return ProductionAssemblyInput(
        tenant,
        product,
        production,
        DIGEST,
        concept,
        DIGEST,
        current,
        (
            AssemblySceneInput(
                "hook",
                1,
                2500,
                "שלום world",
                "A {safe} hook",
                (
                    AssemblyShotInput(
                        generated_shot, "generated", "hook", "GENERATE_VIDEO", None, None
                    ),
                ),
            ),
            AssemblySceneInput(
                "cta",
                2,
                2500,
                None,
                None,
                (AssemblyShotInput(manual_shot, "manual", "cta", "MANUAL_CAPTURE", None, manual),),
            ),
        ),
        (
            AssemblySegmentInput(
                uuid4(),
                "generated_video",
                "VIDEO",
                2500,
                (generated_shot,),
                ("generated",),
                output,
                "SUCCEEDED",
            ),
        ),
        {},
        "Try it now",
        ("Terms apply",),
    )


def test_readiness_is_explicit_and_prioritized() -> None:
    missing = production_input()
    readiness = evaluate_readiness(missing)
    assert readiness.status is AssemblyReadiness.MISSING_MANUAL_MEDIA
    assert not readiness.ready
    assert (
        evaluate_readiness(replace(missing, production_is_current=False)).status
        is AssemblyReadiness.PRODUCTION_OUTDATED
    )
    archived = replace(
        missing,
        scenes=(
            missing.scenes[0],
            replace(
                missing.scenes[1],
                shots=(
                    replace(missing.scenes[1].shots[0], manual_source=candidate(status="archived")),
                ),
            ),
        ),
    )
    assert evaluate_readiness(archived).status is AssemblyReadiness.SOURCE_ARCHIVED
    changed = replace(
        missing,
        scenes=(
            missing.scenes[0],
            replace(
                missing.scenes[1],
                shots=(
                    replace(missing.scenes[1].shots[0], manual_source=candidate(rights="unknown")),
                ),
            ),
        ),
    )
    assert evaluate_readiness(changed).status is AssemblyReadiness.RIGHTS_CHANGED


def test_builder_is_deterministic_and_preserves_text_provenance() -> None:
    source = production_input(manual=candidate())
    first = build_assembly_plan(source, uuid4())
    second = build_assembly_plan(source, uuid4())
    assert first.semantic_digest == second.semantic_digest
    assert first.timeline_duration_ms == 5000
    assert [item.timeline_start_ms for item in first.items] == [0, 2500]
    assert [item.production_shot_keys for item in first.items] == [("generated",), ("manual",)]
    assert first.captions[0].text == "שלום world"
    assert {item.text for item in first.overlays} == {"A {safe} hook", "Try it now", "Terms apply"}
    assert first.render_profile == RenderProfile()
    with pytest.raises(AssemblyNotReady):
        build_assembly_plan(production_input(), uuid4())
    with pytest.raises(AssemblyInvalidTimeline, match="duration conflicts"):
        build_assembly_plan(
            replace(source, segments=(replace(source.segments[0], duration_ms=4000),)), uuid4()
        )


def test_multi_shot_segment_is_emitted_once_across_scene_boundaries() -> None:
    source = production_input(manual=candidate())
    first_shot = source.scenes[0].shots[0]
    second_shot = source.scenes[1].shots[0]
    segment = replace(
        source.segments[0],
        duration_ms=5000,
        shot_ids=(first_shot.id, second_shot.id),
        shot_keys=(first_shot.key, second_shot.key),
    )
    source = replace(
        source,
        scenes=(source.scenes[0], replace(source.scenes[1], voiceover="Second scene")),
        segments=(segment,),
    )

    plan = build_assembly_plan(source, uuid4())

    assert len(plan.items) == 1
    assert plan.items[0].production_shot_ids == (first_shot.id, second_shot.id)
    assert [(cue.start_ms, cue.end_ms) for cue in plan.captions] == [(0, 2500), (2500, 5000)]


def test_plan_digest_and_job_lifecycle_fail_closed() -> None:
    plan = build_assembly_plan(production_input(manual=candidate()), uuid4())
    with pytest.raises(AssemblyInvalidTimeline, match="semantic digest"):
        replace(plan, semantic_digest="sha256:" + "b" * 64)
    job = AssemblyJob(plan.tenant_id, plan.id, "stable-key", plan.created_by_user_id)
    rendering = job.transition(AssemblyJobStatus.RENDERING)
    importing = rendering.transition(AssemblyJobStatus.IMPORTING)
    assert (
        importing.transition(
            AssemblyJobStatus.SUCCEEDED,
            output_asset_id=uuid4(),
            final_creative_id=uuid4(),
            renderer="ffmpeg",
            renderer_version="7.1",
        ).status
        is AssemblyJobStatus.SUCCEEDED
    )
    with pytest.raises(ValueError, match="transition"):
        job.transition(AssemblyJobStatus.SUCCEEDED)


def test_final_creative_and_decision_bind_exact_digests() -> None:
    plan = build_assembly_plan(production_input(manual=candidate()), uuid4())
    output_id = uuid4()
    semantic = final_creative_digest(
        assembly_plan_digest=plan.semantic_digest,
        output_asset_digest=DIGEST,
        renderer="ffmpeg",
        renderer_version="7.1",
    )
    final = FinalCreative(
        plan.tenant_id,
        plan.product_id,
        plan.id,
        plan.semantic_digest,
        plan.production_plan_id,
        plan.production_plan_digest,
        plan.creative_concept_id,
        plan.creative_concept_digest,
        output_id,
        DIGEST,
        "ffmpeg",
        "7.1",
        5000,
        1080,
        1920,
        24,
        False,
        2,
        semantic,
    )
    decision = FinalCreativeDecision(
        plan.tenant_id,
        final.id,
        final.semantic_digest,
        output_id,
        DIGEST,
        FinalCreativeDecisionState.APPROVED_FOR_PUBLISHING,
        uuid4(),
    )
    assert decision.output_asset_id == final.output_asset_id
    with pytest.raises(ValueError, match="digest provenance"):
        replace(final, output_asset_digest="bad")


def test_assembly_domain_rejects_malformed_timeline_boundaries() -> None:
    plan = build_assembly_plan(production_input(manual=candidate()), uuid4())
    item, cue, overlay = plan.items[0], plan.captions[0], plan.overlays[0]
    with pytest.raises(ValueError, match="unsupported render profile"):
        RenderProfile(width=1)
    with pytest.raises(ValueError, match="source identity"):
        AssemblySource(uuid4(), "bad", "IMAGE", "image/png", 1, "object")
    for malformed in (
        lambda: replace(item, item_key="INVALID"),
        lambda: replace(item, ordinal=0),
        lambda: replace(item, production_shot_ids=()),
        lambda: replace(item, source_asset_digest="bad"),
        lambda: replace(item, transition_in=Transition.CROSSFADE, transition_duration_ms=1),
        lambda: replace(item, transition_duration_ms=1),
        lambda: replace(cue, ordinal=0),
        lambda: CaptionCue(1, "x" * 501, 0, 1),
        lambda: replace(overlay, position="OUTSIDE"),
        lambda: OverlayInstruction(
            1, "legal", 0, 1000, "BOTTOM_SAFE", OverlayStyle.DISCLAIMER, "brief"
        ),
        lambda: replace(plan, items=()),
        lambda: replace(plan, items=(replace(plan.items[0], ordinal=2), *plan.items[1:])),
        lambda: replace(plan, items=(replace(plan.items[0], timeline_start_ms=1), *plan.items[1:])),
        lambda: replace(plan, timeline_duration_ms=plan.timeline_duration_ms + 1),
        lambda: replace(
            plan,
            items=(
                plan.items[0],
                replace(plan.items[1], production_shot_ids=plan.items[0].production_shot_ids),
            ),
        ),
        lambda: replace(plan, production_plan_digest="bad"),
    ):
        with pytest.raises(AssemblyInvalidTimeline):
            malformed()

    final = FinalCreative(
        plan.tenant_id,
        plan.product_id,
        plan.id,
        plan.semantic_digest,
        plan.production_plan_id,
        plan.production_plan_digest,
        plan.creative_concept_id,
        plan.creative_concept_digest,
        uuid4(),
        DIGEST,
        "ffmpeg",
        "7.1",
        plan.timeline_duration_ms,
        1080,
        1920,
        24,
        True,
        len(plan.items),
        final_creative_digest(
            assembly_plan_digest=plan.semantic_digest,
            output_asset_digest=DIGEST,
            renderer="ffmpeg",
            renderer_version="7.1",
        ),
    )
    with pytest.raises(ValueError, match="render profile"):
        replace(final, width=1)
    with pytest.raises(ValueError, match="semantic digest"):
        replace(final, renderer_version="other")
    with pytest.raises(ValueError, match="decision binding"):
        FinalCreativeDecision(
            plan.tenant_id,
            final.id,
            "bad",
            final.output_asset_id,
            DIGEST,
            FinalCreativeDecisionState.REJECTED,
            uuid4(),
        )


def test_renderer_helpers_escape_content_and_never_construct_network_inputs(tmp_path: Path) -> None:
    renderer = FFmpegAssemblyRenderer()
    plan = build_assembly_plan(production_input(manual=candidate()), uuid4())
    target = tmp_path / "overlay's.ass"
    renderer._write_ass(plan, target)
    rendered = target.read_text()
    assert "Noto Sans" in rendered and "שלום world" in rendered
    assert "A \\{safe\\} hook" in rendered
    assert renderer._filter_path(target).endswith("overlay\\'s.ass")
    assert renderer._fit_filter(plan.items[0].fit_mode, 1080, 1920).startswith("scale=1080:1920")
    assert renderer._timeout(1000) == 30.0 and renderer._timeout(100_000) == 600.0


@pytest.mark.ffmpeg
@pytest.mark.asyncio
async def test_ffmpeg_mixed_media_unicode_audio_render_is_browser_playable(
    tmp_path: Path,
) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg integration binaries are unavailable")
    image = tmp_path / "source.png"
    video = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x320",
            "-frames:v",
            "1",
            str(image),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=orange:s=320x240:r=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "2.5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(video),
        ],
        check=True,
    )
    source = production_input(manual=candidate())
    image_candidate = candidate(media_kind="image")
    source = replace(
        source,
        segments=(replace(source.segments[0], media_kind="IMAGE", output=image_candidate),),
    )
    plan = build_assembly_plan(source, uuid4())
    renderer = FFmpegAssemblyRenderer()
    manual_source = source.scenes[1].shots[0].manual_source
    assert manual_source is not None

    result = await renderer.render(
        plan,
        (
            MaterializedSource(str(image_candidate.asset_id), str(image), "IMAGE", False),
            MaterializedSource(str(manual_source.asset_id), str(video), "VIDEO", True),
        ),
        str(tmp_path),
    )

    assert (result.width, result.height, result.fps) == (1080, 1920, 24)
    assert result.has_audio and result.duration_ms == pytest.approx(5000, abs=250)
    with Path(result.path).open("rb") as handle:
        assert handle.read(8)[4:8] == b"ftyp"


@pytest.mark.asyncio
async def test_ffmpeg_renderer_builds_bounded_commands_and_inspects_output(
    tmp_path: Path, monkeypatch
) -> None:
    source = production_input(manual=candidate())
    image_candidate = candidate(media_kind="image")
    source = replace(
        source,
        segments=(replace(source.segments[0], media_kind="IMAGE", output=image_candidate),),
    )
    plan = build_assembly_plan(source, uuid4())
    image, video = tmp_path / "source.png", tmp_path / "source.mp4"
    image.write_bytes(b"image")
    video.write_bytes(b"video")
    renderer = FFmpegAssemblyRenderer()
    calls = []

    async def fake_run(argv, *, timeout):
        calls.append((argv, timeout))
        if argv[0] == "ffprobe":
            if "-select_streams" in argv:
                return b'{"streams":[{"index":1}]}'
            return json.dumps(
                {
                    "streams": [
                        {
                            "codec_type": "video",
                            "codec_name": "h264",
                            "pix_fmt": "yuv420p",
                            "width": 1080,
                            "height": 1920,
                            "avg_frame_rate": "24/1",
                        },
                        {"codec_type": "audio"},
                    ],
                    "format": {"duration": str(plan.timeline_duration_ms / 1000)},
                }
            ).encode()
        if "-version" in argv:
            return b"ffmpeg version 7.1.5-0+deb13u1 Copyright"
        output = Path(argv[-1])
        if output.suffix == ".mp4":
            output.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"0" * 32)
        return b""

    monkeypatch.setattr(renderer, "_run", fake_run)
    assert await renderer.source_has_audio(str(video), str(tmp_path))
    manual_source = source.scenes[1].shots[0].manual_source
    assert manual_source is not None
    result = await renderer.render(
        plan,
        (
            MaterializedSource(str(image_candidate.asset_id), str(image), "IMAGE", False),
            MaterializedSource(str(manual_source.asset_id), str(video), "VIDEO", True),
        ),
        str(tmp_path),
    )
    assert result.has_audio and result.renderer_version.startswith("7.1.5")
    assert any("anullsrc=r=48000:cl=stereo" in call[0] for call in calls)
    assert "Dialogue:" in (tmp_path / "overlays.ass").read_text()
    assert renderer._fit_filter(FitMode.FIT_WITH_BACKGROUND, 1080, 1920).startswith("scale")
    assert renderer._filter_path(Path("a:b'c")).count("\\") >= 2
    assert renderer._ass_text("{x}\n\\") == "\\{x\\}\\N\\\\"
    assert renderer._ass_time(3_661_230) == "1:01:01.23"

    with pytest.raises(AssemblyRenderFailed):
        await renderer.source_has_audio(str(tmp_path.parent / "outside.mp4"), str(tmp_path))
    monkeypatch.setattr(renderer, "_run", AsyncMock(return_value=b"not-json"))
    with pytest.raises(AssemblyRenderFailed):
        await renderer.source_has_audio(str(video), str(tmp_path))


@pytest.mark.asyncio
async def test_ffmpeg_process_boundary_maps_spawn_timeout_and_exit(monkeypatch) -> None:
    renderer = FFmpegAssemblyRenderer()
    with pytest.raises(AssemblyRenderFailed):
        await renderer._run((), timeout=1)

    async def unavailable(*_args, **_kwargs):
        raise OSError("missing")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unavailable)
    with pytest.raises(RuntimeError, match="unavailable"):
        await renderer._run(("ffmpeg", "-version"), timeout=1)

    class Process:
        returncode = 1
        killed = False

        async def communicate(self):
            return b"", b"bounded diagnostic"

        def kill(self):
            self.killed = True

        async def wait(self):
            return None

    process = Process()

    async def spawned(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawned)
    with pytest.raises(AssemblyRenderFailed, match="ASSEMBLY_RENDER_FAILED"):
        await renderer._run(("ffmpeg", "bad"), timeout=1)

    async def timeout(awaitable, timeout):
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", timeout)
    with pytest.raises(AssemblyRenderTimeout):
        await renderer._run(("ffmpeg", "slow"), timeout=1)
    assert process.killed


def test_temporal_locator_contains_only_stable_ids() -> None:
    request = FinalCreativeAssemblyWorkflowInput(
        str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())
    )
    assert final_creative_assembly_workflow_id(request) == (
        f"tenant/{request.tenant_id}/assembly-plan/{request.assembly_plan_id}"
    )
    with pytest.raises(ValueError):
        FinalCreativeAssemblyWorkflowInput("bad", str(uuid4()), str(uuid4()), str(uuid4()))


class MemoryAssemblyRepository:
    def __init__(self, source: ProductionAssemblyInput) -> None:
        self.source = source
        self.records = []
        self.jobs = {}
        self.finals = {}
        self.bound = None
        self.decisions = []

    async def production_input(self, production_plan_id):
        return self.source if production_plan_id == self.source.production_plan_id else None

    async def production_input_for_shot(self, shot_id):
        return (
            self.source
            if any(shot.id == shot_id for scene in self.source.scenes for shot in scene.shots)
            else None
        )

    async def bind_manual_source(self, shot_id, asset_id, user_id):
        self.bound = (shot_id, asset_id, user_id)

    async def add_plan(self, plan, job):
        from creative_marketer.assembly.application import AssemblyPlanRecord

        record = AssemblyPlanRecord(plan, job)
        self.records.append(record)
        self.jobs[job.id] = job

    async def list_plans(self, production_plan_id):
        return tuple(
            record
            for record in self.records
            if record.plan.production_plan_id == production_plan_id
        )

    async def get_plan(self, plan_id):
        return next((record for record in self.records if record.plan.id == plan_id), None)

    async def get_job(self, job_id):
        return self.jobs.get(job_id)

    async def get_final(self, final_id, *, for_update=False):
        return self.finals.get(final_id)

    async def add_decision(self, decision):
        from creative_marketer.assembly.application import FinalCreativeRecord

        self.decisions.append(decision)
        final = self.finals[decision.final_creative_id].final_creative
        self.finals[decision.final_creative_id] = FinalCreativeRecord(final, decision)


class MemoryAssemblyUnitOfWork:
    def __init__(self, repository: MemoryAssemblyRepository) -> None:
        self.assembly = repository
        self.audit = SimpleNamespace(records=[])
        self.audit.append = self._append_audit
        self.outbox = SimpleNamespace(events=[])
        self.outbox.append = self._append_event
        self.commits = 0

    async def _append_audit(self, value):
        self.audit.records.append(value)

    async def _append_event(self, value):
        self.outbox.events.append(value)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_assembly_service_is_idempotent_audited_and_fail_closed() -> None:
    source = production_input(manual=candidate())
    context = owner_context(source.tenant_id, uuid4())
    repository = MemoryAssemblyRepository(source)
    uow = MemoryAssemblyUnitOfWork(repository)
    service = AssemblyService(lambda _tenant_id: uow)

    with pytest.raises(AssemblyPermissionDenied):
        await service.create_plan(
            replace(context, membership_role=MembershipRole.MEMBER), source.production_plan_id
        )

    assert (await service.readiness(context, source.production_plan_id)).ready
    with pytest.raises(AssemblyNotFound):
        await service.readiness(context, uuid4())
    shot_id = source.scenes[1].shots[0].id
    asset_id = uuid4()
    assert (await service.bind_manual_source(context, shot_id, asset_id)).ready
    assert repository.bound == (shot_id, asset_id, context.user_id)
    with pytest.raises(AssemblyNotFound):
        await service.bind_manual_source(context, uuid4(), asset_id)

    created = await service.create_plan(context, source.production_plan_id)
    assert await service.create_plan(context, source.production_plan_id) == created
    assert await service.list_plans(context, source.production_plan_id) == (created,)
    assert await service.get_plan(context, created.plan.id) == created
    assert await service.get_job(context, created.job.id) == created.job
    with pytest.raises(AssemblyNotFound):
        await service.create_plan(context, uuid4())
    with pytest.raises(AssemblyNotFound):
        await service.get_plan(context, uuid4())
    with pytest.raises(AssemblyNotFound):
        await service.get_job(context, uuid4())

    output_id = uuid4()
    final = FinalCreative(
        source.tenant_id,
        source.product_id,
        created.plan.id,
        created.plan.semantic_digest,
        source.production_plan_id,
        source.production_plan_digest,
        source.creative_concept_id,
        source.creative_concept_digest,
        output_id,
        DIGEST,
        "ffmpeg",
        "7.1",
        5000,
        1080,
        1920,
        24,
        True,
        2,
        final_creative_digest(
            assembly_plan_digest=created.plan.semantic_digest,
            output_asset_digest=DIGEST,
            renderer="ffmpeg",
            renderer_version="7.1",
        ),
    )
    from creative_marketer.assembly.application import FinalCreativeRecord

    repository.finals[final.id] = FinalCreativeRecord(final, None)
    assert (await service.get_final(context, final.id)).final_creative == final
    approved = await service.decide_final(
        context, final.id, FinalCreativeDecisionState.APPROVED_FOR_PUBLISHING
    )
    assert approved.decision is not None
    assert (
        await service.decide_final(
            context, final.id, FinalCreativeDecisionState.APPROVED_FOR_PUBLISHING
        )
        == approved
    )
    rejected = await service.decide_final(context, final.id, FinalCreativeDecisionState.REJECTED)
    assert rejected.decision is not None and len(repository.decisions) == 2
    with pytest.raises(AssemblyNotFound):
        await service.get_final(context, uuid4())
    with pytest.raises(AssemblyNotFound):
        await service.decide_final(context, uuid4(), FinalCreativeDecisionState.REJECTED)
    assert len(uow.audit.records) == 5 and len(uow.outbox.events) == 2
