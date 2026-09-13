# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,index,unused-ignore"

import json
from dataclasses import replace
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from uuid import uuid4

import httpx
import pytest
from openai import APIStatusError, APITimeoutError

import creative_marketer.production.infrastructure.seedance as seedance_module
from creative_marketer.agent_runtime.application import (
    AgentCapabilityRegistry,
    AgentCapabilityUnavailable,
    ProducerCapability,
    default_capability_registry,
)
from creative_marketer.agent_runtime.domain import ModelContext, canonical_digest
from creative_marketer.creative.domain import (
    ApprovedCreativeConcept,
    CreativeConcept,
    CreativeConceptDecision,
    CreativeConceptSet,
    CreativeDecisionState,
)
from creative_marketer.production.application import (
    ImageReservationPricing,
    MediaRouter,
    ProductionBudgetGuard,
    SeedancePricing,
    build_production_context,
    initial_media_router,
    initial_producer_route,
    load_production_plan_schema,
    production_context_from_payload,
    select_visual_assets,
    validate_production_plan,
)
from creative_marketer.production.domain import (
    MAX_VISUAL_REFERENCES,
    AssetLineage,
    FrozenAssetReference,
    GenerationJob,
    GenerationJobStatus,
    InvalidProductionPlan,
    MediaKind,
    ProductionCost,
    ProductionCreativeRefreshRequired,
    ProductionPermissionDenied,
    ProductionPlanDecision,
    ProductionPlanDecisionState,
    ProductionPlanningRequest,
)
from creative_marketer.production.infrastructure.openai_images import OpenAIImageProvider
from creative_marketer.production.infrastructure.seedance import (
    SeedanceMediaProvider,
    UrllibJsonHttpTransport,
)
from creative_marketer.production.media import (
    ImageGenerationRequest,
    InvalidMediaResult,
    MaterializedReference,
    MediaProviderError,
    MediaProviderOutcomeUnknown,
    ProviderGenerationState,
    VideoGenerationRequest,
    assert_reference_limits,
    validate_image_result,
    validate_video_result,
)
from creative_marketer.production.tool_contracts import media_tool_contracts
from creative_marketer.workflow_orchestration.contracts import MediaProductionWorkflowInput

DIGEST = "sha256:" + "a" * 64


def manifest_item(role: str = "product_hero", *, allowed: bool = True) -> dict[str, object]:
    return {
        "asset_id": str(uuid4()),
        "status": "ready",
        "kind": "image",
        "role": role,
        "rights_status": "confirmed",
        "allowed_uses": ["generation_input"] if allowed else ["internal_analysis"],
        "digest": DIGEST,
    }


def approved() -> ApprovedCreativeConcept:
    tenant, product, run, set_id = uuid4(), uuid4(), uuid4(), uuid4()
    product_snapshot_id, research_snapshot_id = uuid4(), uuid4()
    payload = {"title": "Concept", "scenes": [{"scene_key": "scene_one"}]}
    concept = CreativeConcept(
        tenant, product, set_id, "concept", 1, payload, canonical_digest(payload)
    )
    semantic = {
        "schema_version": 1,
        "product_snapshot_id": str(product_snapshot_id),
        "product_snapshot_digest": DIGEST,
        "research_snapshot_id": str(research_snapshot_id),
        "research_snapshot_digest": DIGEST,
        "input_context_digest": DIGEST,
        "concepts": [payload, payload, payload],
    }
    concept_set = CreativeConceptSet(
        tenant,
        product,
        run,
        product_snapshot_id,
        DIGEST,
        research_snapshot_id,
        DIGEST,
        DIGEST,
        (
            concept,
            replace(concept, id=uuid4(), concept_key="two", ordinal=2),
            replace(concept, id=uuid4(), concept_key="three", ordinal=3),
        ),
        canonical_digest(semantic),
        id=set_id,
    )
    decision = CreativeConceptDecision(
        tenant,
        product,
        concept.id,
        CreativeDecisionState.APPROVED_FOR_PRODUCTION,
        uuid4(),
    )
    return ApprovedCreativeConcept(concept, concept_set, decision)


def context():
    value = approved()
    return build_production_context(
        value,
        current_product_snapshot_id=value.concept_set.product_snapshot_id,
        current_product_snapshot_digest=DIGEST,
        current_research_snapshot_id=value.concept_set.research_snapshot_id,
        current_research_snapshot_digest=DIGEST,
        asset_manifest=[manifest_item(), manifest_item("logo"), manifest_item(allowed=False)],
    )


def valid_output(asset_id: str) -> dict[str, object]:
    shot_fields = {
        "visual_objective": "Show product",
        "subject": "Product",
        "environment": "Studio",
        "composition": "Centered",
        "framing": "Close",
        "lens_intent": "Natural",
        "camera_position": "Eye level",
        "camera_motion": "Slow push",
        "lighting": "Soft",
        "action": "Rotate",
        "continuity_requirements": [],
        "product_preservation_constraints": ["Keep label"],
        "audio_intent": None,
    }
    image_spec = {
        "subject": "Product",
        "environment": "Studio",
        "composition": "Centered",
        "camera_framing": "Close",
        "lighting": "Soft",
        "style": "Editorial",
        "product_preservation_constraints": ["Keep label"],
        "reference_asset_ids": [asset_id],
        "aspect_ratio": "9:16",
        "quality_intent": "HIGH",
        "negative_constraints": ["No distortion"],
    }
    return {
        "strategy": "Preserve the approved hook and reveal the product.",
        "format": {"kind": "SHORT_FORM_VERTICAL_VIDEO", "aspect_ratio": "9:16"},
        "scenes": [
            {
                "scene_key": "scene_one",
                "ordinal": 1,
                "purpose": "Hook",
                "duration_seconds": 12,
                "message": "Product value",
                "voiceover": None,
                "on_screen_text": "Look",
                "shots": [
                    {
                        **shot_fields,
                        "shot_key": "shot_one",
                        "scene_key": "scene_one",
                        "ordinal": 1,
                        "source_strategy": "GENERATE_IMAGE",
                        "existing_asset_id": None,
                        "image_generation_spec": image_spec,
                    },
                    {
                        **shot_fields,
                        "shot_key": "shot_two",
                        "scene_key": "scene_one",
                        "ordinal": 2,
                        "source_strategy": "GENERATE_VIDEO",
                        "existing_asset_id": None,
                        "image_generation_spec": None,
                    },
                ],
            }
        ],
        "generation_segments": [
            {
                "segment_key": "image_one",
                "shot_keys": ["shot_one"],
                "media_kind": "IMAGE",
                "duration_seconds": None,
                "continuity": [],
                "reference_asset_ids": [asset_id],
                "generation_spec": {
                    "subject": "Product",
                    "environment": "Studio",
                    "composition": "Centered",
                    "camera_motion": "Static",
                    "lighting": "Soft",
                    "action": "Still",
                    "product_preservation_constraints": ["Keep label"],
                    "negative_constraints": ["No distortion"],
                    "generate_audio": False,
                    "resolution": "720p",
                    "aspect_ratio": "9:16",
                },
            },
            {
                "segment_key": "video_one",
                "shot_keys": ["shot_two"],
                "media_kind": "VIDEO",
                "duration_seconds": 12,
                "continuity": ["Same product"],
                "reference_asset_ids": [asset_id],
                "generation_spec": {
                    "subject": "Product",
                    "environment": "Studio",
                    "composition": "Centered",
                    "camera_motion": "Slow push",
                    "lighting": "Soft",
                    "action": "Rotate",
                    "product_preservation_constraints": ["Keep label"],
                    "negative_constraints": ["No distortion"],
                    "generate_audio": True,
                    "resolution": "720p",
                    "aspect_ratio": "9:16",
                },
            },
        ],
        "required_assets": ["Voiceover"],
    }


def test_routes_pricing_contracts_and_selection() -> None:
    producer = initial_producer_route()
    assert (producer.profile_key, producer.model, producer.reasoning_effort) == (
        "production_deep",
        "gpt-6-astra",
        "high",
    )
    assert producer.route_version == "openai-gpt-6-astra-production-2026-09-13"
    assert producer.pricing.version == "openai-gpt-6-astra-2026-09-13"
    assert producer.max_output_tokens == 12_000
    router = initial_media_router()
    assert router.resolve("production_video").model == "dreamina-seedance-2-5-260628"
    assert router.resolve("production_image").model == "gpt-image-2.5-sunburst-2026-09-08"
    with pytest.raises(InvalidProductionPlan):
        MediaRouter(()).resolve("missing")
    duplicate = initial_media_router().resolve("production_video")
    with pytest.raises(ValueError, match="unique"):
        MediaRouter((duplicate, duplicate))
    pricing = SeedancePricing()
    assert pricing.cost(output_duration_seconds=10, resolution="720p") == Decimal("4.620750")
    assert pricing.cost(
        output_duration_seconds=10, input_video_duration_seconds=5, resolution="480p"
    ) == Decimal("1.845270")
    with pytest.raises(ValueError):
        pricing.cost(output_duration_seconds=3, resolution="720p")
    with pytest.raises(ValueError, match="dimensions"):
        pricing.cost(output_duration_seconds=4, resolution="1080p")
    assert ImageReservationPricing().reserve("HIGH") == Decimal("0.40")
    assert ImageReservationPricing().actual_cost(
        {
            "text_input_tokens": 1_000,
            "cached_text_input_tokens": 500,
            "image_input_tokens": 2_000,
            "cached_image_input_tokens": 1_000,
            "image_output_tokens": 3_000,
        }
    ) == Decimal("0.103125")
    assert ImageReservationPricing().actual_cost({"output_tokens": 3}) is None
    with pytest.raises(ValueError):
        ImageReservationPricing().reserve("AUTO")
    assert len(load_production_plan_schema()["$defs"]) > 1  # type: ignore[arg-type]
    items = [manifest_item("logo"), manifest_item("product_hero"), manifest_item(allowed=False)]
    assert [item.role for item in select_visual_assets(items)] == ["product_hero", "logo"]
    assert [item.tool_key for item in media_tool_contracts()] == [
        "media.image.generate",
        "media.video.generate.start",
        "media.video.generate.status",
        "media.video.generate.import",
    ]


def test_context_freshness_round_trip_and_valid_plan() -> None:
    planning = context()
    payload = planning.semantic_content()
    payload["concept_scene_keys"] = ["scene_one"]
    payload["production_request"] = payload.pop("request")
    assert production_context_from_payload(payload, planning.context_digest) == planning
    with pytest.raises(InvalidProductionPlan, match="malformed"):
        production_context_from_payload({**payload, "selected_assets": "not-a-list"}, DIGEST)
    plan = validate_production_plan(
        valid_output(str(planning.selected_assets[0].asset_id)),
        tenant_id=uuid4(),
        product_id=uuid4(),
        agent_run_id=uuid4(),
        context=planning,
        concept_scene_keys=["scene_one"],
    )
    assert plan.cost.estimated_max_video_cost == Decimal("5.544900")
    assert plan.cost.estimated_max_image_cost == Decimal("0.40")
    assert plan.cost.estimated_total_cost == Decimal("5.944900")
    decision = ProductionPlanDecision(
        plan.tenant_id,
        plan.id,
        plan.semantic_digest,
        ProductionPlanDecisionState.APPROVED_FOR_GENERATION,
        uuid4(),
        "video-v1",
        "image-v1",
        plan.cost.video_pricing_version,
        plan.cost.image_pricing_version,
        plan.cost.estimated_total_cost,
        "USD",
    )
    assert decision.estimated_max_cost == plan.cost.estimated_total_cost
    with pytest.raises(ProductionCreativeRefreshRequired):
        value = approved()
        build_production_context(
            value,
            current_product_snapshot_id=uuid4(),
            current_product_snapshot_digest=DIGEST,
            current_research_snapshot_id=value.concept_set.research_snapshot_id,
            current_research_snapshot_digest=DIGEST,
            asset_manifest=[],
        )


@pytest.mark.parametrize("mutation", ["scene", "asset", "duration", "provider", "strategy"])
def test_plan_validation_fails_closed(mutation: str) -> None:
    planning = context()
    output = valid_output(str(planning.selected_assets[0].asset_id))
    if mutation == "scene":
        output["scenes"][0]["scene_key"] = "changed"  # type: ignore[index]
    elif mutation == "asset":
        output["generation_segments"][0]["reference_asset_ids"] = [str(uuid4())]  # type: ignore[index]
    elif mutation == "duration":
        output["generation_segments"][1]["duration_seconds"] = 3  # type: ignore[index]
    elif mutation == "provider":
        output["generation_segments"][1]["generation_spec"]["provider"] = "byteplus"  # type: ignore[index]
    else:
        output["scenes"][0]["shots"][0]["source_strategy"] = "MANUAL_CAPTURE"  # type: ignore[index]
    with pytest.raises(InvalidProductionPlan):
        validate_production_plan(
            output,
            tenant_id=uuid4(),
            product_id=uuid4(),
            agent_run_id=uuid4(),
            context=planning,
            concept_scene_keys=["scene_one"],
        )


def test_existing_asset_strategy_binding_fails_closed() -> None:
    planning = context()
    output = valid_output(str(planning.selected_assets[0].asset_id))
    shot = output["scenes"][0]["shots"][0]  # type: ignore[index]
    shot["source_strategy"] = "USE_EXISTING_ASSET"
    shot["image_generation_spec"] = None
    with pytest.raises(InvalidProductionPlan, match="authorized frozen Asset"):
        validate_production_plan(
            output,
            tenant_id=uuid4(),
            product_id=uuid4(),
            agent_run_id=uuid4(),
            context=planning,
            concept_scene_keys=("scene_one",),
        )
    output = valid_output(str(planning.selected_assets[0].asset_id))
    output["scenes"][0]["shots"][1]["existing_asset_id"] = str(  # type: ignore[index]
        planning.selected_assets[0].asset_id
    )
    with pytest.raises(InvalidProductionPlan, match="only existing-asset"):
        validate_production_plan(
            output,
            tenant_id=uuid4(),
            product_id=uuid4(),
            agent_run_id=uuid4(),
            context=planning,
            concept_scene_keys=("scene_one",),
        )


@pytest.mark.asyncio
async def test_production_budget_guard_rejects_negative_and_denied_reservations() -> None:
    class Ledger:
        def __init__(self, allowed: bool) -> None:
            self.allowed = allowed

        async def reserve(self, *_args) -> bool:
            return self.allowed

        async def settle(self, *_args) -> None:
            return None

    for amount, allowed in ((Decimal("-1"), True), (Decimal("1"), False)):
        with pytest.raises(ProductionPermissionDenied):
            await ProductionBudgetGuard(Ledger(allowed)).reserve(uuid4(), uuid4(), amount, "USD")


def test_generation_job_unknown_and_lineage() -> None:
    reference = context().selected_assets[0]
    job = GenerationJob(
        uuid4(),
        uuid4(),
        "video_one",
        MediaKind.VIDEO,
        "production_video",
        "r1",
        "byteplus",
        "seedance",
        "p1",
        DIGEST,
        (reference,),
        Decimal("1.2"),
        "USD",
    )
    unknown = (
        job.transition(GenerationJobStatus.READY)
        .transition(GenerationJobStatus.STARTING)
        .transition(GenerationJobStatus.OUTCOME_UNKNOWN)
    )
    assert unknown.unknown_cost == Decimal("1.2")
    with pytest.raises(ValueError):
        unknown.transition(GenerationJobStatus.READY)
    assert (
        AssetLineage(job.tenant_id, uuid4(), uuid4(), "REFERENCE_IMAGE").relationship_type
        == "REFERENCE_IMAGE"
    )
    with pytest.raises(ValueError):
        AssetLineage(job.tenant_id, job.id, job.id, "DERIVED_FROM")


def test_production_domain_invariants_fail_closed() -> None:
    planning = context()
    plan = validate_production_plan(
        valid_output(str(planning.selected_assets[0].asset_id)),
        tenant_id=uuid4(),
        product_id=uuid4(),
        agent_run_id=uuid4(),
        context=planning,
        concept_scene_keys=("scene_one",),
    )
    reference = planning.selected_assets[0]
    with pytest.raises(ValueError):
        FrozenAssetReference(uuid4(), "bad", "image", "logo", ())
    with pytest.raises(ValueError):
        ProductionPlanningRequest(aspect_ratio="1:1")
    with pytest.raises(ValueError, match="duplicate"):
        replace(planning, selected_assets=(reference, reference))
    many_assets = tuple(
        replace(reference, asset_id=uuid4()) for _ in range(MAX_VISUAL_REFERENCES + 1)
    )
    with pytest.raises(ValueError, match="bounded maximum"):
        replace(planning, selected_assets=many_assets)
    with pytest.raises(ValueError, match="digest"):
        replace(planning, context_digest=DIGEST)

    image_shot, video_shot = plan.scenes[0].shots
    with pytest.raises(InvalidProductionPlan, match="identity"):
        replace(image_shot, shot_key="INVALID")
    with pytest.raises(InvalidProductionPlan, match="provider-neutral"):
        replace(image_shot, specification={"provider": "openai"})
    with pytest.raises(InvalidProductionPlan, match="scene key"):
        replace(plan.scenes[0], scene_key="INVALID")
    with pytest.raises(InvalidProductionPlan, match="duration and shots"):
        replace(plan.scenes[0], shots=())
    with pytest.raises(InvalidProductionPlan, match="another scene"):
        replace(plan.scenes[0], shots=(replace(image_shot, scene_key="elsewhere"),))
    with pytest.raises(InvalidProductionPlan, match="contiguous"):
        replace(plan.scenes[0], shots=(replace(image_shot, ordinal=2),))

    image_segment, video_segment = plan.generation_segments
    with pytest.raises(InvalidProductionPlan, match="segment key"):
        replace(image_segment, segment_key="INVALID")
    with pytest.raises(InvalidProductionPlan, match="unique shots"):
        replace(image_segment, shot_keys=("shot_one", "shot_one"))
    with pytest.raises(InvalidProductionPlan, match="duration"):
        replace(video_segment, duration_seconds=31)
    with pytest.raises(InvalidProductionPlan, match="cannot have"):
        replace(image_segment, duration_seconds=4)
    with pytest.raises(InvalidProductionPlan, match="provider-neutral"):
        replace(image_segment, generation_spec={"api_key": "secret"})
    with pytest.raises(ValueError):
        ProductionCost(Decimal("-1"), Decimal(0), "USD", "v", "i")
    with pytest.raises(ValueError):
        ProductionCost(Decimal(0), Decimal(0), "usd", "v", "i")

    with pytest.raises(InvalidProductionPlan, match="requires scenes"):
        replace(plan, scenes=())
    with pytest.raises(InvalidProductionPlan, match="scene ordinals"):
        replace(plan, scenes=(replace(plan.scenes[0], ordinal=2),))
    with pytest.raises(InvalidProductionPlan, match="10 and 60"):
        replace(plan, scenes=(replace(plan.scenes[0], duration_seconds=1),))
    duplicate_shot_scene = replace(
        plan.scenes[0], shots=(image_shot, replace(image_shot, ordinal=2))
    )
    with pytest.raises(InvalidProductionPlan, match="shot keys"):
        replace(plan, scenes=(duplicate_shot_scene,))
    with pytest.raises(InvalidProductionPlan, match="unique plan shots"):
        replace(plan, generation_segments=(replace(image_segment, shot_keys=("missing",)),))
    with pytest.raises(InvalidProductionPlan, match="source strategy"):
        replace(
            plan,
            generation_segments=(
                replace(
                    image_segment,
                    shot_keys=(video_shot.shot_key,),
                    reference_asset_ids=(),
                ),
            ),
        )
    with pytest.raises(InvalidProductionPlan, match="plan digest"):
        replace(plan, semantic_digest=DIGEST)

    with pytest.raises(ValueError, match="cost binding"):
        ProductionPlanDecision(
            plan.tenant_id,
            plan.id,
            plan.semantic_digest,
            ProductionPlanDecisionState.REJECTED,
            uuid4(),
            "v",
            "i",
            "vp",
            "ip",
            Decimal("-1"),
            "USD",
        )


def test_generation_job_constructor_invariants() -> None:
    reference = context().selected_assets[0]
    values = {
        "tenant_id": uuid4(),
        "production_plan_id": uuid4(),
        "segment_key": "segment",
        "kind": MediaKind.IMAGE,
        "media_profile": "production_image",
        "route_version": "r",
        "provider": "openai",
        "model": "gpt-image-2",
        "pricing_version": "p",
        "generation_spec_digest": DIGEST,
        "input_assets": (reference,),
        "reserved_cost": Decimal("1"),
        "currency": "USD",
    }
    with pytest.raises(ValueError, match="cannot be negative"):
        GenerationJob(**{**values, "reserved_cost": Decimal("-1")})
    with pytest.raises(ValueError, match="specification digest"):
        GenerationJob(**{**values, "generation_spec_digest": "bad"})
    with pytest.raises(ValueError, match="unknown outcome"):
        GenerationJob(**values, status=GenerationJobStatus.OUTCOME_UNKNOWN)
    with pytest.raises(ValueError, match="imported Asset"):
        GenerationJob(**values, status=GenerationJobStatus.SUCCEEDED)


class FakeTransport:
    def __init__(self, replies: list[tuple[int, bytes]]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, str, bytes | None]] = []

    async def request(self, method, url, headers, body):
        self.calls.append((method, url, body))
        return self.replies.pop(0)


@pytest.mark.asyncio
async def test_seedance_exact_mapping_status_and_download() -> None:
    transport = FakeTransport(
        [
            (200, b'{"id":"task-1"}'),
            (200, b'{"status":"running","usage":{"total_tokens":2}}'),
            (
                200,
                b'{"status":"succeeded","content":{"video_url":"https://result.example/v.mp4"},"duration":12,"resolution":"720p"}',
            ),
            (200, b"\0\0\0\x18ftypmp42video"),
        ]
    )
    provider = SeedanceMediaProvider("real-key-value", transport=transport)
    request = VideoGenerationRequest("animate", 12, "720p", "9:16", True)
    started = await provider.start(request)
    body = json.loads(transport.calls[0][2])
    assert body["model"] == "dreamina-seedance-2-5-260628"
    assert (body["ratio"], body["duration"], body["generate_audio"]) == ("9:16", 12, True)
    assert (
        await provider.status(started.provider_operation_ref)
    ).state is ProviderGenerationState.RUNNING
    complete = await provider.status(started.provider_operation_ref)
    assert complete.temporary_result_locator == "https://result.example/v.mp4"
    content = await provider.download(complete.temporary_result_locator)
    assert validate_video_result(content) == "video/mp4"


@pytest.mark.asyncio
async def test_seedance_unknown_and_policy_rejections() -> None:
    provider = SeedanceMediaProvider("real-key-value", transport=FakeTransport([(500, b"")]))
    with pytest.raises(MediaProviderOutcomeUnknown):
        await provider.start(VideoGenerationRequest("x", 4, "480p", "9:16", False))
    with pytest.raises(ValueError):
        await provider.start(VideoGenerationRequest("x", 3, "480p", "9:16", False))
    face = MaterializedReference("image/png", b"x", "reference_image", True)
    with pytest.raises(ValueError):
        await provider.start(VideoGenerationRequest("x", 4, "480p", "9:16", False, (face,)))


@pytest.mark.asyncio
async def test_seedance_rejects_malformed_requests_and_provider_responses() -> None:
    with pytest.raises(ValueError):
        SeedanceMediaProvider("test-placeholder")
    with pytest.raises(ValueError):
        SeedanceMediaProvider("real-key-value", base_url="https://unverified.invalid")
    regional = SeedanceMediaProvider(
        "real-key-value",
        transport=FakeTransport([]),
        base_url="https://operator.las.eu-west-1.bytepluses.com/",
    )
    assert regional.base_url == "https://operator.las.eu-west-1.bytepluses.com"
    assert isinstance(SeedanceMediaProvider("real-key-value").transport, UrllibJsonHttpTransport)
    provider = SeedanceMediaProvider("real-key-value", transport=FakeTransport([]))
    with pytest.raises(ValueError):
        await provider.start(VideoGenerationRequest("x", 4, "1080p", "9:16", False))
    with pytest.raises(ValueError):
        await provider.start(
            VideoGenerationRequest("x", 4, "480p", "9:16", False, execution_expires_after=1)
        )
    video = MaterializedReference("video/mp4", b"video", "source_video")
    with pytest.raises(ValueError):
        await provider.start(VideoGenerationRequest("x", 4, "480p", "9:16", False, (video,)))
    image = MaterializedReference("image/png", b"image", "reference_image")
    inline = SeedanceMediaProvider(
        "real-key-value", transport=FakeTransport([(200, b'{"id":"task-image"}')])
    )
    assert (
        await inline.start(VideoGenerationRequest("x", 4, "480p", "9:16", False, (image,)))
    ).provider_operation_ref == "task-image"

    for status, body, error in (
        (400, b"rejected", MediaProviderError),
        (200, b"not-json", MediaProviderOutcomeUnknown),
        (200, b'{"id":""}', MediaProviderOutcomeUnknown),
    ):
        failing = SeedanceMediaProvider("real-key-value", transport=FakeTransport([(status, body)]))
        with pytest.raises(error):
            await failing.start(VideoGenerationRequest("x", 4, "480p", "9:16", False))

    with pytest.raises(ValueError):
        await provider.status("bad/reference")
    for status, body in ((400, b""), (200, b"not-json"), (200, b'{"status":"unknown"}')):
        failing = SeedanceMediaProvider("real-key-value", transport=FakeTransport([(status, body)]))
        with pytest.raises(MediaProviderError):
            await failing.status("task-1")
    missing_output = SeedanceMediaProvider(
        "real-key-value",
        transport=FakeTransport([(200, b'{"status":"succeeded","content":{}}')]),
    )
    with pytest.raises(MediaProviderError):
        await missing_output.status("task-1")
    with pytest.raises(ValueError):
        await provider.download("http://insecure.invalid/video")
    failed_download = SeedanceMediaProvider("real-key-value", transport=FakeTransport([(404, b"")]))
    with pytest.raises(MediaProviderError):
        await failed_download.download("https://result.example/video")


@pytest.mark.asyncio
async def test_default_seedance_transport_success_and_network_uncertainty(monkeypatch) -> None:
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b"ok"

    monkeypatch.setattr(seedance_module, "urlopen", lambda *_args, **_kwargs: Response())
    assert await UrllibJsonHttpTransport().request("GET", "https://example.test", {}, None) == (
        200,
        b"ok",
    )

    def unavailable(*_args, **_kwargs):
        raise URLError("offline")

    monkeypatch.setattr(seedance_module, "urlopen", unavailable)
    with pytest.raises(MediaProviderOutcomeUnknown):
        await UrllibJsonHttpTransport().request("GET", "https://example.test", {}, None)

    def rejected(*_args, **_kwargs):
        raise HTTPError("https://example.test", 418, "rejected", hdrs=None, fp=BytesIO(b"rejected"))

    monkeypatch.setattr(seedance_module, "urlopen", rejected)
    assert await UrllibJsonHttpTransport().request("GET", "https://example.test", {}, None) == (
        418,
        b"rejected",
    )


class FakeImages:
    def __init__(self, response):
        self.response = response
        self.generate_kwargs = None
        self.edit_kwargs = None

    async def generate(self, **kwargs):
        self.generate_kwargs = kwargs
        return self.response

    async def edit(self, **kwargs):
        self.edit_kwargs = kwargs
        return self.response


@pytest.mark.asyncio
async def test_openai_image_generate_and_edit() -> None:
    import base64

    png = b"\x89PNG\r\n\x1a\nbytes"
    response = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(png).decode())],
        id="image-1",
        usage=SimpleNamespace(input_tokens=1, output_tokens=2, total_tokens=3),
    )
    images = FakeImages(response)
    client = SimpleNamespace(images=images)
    provider = OpenAIImageProvider("real-key-value", client=client)  # type: ignore[arg-type]
    result = await provider.generate(ImageGenerationRequest("render", "1024x1536", "high"))
    assert (
        result.content == png
        and images.generate_kwargs["model"] == "gpt-image-2.5-sunburst-2026-09-08"
    )
    reference = MaterializedReference("image/png", png, "reference_image")
    await provider.generate(ImageGenerationRequest("edit", "1024x1536", "medium", (reference,)))
    assert images.edit_kwargs["model"] == "gpt-image-2.5-sunburst-2026-09-08"
    with pytest.raises(ValueError):
        await provider.generate(ImageGenerationRequest("render", "auto", "auto"))


@pytest.mark.asyncio
async def test_openai_image_adapter_rejects_credentials_transport_and_malformed_output() -> None:
    with pytest.raises(ValueError):
        OpenAIImageProvider("fake-placeholder")

    class RaisingImages:
        def __init__(self, error):
            self.error = error

        async def generate(self, **_kwargs):
            raise self.error

    request = httpx.Request("POST", "https://api.openai.com/v1/images")
    for error, expected in (
        (APITimeoutError(request=request), MediaProviderOutcomeUnknown),
        (
            APIStatusError("rejected", response=httpx.Response(400, request=request), body=None),
            MediaProviderError,
        ),
    ):
        provider = OpenAIImageProvider(
            "real-key-value",
            client=SimpleNamespace(images=RaisingImages(error)),  # type: ignore[arg-type]
        )
        with pytest.raises(expected):
            await provider.generate(ImageGenerationRequest("render", "1024x1024", "medium"))
    malformed = OpenAIImageProvider(
        "real-key-value",
        client=SimpleNamespace(images=FakeImages(SimpleNamespace(data=[]))),  # type: ignore[arg-type]
    )
    with pytest.raises(MediaProviderError):
        await malformed.generate(ImageGenerationRequest("render", "1024x1024", "medium"))


def test_media_result_and_workflow_contract_guards() -> None:
    assert validate_image_result(b"\xff\xd8\xffdata") == "image/jpeg"
    assert validate_image_result(b"RIFFxxxxWEBPdata") == "image/webp"
    with pytest.raises(InvalidMediaResult):
        validate_image_result(b"")
    with pytest.raises(InvalidMediaResult):
        validate_image_result(b"unrecognized-image")
    with pytest.raises(InvalidMediaResult):
        validate_image_result(b"\x89PNG\r\n\x1a\nlarge", maximum_bytes=2)
    with pytest.raises(InvalidMediaResult):
        validate_video_result(b"not-video")
    with pytest.raises(InvalidMediaResult):
        validate_video_result(b"\x00\x00\x00\x18ftypmp42video", maximum_bytes=2)
    request = MediaProductionWorkflowInput(
        str(uuid4()), str(uuid4()), (str(uuid4()),), (str(uuid4()),), str(uuid4())
    )
    assert len(request.image_job_ids) == 1
    with pytest.raises(ValueError):
        MediaProductionWorkflowInput(
            str(uuid4()), str(uuid4()), tuple(str(uuid4()) for _ in range(9)), (), str(uuid4())
        )
    with pytest.raises(ValueError):
        assert_reference_limits(
            (MaterializedReference("audio/mpeg", b"audio", "soundtrack"),),
            images=0,
            videos=0,
            audio=0,
        )
    assert isinstance(default_capability_registry().resolve("producer"), ProducerCapability)
    with pytest.raises(AgentCapabilityUnavailable):
        AgentCapabilityRegistry(()).resolve("producer")
    model = ModelContext("rules", {}, (), DIGEST, capability_context={"x": 1})
    assert model.capability_context is not None
