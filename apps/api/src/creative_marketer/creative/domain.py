from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from creative_marketer.agent_runtime.domain import canonical_digest

MAX_CONCEPTS = 5
MIN_CONCEPTS = 3
MAX_SCENES = 10
MIN_SCENES = 3
MAX_MESSAGE_POINTS = 10
MAX_RESEARCH_REFS = 10
MAX_ASSET_REQUIREMENTS = 15


class CreativeError(Exception):
    code = "CREATIVE_ERROR"


class CreativeBriefIncomplete(CreativeError):
    code = "CREATIVE_BRIEF_INCOMPLETE"


class CreativeResearchRefreshRequired(CreativeError):
    code = "CREATIVE_RESEARCH_REFRESH_REQUIRED"


class InvalidCreativeOutput(CreativeError):
    code = "INVALID_CREATIVE_OUTPUT"


class InvalidCreativeAssetReference(InvalidCreativeOutput):
    code = "INVALID_CREATIVE_ASSET_REFERENCE"


class InvalidCreativeResearchReference(InvalidCreativeOutput):
    code = "INVALID_CREATIVE_RESEARCH_REFERENCE"


class InvalidCreativeClaimReference(InvalidCreativeOutput):
    code = "INVALID_CREATIVE_CLAIM_REFERENCE"


class ProhibitedCreativeClaim(InvalidCreativeOutput):
    code = "PROHIBITED_CREATIVE_CLAIM"


class CreativeNotFound(CreativeError):
    code = "CREATIVE_NOT_FOUND"


class CreativePermissionDenied(CreativeError):
    code = "CREATIVE_PERMISSION_DENIED"


class CreativeDecisionConflict(CreativeError):
    code = "CREATIVE_DECISION_CONFLICT"


class ChannelIntent(StrEnum):
    ORGANIC_SHORT_FORM = "ORGANIC_SHORT_FORM"
    TIKTOK = "TIKTOK"
    INSTAGRAM_REELS = "INSTAGRAM_REELS"
    PAID_SOCIAL = "PAID_SOCIAL"


class SuccessMetric(StrEnum):
    THREE_SECOND_VIEW_RATE = "THREE_SECOND_VIEW_RATE"
    HOOK_HOLD_RATE = "HOOK_HOLD_RATE"
    WATCH_THROUGH_RATE = "WATCH_THROUGH_RATE"
    CTR = "CTR"
    CONVERSION_RATE = "CONVERSION_RATE"


class MessagePointKind(StrEnum):
    VALUE_PROPOSITION = "VALUE_PROPOSITION"
    PRODUCT_FACT = "PRODUCT_FACT"
    EMOTIONAL_MESSAGE = "EMOTIONAL_MESSAGE"
    SOCIAL_PROOF_ANGLE = "SOCIAL_PROOF_ANGLE"
    OFFER = "OFFER"
    CTA = "CTA"


class AssetRequirementKind(StrEnum):
    EXISTING_ASSET = "EXISTING_ASSET"
    MISSING_ASSET = "MISSING_ASSET"


class CreativeDecisionState(StrEnum):
    SHORTLISTED = "SHORTLISTED"
    APPROVED_FOR_PRODUCTION = "APPROVED_FOR_PRODUCTION"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class ProductClaimRef:
    key: str
    text: str


def product_claim_refs(
    snapshot_digest: str, content: Mapping[str, object]
) -> tuple[ProductClaimRef, ...]:
    values: list[tuple[str, str]] = []
    for section, key in (("brand_profile", "allowed_claims"), ("profile", "allowed_claims")):
        raw = content.get(section)
        if isinstance(raw, Mapping):
            claims = raw.get(key, ())
            if isinstance(claims, (list, tuple)):
                values.extend((section, str(item)) for item in claims if str(item).strip())
    return tuple(
        ProductClaimRef(
            canonical_digest(
                {"snapshot_digest": snapshot_digest, "kind": kind, "ordinal": i, "text": text}
            ),
            text,
        )
        for i, (kind, text) in enumerate(values)
    )


@dataclass(frozen=True, slots=True)
class CreativeStrategyRequest:
    concept_count: int = 5
    channel_intent: ChannelIntent = ChannelIntent.ORGANIC_SHORT_FORM

    def __post_init__(self) -> None:
        if not MIN_CONCEPTS <= self.concept_count <= MAX_CONCEPTS:
            raise ValueError("concept_count must be between 3 and 5")


@dataclass(frozen=True, slots=True)
class CreativeStrategyContext:
    product_snapshot_id: UUID
    product_snapshot_digest: str
    product_snapshot_schema_version: int
    research_snapshot_id: UUID
    research_snapshot_digest: str
    research_agent_run_id: UUID
    product_context: Mapping[str, object]
    research_findings: tuple[Mapping[str, object], ...]
    research_gaps: tuple[str, ...]
    asset_manifest: tuple[Mapping[str, object], ...]
    product_claims: tuple[ProductClaimRef, ...]
    request: CreativeStrategyRequest
    context_digest: str

    def refs(self) -> tuple[Mapping[str, object], ...]:
        return (
            {
                "kind": "product_snapshot",
                "id": str(self.product_snapshot_id),
                "digest": self.product_snapshot_digest,
            },
            {
                "kind": "research_snapshot",
                "id": str(self.research_snapshot_id),
                "digest": self.research_snapshot_digest,
                "agent_run_id": str(self.research_agent_run_id),
            },
            {
                "kind": "asset_manifest",
                "asset_ids": [str(item["asset_id"]) for item in self.asset_manifest],
            },
            {
                "kind": "strategy_request",
                "concept_count": self.request.concept_count,
                "channel_intent": self.request.channel_intent.value,
            },
        )


@dataclass(frozen=True, slots=True)
class CreativeConcept:
    tenant_id: UUID
    product_id: UUID
    concept_set_id: UUID
    concept_key: str
    ordinal: int
    payload: Mapping[str, object]
    semantic_digest: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.concept_key) or self.ordinal < 1:
            raise InvalidCreativeOutput("concept identity is invalid")
        if self.semantic_digest != canonical_digest(dict(self.payload)):
            raise InvalidCreativeOutput("concept digest does not match payload")


@dataclass(frozen=True, slots=True)
class CreativeConceptSet:
    tenant_id: UUID
    product_id: UUID
    agent_run_id: UUID
    product_snapshot_id: UUID
    product_snapshot_digest: str
    research_snapshot_id: UUID
    research_snapshot_digest: str
    input_context_digest: str
    concepts: tuple[CreativeConcept, ...]
    semantic_digest: str
    id: UUID = field(default_factory=uuid4)
    schema_version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not MIN_CONCEPTS <= len(self.concepts) <= MAX_CONCEPTS:
            raise InvalidCreativeOutput("concept set must contain 3 to 5 concepts")
        if any(item.concept_set_id != self.id for item in self.concepts):
            raise InvalidCreativeOutput("concept belongs to another concept set")
        expected = canonical_digest(self.semantic_content())
        if expected != self.semantic_digest:
            raise InvalidCreativeOutput("concept set digest does not match content")

    def semantic_content(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "product_snapshot_id": str(self.product_snapshot_id),
            "product_snapshot_digest": self.product_snapshot_digest,
            "research_snapshot_id": str(self.research_snapshot_id),
            "research_snapshot_digest": self.research_snapshot_digest,
            "input_context_digest": self.input_context_digest,
            "concepts": [dict(item.payload) for item in self.concepts],
        }


@dataclass(frozen=True, slots=True)
class CreativeConceptDecision:
    tenant_id: UUID
    product_id: UUID
    concept_id: UUID
    state: CreativeDecisionState
    decided_by: UUID
    reason_code: str | None = None
    note: str | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.reason_code is not None and not re.fullmatch(
            r"[A-Z][A-Z0-9_]{0,63}", self.reason_code
        ):
            raise ValueError("decision reason code is invalid")
        if self.note is not None and len(self.note) > 1000:
            raise ValueError("decision note is too long")


@dataclass(frozen=True, slots=True)
class ApprovedCreativeConcept:
    concept: CreativeConcept
    concept_set: CreativeConceptSet
    decision: CreativeConceptDecision

    def __post_init__(self) -> None:
        if self.decision.state is not CreativeDecisionState.APPROVED_FOR_PRODUCTION:
            raise ValueError("producer handoff requires production approval")


def normalized_phrase(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))
