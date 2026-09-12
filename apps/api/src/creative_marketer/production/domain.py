from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import ROUND_UP, Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from creative_marketer.agent_runtime.domain import canonical_digest

MAX_VISUAL_REFERENCES = 10
MAX_IMAGE_GENERATIONS = 8
MAX_VIDEO_SEGMENTS = 4
MIN_VIDEO_SECONDS = 4
MAX_VIDEO_SECONDS = 30
MAX_PLAN_SECONDS = 60
MIN_PLAN_SECONDS = 10
MONEY_QUANTUM = Decimal("0.000001")


class ProductionError(Exception):
    code = "PRODUCTION_ERROR"


class ProductionCreativeNotApproved(ProductionError):
    code = "PRODUCTION_CREATIVE_NOT_APPROVED"


class ProductionCreativeRefreshRequired(ProductionError):
    code = "PRODUCTION_CREATIVE_REFRESH_REQUIRED"


class InvalidProductionPlan(ProductionError):
    code = "PRODUCTION_PLAN_INVALID"


class ProductionPricingChanged(ProductionError):
    code = "PRODUCTION_PRICING_CHANGED"


class ProductionCapabilityChanged(ProductionError):
    code = "PRODUCTION_MEDIA_CAPABILITY_CHANGED"


class ProductionRightsChanged(ProductionError):
    code = "PRODUCTION_ASSET_RIGHTS_CHANGED"


class ProductionPermissionDenied(ProductionError):
    code = "PRODUCTION_PERMISSION_DENIED"


class ProductionNotFound(ProductionError):
    code = "PRODUCTION_NOT_FOUND"


class ProductionDecisionConflict(ProductionError):
    code = "PRODUCTION_DECISION_CONFLICT"


class SourceStrategy(StrEnum):
    USE_EXISTING_ASSET = "USE_EXISTING_ASSET"
    GENERATE_IMAGE = "GENERATE_IMAGE"
    GENERATE_VIDEO = "GENERATE_VIDEO"
    MANUAL_CAPTURE = "MANUAL_CAPTURE"


class MediaKind(StrEnum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"


class ProductionPlanDecisionState(StrEnum):
    APPROVED_FOR_GENERATION = "APPROVED_FOR_GENERATION"
    REJECTED = "REJECTED"


class GenerationJobStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    READY = "READY"
    STARTING = "STARTING"
    PROCESSING = "PROCESSING"
    IMPORTING = "IMPORTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class ProductionExecutionStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    SHOTS_READY = "SHOTS_READY"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class FrozenAssetReference:
    asset_id: UUID
    digest: str
    kind: str
    role: str
    allowed_uses: tuple[str, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.digest):
            raise ValueError("asset reference requires an immutable sha256 digest")

    def primitive(self) -> dict[str, object]:
        return {
            "asset_id": str(self.asset_id),
            "digest": self.digest,
            "kind": self.kind,
            "role": self.role,
            "allowed_uses": list(self.allowed_uses),
        }


@dataclass(frozen=True, slots=True)
class ProductionPlanningRequest:
    target_format: str = "SHORT_FORM_VERTICAL_VIDEO"
    aspect_ratio: str = "9:16"

    def __post_init__(self) -> None:
        if self.target_format != "SHORT_FORM_VERTICAL_VIDEO" or self.aspect_ratio != "9:16":
            raise ValueError("V1 supports only 9:16 short-form vertical video")


@dataclass(frozen=True, slots=True)
class ProductionPlanningContext:
    concept_id: UUID
    concept_digest: str
    concept_set_id: UUID
    concept_set_digest: str
    product_snapshot_id: UUID
    product_snapshot_digest: str
    research_snapshot_id: UUID
    research_snapshot_digest: str
    creative_decision_id: UUID
    selected_assets: tuple[FrozenAssetReference, ...]
    request: ProductionPlanningRequest
    context_digest: str

    def __post_init__(self) -> None:
        if len(self.selected_assets) > MAX_VISUAL_REFERENCES:
            raise ValueError("Producer visual context exceeds its bounded maximum")
        if len({item.asset_id for item in self.selected_assets}) != len(self.selected_assets):
            raise ValueError("Producer visual context contains duplicate assets")
        if self.context_digest != canonical_digest(self.semantic_content()):
            raise ValueError("Producer context digest does not match frozen provenance")

    def semantic_content(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "concept_id": str(self.concept_id),
            "concept_digest": self.concept_digest,
            "concept_set_id": str(self.concept_set_id),
            "concept_set_digest": self.concept_set_digest,
            "product_snapshot_id": str(self.product_snapshot_id),
            "product_snapshot_digest": self.product_snapshot_digest,
            "research_snapshot_id": str(self.research_snapshot_id),
            "research_snapshot_digest": self.research_snapshot_digest,
            "creative_decision_id": str(self.creative_decision_id),
            "selected_assets": [item.primitive() for item in self.selected_assets],
            "request": {
                "target_format": self.request.target_format,
                "aspect_ratio": self.request.aspect_ratio,
            },
        }


@dataclass(frozen=True, slots=True)
class ProductionShot:
    shot_key: str
    scene_key: str
    ordinal: int
    source_strategy: SourceStrategy
    specification: Mapping[str, object]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.shot_key) or self.ordinal < 1:
            raise InvalidProductionPlan("shot identity is invalid")
        forbidden = {"provider", "model", "prompt", "seedance", "openai", "sunburst"}
        if forbidden.intersection(self.specification):
            raise InvalidProductionPlan("shot specification must remain provider-neutral")


@dataclass(frozen=True, slots=True)
class ProductionScene:
    scene_key: str
    ordinal: int
    purpose: str
    duration_seconds: int
    message: str
    voiceover: str | None
    on_screen_text: str | None
    shots: tuple[ProductionShot, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.scene_key):
            raise InvalidProductionPlan("scene key is invalid")
        if self.ordinal < 1 or self.duration_seconds < 1 or not self.shots:
            raise InvalidProductionPlan("scene must have duration and shots")
        if any(shot.scene_key != self.scene_key for shot in self.shots):
            raise InvalidProductionPlan("shot belongs to another scene")
        if [shot.ordinal for shot in self.shots] != list(range(1, len(self.shots) + 1)):
            raise InvalidProductionPlan("shot ordinals must be contiguous")


@dataclass(frozen=True, slots=True)
class GenerationSegment:
    segment_key: str
    shot_keys: tuple[str, ...]
    media_kind: MediaKind
    duration_seconds: int | None
    continuity: tuple[str, ...]
    reference_asset_ids: tuple[UUID, ...]
    generation_spec: Mapping[str, object]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.segment_key):
            raise InvalidProductionPlan("generation segment key is invalid")
        if not self.shot_keys or len(set(self.shot_keys)) != len(self.shot_keys):
            raise InvalidProductionPlan("generation segment requires unique shots")
        if self.media_kind is MediaKind.VIDEO and (
            self.duration_seconds is None
            or not MIN_VIDEO_SECONDS <= self.duration_seconds <= MAX_VIDEO_SECONDS
        ):
            raise InvalidProductionPlan("video segment duration is outside provider bounds")
        if self.media_kind is MediaKind.IMAGE and self.duration_seconds is not None:
            raise InvalidProductionPlan("image segment cannot have a duration")
        forbidden = {"provider", "model", "prompt", "endpoint", "api_key"}
        if forbidden.intersection(self.generation_spec):
            raise InvalidProductionPlan("generation specification must remain provider-neutral")


@dataclass(frozen=True, slots=True)
class ProductionCost:
    estimated_max_video_cost: Decimal
    estimated_max_image_cost: Decimal
    currency: str
    video_pricing_version: str
    image_pricing_version: str

    def __post_init__(self) -> None:
        if min(self.estimated_max_video_cost, self.estimated_max_image_cost) < 0:
            raise ValueError("production estimates cannot be negative")
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValueError("production cost currency is invalid")

    @property
    def estimated_total_cost(self) -> Decimal:
        return (self.estimated_max_video_cost + self.estimated_max_image_cost).quantize(
            MONEY_QUANTUM, rounding=ROUND_UP
        )


@dataclass(frozen=True, slots=True)
class ProductionPlan:
    tenant_id: UUID
    product_id: UUID
    agent_run_id: UUID
    context: ProductionPlanningContext
    strategy: str
    scenes: tuple[ProductionScene, ...]
    generation_segments: tuple[GenerationSegment, ...]
    required_assets: tuple[str, ...]
    cost: ProductionCost
    semantic_digest: str
    id: UUID = field(default_factory=uuid4)
    schema_version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.scenes:
            raise InvalidProductionPlan("production plan requires scenes")
        if [scene.ordinal for scene in self.scenes] != list(range(1, len(self.scenes) + 1)):
            raise InvalidProductionPlan("scene ordinals must be contiguous")
        total = sum(scene.duration_seconds for scene in self.scenes)
        if not MIN_PLAN_SECONDS <= total <= MAX_PLAN_SECONDS:
            raise InvalidProductionPlan("plan duration must be between 10 and 60 seconds")
        shots = [shot for scene in self.scenes for shot in scene.shots]
        shot_keys = [shot.shot_key for shot in shots]
        if len(set(shot_keys)) != len(shot_keys):
            raise InvalidProductionPlan("shot keys must be unique")
        segment_shots = [key for segment in self.generation_segments for key in segment.shot_keys]
        if any(key not in shot_keys for key in segment_shots) or len(set(segment_shots)) != len(
            segment_shots
        ):
            raise InvalidProductionPlan("segments must reference unique plan shots")
        video = [item for item in self.generation_segments if item.media_kind is MediaKind.VIDEO]
        images = [item for item in self.generation_segments if item.media_kind is MediaKind.IMAGE]
        if len(video) > MAX_VIDEO_SEGMENTS or len(images) > MAX_IMAGE_GENERATIONS:
            raise InvalidProductionPlan("generation count exceeds bounded plan limits")
        expected_strategy = {
            MediaKind.IMAGE: SourceStrategy.GENERATE_IMAGE,
            MediaKind.VIDEO: SourceStrategy.GENERATE_VIDEO,
        }
        by_key = {shot.shot_key: shot for shot in shots}
        if any(
            by_key[key].source_strategy is not expected_strategy[segment.media_kind]
            for segment in self.generation_segments
            for key in segment.shot_keys
        ):
            raise InvalidProductionPlan("segment media kind conflicts with shot source strategy")
        if self.semantic_digest != canonical_digest(self.semantic_content()):
            raise InvalidProductionPlan("plan digest does not match semantic content")

    def semantic_content(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "context_digest": self.context.context_digest,
            "strategy": self.strategy,
            "format": {"kind": "SHORT_FORM_VERTICAL_VIDEO", "aspect_ratio": "9:16"},
            "scenes": [
                {
                    "scene_key": scene.scene_key,
                    "ordinal": scene.ordinal,
                    "purpose": scene.purpose,
                    "duration_seconds": scene.duration_seconds,
                    "message": scene.message,
                    "voiceover": scene.voiceover,
                    "on_screen_text": scene.on_screen_text,
                    "shots": [
                        {
                            "shot_key": shot.shot_key,
                            "scene_key": shot.scene_key,
                            "ordinal": shot.ordinal,
                            "source_strategy": shot.source_strategy.value,
                            **dict(shot.specification),
                        }
                        for shot in scene.shots
                    ],
                }
                for scene in self.scenes
            ],
            "generation_segments": [
                {
                    "segment_key": item.segment_key,
                    "shot_keys": list(item.shot_keys),
                    "media_kind": item.media_kind.value,
                    "duration_seconds": item.duration_seconds,
                    "continuity": list(item.continuity),
                    "reference_asset_ids": [str(value) for value in item.reference_asset_ids],
                    "generation_spec": dict(item.generation_spec),
                }
                for item in self.generation_segments
            ],
            "required_assets": list(self.required_assets),
        }


@dataclass(frozen=True, slots=True)
class ProductionPlanDecision:
    tenant_id: UUID
    production_plan_id: UUID
    plan_digest: str
    state: ProductionPlanDecisionState
    decided_by: UUID
    video_route_version: str
    image_route_version: str
    video_pricing_version: str
    image_pricing_version: str
    estimated_max_cost: Decimal
    currency: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.estimated_max_cost < 0 or not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValueError("approval cost binding is invalid")


@dataclass(frozen=True, slots=True)
class GenerationJob:
    tenant_id: UUID
    production_plan_id: UUID
    segment_key: str
    kind: MediaKind
    media_profile: str
    route_version: str
    provider: str
    model: str
    pricing_version: str
    generation_spec_digest: str
    input_assets: tuple[FrozenAssetReference, ...]
    reserved_cost: Decimal
    currency: str
    id: UUID = field(default_factory=uuid4)
    status: GenerationJobStatus = GenerationJobStatus.PENDING_APPROVAL
    provider_operation_ref: str | None = None
    actual_cost: Decimal = Decimal("0")
    unknown_cost: Decimal = Decimal("0")
    output_asset_id: UUID | None = None
    failure_code: str | None = None
    initiated_by_user_id: UUID | None = None
    executed_by_workload_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if min(self.reserved_cost, self.actual_cost, self.unknown_cost) < 0:
            raise ValueError("generation costs cannot be negative")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.generation_spec_digest):
            raise ValueError("generation job requires a specification digest")
        if self.status is GenerationJobStatus.OUTCOME_UNKNOWN and self.unknown_cost <= 0:
            raise ValueError("unknown outcome must retain conservative cost")
        if self.status is GenerationJobStatus.SUCCEEDED and self.output_asset_id is None:
            raise ValueError("successful generation requires an imported Asset")
        if self.executed_by_workload_id is not None and not self.executed_by_workload_id.strip():
            raise ValueError("generation workload identity cannot be blank")

    def transition(
        self,
        status: GenerationJobStatus,
        *,
        provider_operation_ref: str | None = None,
        actual_cost: Decimal | None = None,
        output_asset_id: UUID | None = None,
        failure_code: str | None = None,
        initiated_by_user_id: UUID | None = None,
        executed_by_workload_id: str | None = None,
    ) -> GenerationJob:
        allowed = {
            GenerationJobStatus.PENDING_APPROVAL: {GenerationJobStatus.READY},
            GenerationJobStatus.READY: {GenerationJobStatus.STARTING},
            GenerationJobStatus.STARTING: {
                GenerationJobStatus.PROCESSING,
                GenerationJobStatus.IMPORTING,
                GenerationJobStatus.FAILED,
                GenerationJobStatus.OUTCOME_UNKNOWN,
            },
            GenerationJobStatus.PROCESSING: {
                GenerationJobStatus.PROCESSING,
                GenerationJobStatus.IMPORTING,
                GenerationJobStatus.FAILED,
            },
            GenerationJobStatus.IMPORTING: {
                GenerationJobStatus.SUCCEEDED,
                GenerationJobStatus.FAILED,
            },
        }
        if status not in allowed.get(self.status, set()):
            raise ValueError("invalid generation job transition")
        unknown = (
            self.reserved_cost if status is GenerationJobStatus.OUTCOME_UNKNOWN else Decimal(0)
        )
        return replace(
            self,
            status=status,
            provider_operation_ref=provider_operation_ref or self.provider_operation_ref,
            actual_cost=self.actual_cost if actual_cost is None else actual_cost,
            unknown_cost=unknown,
            output_asset_id=output_asset_id or self.output_asset_id,
            failure_code=failure_code,
            initiated_by_user_id=initiated_by_user_id or self.initiated_by_user_id,
            executed_by_workload_id=executed_by_workload_id or self.executed_by_workload_id,
            updated_at=datetime.now(UTC),
        )


@dataclass(frozen=True, slots=True)
class AssetLineage:
    tenant_id: UUID
    parent_asset_id: UUID
    child_asset_id: UUID
    relationship_type: str
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        allowed = {
            "REFERENCE_IMAGE",
            "START_FRAME",
            "END_FRAME",
            "SOURCE_IMAGE",
            "SOURCE_VIDEO",
            "DERIVED_FROM",
        }
        if self.parent_asset_id == self.child_asset_id or self.relationship_type not in allowed:
            raise ValueError("asset lineage is invalid")
