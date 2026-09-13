from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_UP, Decimal
from pathlib import Path
from typing import ClassVar, Protocol
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker

from creative_marketer.agent_runtime.domain import ModelPricing, ModelRoute, canonical_digest
from creative_marketer.creative.domain import (
    ApprovedCreativeConcept,
    CreativeDecisionState,
)

from .domain import (
    MAX_VISUAL_REFERENCES,
    FrozenAssetReference,
    GenerationSegment,
    InvalidProductionPlan,
    MediaKind,
    ProductionCost,
    ProductionCreativeNotApproved,
    ProductionCreativeRefreshRequired,
    ProductionPermissionDenied,
    ProductionPlan,
    ProductionPlanningContext,
    ProductionPlanningRequest,
    ProductionScene,
    ProductionShot,
    SourceStrategy,
)

PRODUCTION_CONTRACT_KEY = "production.production_plan"
PRODUCTION_CONTRACT_VERSION = 1
SEEDANCE_MODEL = "dreamina-seedance-2-5-260628"
SEEDANCE_ROUTE_VERSION = "byteplus-seedance-2.5-2026-08-17"
SEEDANCE_PRICING_VERSION = "byteplus-enhanced-2026-08-17"
OPENAI_IMAGE_MODEL = "gpt-image-2.5-sunburst-2026-09-08"
OPENAI_IMAGE_ROUTE_VERSION = "openai-gpt-image-2.5-sunburst-2026-09-13"
OPENAI_IMAGE_PRICING_VERSION = "openai-gpt-image-2.5-sunburst-2026-09-13"


@dataclass(frozen=True, slots=True)
class MediaCapabilities:
    media_kind: MediaKind
    aspect_ratios: frozenset[str]
    resolutions: frozenset[str]
    minimum_duration_seconds: int | None
    maximum_duration_seconds: int | None
    maximum_images: int
    maximum_videos: int
    maximum_audio: int
    real_face_references_require_allowlist: bool


@dataclass(frozen=True, slots=True)
class MediaRoute:
    profile_key: str
    route_version: str
    provider: str
    model: str
    pricing_version: str
    capabilities: MediaCapabilities


class MediaRouter:
    def __init__(self, routes: Sequence[MediaRoute]) -> None:
        self._routes = {route.profile_key: route for route in routes}
        if len(self._routes) != len(routes):
            raise ValueError("media profile keys must be unique")

    def resolve(self, profile_key: str) -> MediaRoute:
        try:
            return self._routes[profile_key]
        except KeyError as error:
            raise InvalidProductionPlan("logical media profile is unavailable") from error


def initial_producer_route() -> ModelRoute:
    """Current immutable Producer route verified against official OpenAI documentation."""
    return ModelRoute(
        profile_key="production_deep",
        route_version="openai-gpt-6-astra-production-2026-09-13",
        provider="openai",
        model="gpt-6-astra",
        capabilities=frozenset({"text", "image_input", "reasoning", "structured_output"}),
        reasoning_effort="high",
        max_output_tokens=12_000,
        pricing=ModelPricing("openai-gpt-6-astra-2026-09-13", Decimal("10"), Decimal("50"), "USD"),
    )


def initial_media_router() -> MediaRouter:
    return MediaRouter(
        (
            MediaRoute(
                "production_video",
                SEEDANCE_ROUTE_VERSION,
                "byteplus",
                SEEDANCE_MODEL,
                SEEDANCE_PRICING_VERSION,
                MediaCapabilities(
                    MediaKind.VIDEO,
                    frozenset({"16:9", "4:3", "1:1", "3:4", "9:16", "21:9"}),
                    frozenset({"480p", "720p"}),
                    4,
                    30,
                    30,
                    10,
                    10,
                    True,
                ),
            ),
            MediaRoute(
                "production_image",
                OPENAI_IMAGE_ROUTE_VERSION,
                "openai",
                OPENAI_IMAGE_MODEL,
                OPENAI_IMAGE_PRICING_VERSION,
                MediaCapabilities(
                    MediaKind.IMAGE,
                    frozenset({"1:1", "2:3", "3:2"}),
                    frozenset({"1024x1024", "1024x1536", "1536x1024"}),
                    None,
                    None,
                    10,
                    0,
                    0,
                    False,
                ),
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class SeedancePricing:
    version: str = SEEDANCE_PRICING_VERSION
    base_usd_per_second: Decimal = Decimal("0.303")

    _FACTORS: ClassVar[dict[tuple[str, bool], Decimal]] = {
        ("480p", False): Decimal("0.6785"),
        ("720p", False): Decimal("1.525"),
        ("480p", True): Decimal("0.406"),
        ("720p", True): Decimal("0.9125"),
    }

    def cost(
        self,
        *,
        output_duration_seconds: int,
        resolution: str,
        input_video_duration_seconds: int = 0,
    ) -> Decimal:
        with_input_video = input_video_duration_seconds > 0
        try:
            factor = self._FACTORS[(resolution, with_input_video)]
        except KeyError as error:
            raise ValueError("unsupported Seedance pricing dimensions") from error
        billed_seconds = output_duration_seconds + input_video_duration_seconds
        if not 4 <= output_duration_seconds <= 30 or billed_seconds <= 0:
            raise ValueError("Seedance billing duration is invalid")
        return (self.base_usd_per_second * Decimal(billed_seconds) * factor).quantize(
            Decimal("0.000001"), rounding=ROUND_UP
        )


@dataclass(frozen=True, slots=True)
class ImageReservationPricing:
    version: str = OPENAI_IMAGE_PRICING_VERSION
    medium_max_usd: Decimal = Decimal("0.20")
    high_max_usd: Decimal = Decimal("0.40")

    # Official token prices per one million tokens. Reservations remain deliberately
    # conservative because output token counts are not knowable before generation.
    text_input_usd_per_million: Decimal = Decimal("5")
    cached_text_input_usd_per_million: Decimal = Decimal("1.25")
    image_input_usd_per_million: Decimal = Decimal("8")
    cached_image_input_usd_per_million: Decimal = Decimal("2")
    image_output_usd_per_million: Decimal = Decimal("30")

    def reserve(self, quality: str) -> Decimal:
        try:
            return {"MEDIUM": self.medium_max_usd, "HIGH": self.high_max_usd}[quality]
        except KeyError as error:
            raise ValueError("unsupported image quality") from error

    def actual_cost(self, usage: Mapping[str, int]) -> Decimal | None:
        required = {"text_input_tokens", "image_input_tokens", "image_output_tokens"}
        if not required.issubset(usage):
            return None
        text = max(0, usage["text_input_tokens"] - usage.get("cached_text_input_tokens", 0))
        cached_text = max(0, usage.get("cached_text_input_tokens", 0))
        image = max(0, usage["image_input_tokens"] - usage.get("cached_image_input_tokens", 0))
        cached_image = max(0, usage.get("cached_image_input_tokens", 0))
        output = max(0, usage["image_output_tokens"])
        total = (
            Decimal(text) * self.text_input_usd_per_million
            + Decimal(cached_text) * self.cached_text_input_usd_per_million
            + Decimal(image) * self.image_input_usd_per_million
            + Decimal(cached_image) * self.cached_image_input_usd_per_million
            + Decimal(output) * self.image_output_usd_per_million
        ) / Decimal(1_000_000)
        return total.quantize(Decimal("0.000001"), rounding=ROUND_UP)


def load_production_plan_schema() -> Mapping[str, object]:
    path = Path(__file__).with_name("schemas") / "production.production_plan.v1.json"
    return json.loads(path.read_text())  # type: ignore[no-any-return]


_VISUAL_ROLE_ORDER = {
    "product_hero": 0,
    "product_detail": 1,
    "lifestyle": 2,
    "logo": 3,
    "brand_reference": 4,
    "brand_guideline": 4,
}


def select_visual_assets(
    manifest: Sequence[Mapping[str, object]],
) -> tuple[FrozenAssetReference, ...]:
    eligible = [
        item
        for item in manifest
        if str(item.get("status", "")).casefold() == "ready"
        and str(item.get("kind", "")).casefold() == "image"
        and str(item.get("role", "")).casefold() in _VISUAL_ROLE_ORDER
        and str(item.get("rights_status", "")).casefold() == "confirmed"
        and "generation_input" in _allowed_uses(item)
    ]
    eligible.sort(
        key=lambda item: (
            _VISUAL_ROLE_ORDER[str(item["role"]).casefold()],
            str(item["asset_id"]),
        )
    )
    return tuple(
        FrozenAssetReference(
            UUID(str(item["asset_id"])),
            str(item["digest"]),
            str(item["kind"]),
            str(item["role"]),
            _allowed_use_values(item),
        )
        for item in eligible[:MAX_VISUAL_REFERENCES]
    )


def _allowed_use_values(item: Mapping[str, object]) -> tuple[str, ...]:
    raw = item.get("allowed_uses", ())
    return tuple(str(value) for value in raw) if isinstance(raw, (list, tuple)) else ()


def _allowed_uses(item: Mapping[str, object]) -> set[str]:
    return {value.casefold() for value in _allowed_use_values(item)}


def build_production_context(
    approved: ApprovedCreativeConcept,
    *,
    current_product_snapshot_id: UUID,
    current_product_snapshot_digest: str,
    current_research_snapshot_id: UUID,
    current_research_snapshot_digest: str,
    asset_manifest: Sequence[Mapping[str, object]],
    request: ProductionPlanningRequest | None = None,
) -> ProductionPlanningContext:
    if approved.decision.state is not CreativeDecisionState.APPROVED_FOR_PRODUCTION:
        raise ProductionCreativeNotApproved("concept requires current production approval")
    concept_set = approved.concept_set
    if (
        concept_set.product_snapshot_id != current_product_snapshot_id
        or concept_set.product_snapshot_digest != current_product_snapshot_digest
        or concept_set.research_snapshot_id != current_research_snapshot_id
        or concept_set.research_snapshot_digest != current_research_snapshot_digest
    ):
        raise ProductionCreativeRefreshRequired("approved creative context is no longer current")
    selected = select_visual_assets(asset_manifest)
    value = ProductionPlanningRequest() if request is None else request
    fields = {
        "schema_version": 1,
        "concept_id": str(approved.concept.id),
        "concept_digest": approved.concept.semantic_digest,
        "concept_set_id": str(concept_set.id),
        "concept_set_digest": concept_set.semantic_digest,
        "product_snapshot_id": str(current_product_snapshot_id),
        "product_snapshot_digest": current_product_snapshot_digest,
        "research_snapshot_id": str(current_research_snapshot_id),
        "research_snapshot_digest": current_research_snapshot_digest,
        "creative_decision_id": str(approved.decision.id),
        "selected_assets": [item.primitive() for item in selected],
        "request": {"target_format": value.target_format, "aspect_ratio": value.aspect_ratio},
    }
    return ProductionPlanningContext(
        approved.concept.id,
        approved.concept.semantic_digest,
        concept_set.id,
        concept_set.semantic_digest,
        current_product_snapshot_id,
        current_product_snapshot_digest,
        current_research_snapshot_id,
        current_research_snapshot_digest,
        approved.decision.id,
        selected,
        value,
        canonical_digest(fields),
    )


def production_context_from_payload(
    values: Mapping[str, object], context_digest: str
) -> ProductionPlanningContext:
    """Rehydrate only frozen, JSON-safe Producer provenance recovered from an AgentRun."""
    assets = values.get("selected_assets", ())
    request = values.get("production_request", {})
    if not isinstance(assets, (list, tuple)) or not isinstance(request, Mapping):
        raise InvalidProductionPlan("stored Producer context is malformed")
    return ProductionPlanningContext(
        UUID(str(values["concept_id"])),
        str(values["concept_digest"]),
        UUID(str(values["concept_set_id"])),
        str(values["concept_set_digest"]),
        UUID(str(values["product_snapshot_id"])),
        str(values["product_snapshot_digest"]),
        UUID(str(values["research_snapshot_id"])),
        str(values["research_snapshot_digest"]),
        UUID(str(values["creative_decision_id"])),
        tuple(
            FrozenAssetReference(
                UUID(str(item["asset_id"])),
                str(item["digest"]),
                str(item["kind"]),
                str(item["role"]),
                tuple(str(value) for value in item["allowed_uses"]),
            )
            for item in assets
            if isinstance(item, Mapping)
        ),
        ProductionPlanningRequest(
            str(request.get("target_format", "SHORT_FORM_VERTICAL_VIDEO")),
            str(request.get("aspect_ratio", "9:16")),
        ),
        context_digest,
    )


def validate_production_plan(
    output: Mapping[str, object],
    *,
    tenant_id: UUID,
    product_id: UUID,
    agent_run_id: UUID,
    context: ProductionPlanningContext,
    concept_scene_keys: Sequence[str],
) -> ProductionPlan:
    errors = sorted(
        Draft202012Validator(
            load_production_plan_schema(), format_checker=FormatChecker()
        ).iter_errors(dict(output)),
        key=lambda item: list(item.path),
    )
    if errors:
        raise InvalidProductionPlan("provider output does not match production contract")
    raw_scenes = output["scenes"]
    raw_segments = output["generation_segments"]
    assert isinstance(raw_scenes, list) and isinstance(raw_segments, list)
    if [str(item["scene_key"]) for item in raw_scenes] != list(concept_scene_keys):
        raise InvalidProductionPlan("plan must preserve every CreativeConcept scene in order")
    selected_ids = {item.asset_id for item in context.selected_assets}
    scenes: list[ProductionScene] = []
    for raw_scene in raw_scenes:
        shots: list[ProductionShot] = []
        for raw_shot in raw_scene["shots"]:
            strategy = SourceStrategy(raw_shot["source_strategy"])
            existing = raw_shot["existing_asset_id"]
            image_spec = raw_shot["image_generation_spec"]
            if strategy is SourceStrategy.USE_EXISTING_ASSET:
                if existing is None or UUID(existing) not in selected_ids or image_spec is not None:
                    raise InvalidProductionPlan("existing shot requires an authorized frozen Asset")
            elif existing is not None:
                raise InvalidProductionPlan("only existing-asset shots may bind existing_asset_id")
            if (strategy is SourceStrategy.GENERATE_IMAGE) != (image_spec is not None):
                raise InvalidProductionPlan("image generation strategy/specification mismatch")
            specification = {
                key: value
                for key, value in raw_shot.items()
                if key not in {"shot_key", "scene_key", "ordinal", "source_strategy"}
            }
            shots.append(
                ProductionShot(
                    raw_shot["shot_key"],
                    raw_shot["scene_key"],
                    raw_shot["ordinal"],
                    strategy,
                    specification,
                )
            )
        scenes.append(
            ProductionScene(
                raw_scene["scene_key"],
                raw_scene["ordinal"],
                raw_scene["purpose"],
                raw_scene["duration_seconds"],
                raw_scene["message"],
                raw_scene["voiceover"],
                raw_scene["on_screen_text"],
                tuple(shots),
            )
        )
    segments = tuple(
        GenerationSegment(
            item["segment_key"],
            tuple(item["shot_keys"]),
            MediaKind(item["media_kind"]),
            item["duration_seconds"],
            tuple(item["continuity"]),
            tuple(UUID(value) for value in item["reference_asset_ids"]),
            item["generation_spec"],
        )
        for item in raw_segments
    )
    if any(
        asset_id not in selected_ids for item in segments for asset_id in item.reference_asset_ids
    ):
        raise InvalidProductionPlan("segment references an Asset outside frozen Producer context")
    seedance = SeedancePricing()
    image = ImageReservationPricing()
    video_cost = sum(
        (
            seedance.cost(
                output_duration_seconds=item.duration_seconds or 0,
                resolution=str(item.generation_spec["resolution"]),
            )
            for item in segments
            if item.media_kind is MediaKind.VIDEO
        ),
        Decimal(0),
    )
    image_cost = sum(
        (
            image.reserve(
                next(
                    str(shot.specification["image_generation_spec"]["quality_intent"])  # type: ignore[index]
                    for scene in scenes
                    for shot in scene.shots
                    if shot.shot_key in item.shot_keys
                )
            )
            for item in segments
            if item.media_kind is MediaKind.IMAGE
        ),
        Decimal(0),
    )
    cost = ProductionCost(
        video_cost,
        image_cost,
        "USD",
        seedance.version,
        image.version,
    )
    semantic = {
        "schema_version": 1,
        "context_digest": context.context_digest,
        "strategy": output["strategy"],
        "format": output["format"],
        "scenes": raw_scenes,
        "generation_segments": raw_segments,
        "required_assets": output["required_assets"],
    }
    return ProductionPlan(
        tenant_id,
        product_id,
        agent_run_id,
        context,
        str(output["strategy"]),
        tuple(scenes),
        segments,
        tuple(str(value) for value in output["required_assets"])
        if isinstance(output["required_assets"], list)
        else (),
        cost,
        canonical_digest(semantic),
    )


class ProductionBudgetLedger(Protocol):
    async def reserve(
        self, tenant_id: UUID, job_id: UUID, amount: Decimal, currency: str
    ) -> bool: ...

    async def settle(
        self, tenant_id: UUID, job_id: UUID, actual: Decimal, unknown: Decimal, currency: str
    ) -> None: ...


@dataclass(slots=True)
class ProductionBudgetGuard:
    ledger: ProductionBudgetLedger

    async def reserve(self, tenant_id: UUID, job_id: UUID, amount: Decimal, currency: str) -> None:
        if amount < 0 or not await self.ledger.reserve(tenant_id, job_id, amount, currency):
            raise ProductionPermissionDenied("production media budget exceeded")
