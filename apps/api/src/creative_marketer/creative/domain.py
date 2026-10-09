from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from creative_marketer.agent_runtime.domain import Finding, canonical_digest

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

    def __init__(self, message: str, *, diagnostic: CreativeClaimDiagnostic | None = None) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic


class ProhibitedCreativeClaim(InvalidCreativeOutput):
    code = "PROHIBITED_CREATIVE_CLAIM"


class CreativeNotFound(CreativeError):
    code = "CREATIVE_NOT_FOUND"


class CreativePermissionDenied(CreativeError):
    code = "CREATIVE_PERMISSION_DENIED"


class CreativeDecisionConflict(CreativeError):
    code = "CREATIVE_DECISION_CONFLICT"


class CreativeRevalidationNotReady(CreativeError):
    code = "CREATIVE_REVALIDATION_NOT_READY"


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


class CreativeRevalidationResult(StrEnum):
    REVALIDATED_FOR_PRODUCTION = "REVALIDATED_FOR_PRODUCTION"
    REQUIRES_RESTRATEGY = "REQUIRES_RESTRATEGY"


class CreativeRevalidationReason(StrEnum):
    RESEARCH_FINDING_MISSING = "RESEARCH_FINDING_MISSING"
    RESEARCH_FINDING_CATEGORY_CHANGED = "RESEARCH_FINDING_CATEGORY_CHANGED"
    RESEARCH_FINDING_STATEMENT_CHANGED = "RESEARCH_FINDING_STATEMENT_CHANGED"
    RESEARCH_FINDING_CONFIDENCE_CHANGED = "RESEARCH_FINDING_CONFIDENCE_CHANGED"
    RESEARCH_FINDING_SCOPE_CHANGED = "RESEARCH_FINDING_SCOPE_CHANGED"
    RESEARCH_FINDING_IMPLICATION_CHANGED = "RESEARCH_FINDING_IMPLICATION_CHANGED"
    PRODUCT_AUTHORITY_CHANGED = "PRODUCT_AUTHORITY_CHANGED"
    PRODUCT_CLAIM_REFERENCE_INVALID = "PRODUCT_CLAIM_REFERENCE_INVALID"


@dataclass(frozen=True, slots=True)
class ProductClaimRef:
    key: str
    text: str


@dataclass(frozen=True, slots=True)
class CreativeClaimMismatch:
    concept_ordinal: int
    message_ordinal: int | None
    category: str
    reference: str | None

    def safe_fields(self) -> dict[str, str | int | None]:
        # Model strings are untrusted: expose only bounded hash-shaped identities,
        # never arbitrary text, secrets, claim prose, or concept/message keys.
        reference = self.reference
        visible = (
            reference is None
            or re.fullmatch(r"\s{0,2}(?:[sS][hH][aA]256:)?[a-fA-F0-9]{64}\s{0,2}", reference)
            is not None
        )
        return {
            "concept_ordinal": self.concept_ordinal,
            "message_ordinal": self.message_ordinal,
            "category": self.category,
            "offending_reference": reference if visible else "[REDACTED_NONCANONICAL_REFERENCE]",
            "reference_digest": canonical_digest(reference),
        }


@dataclass(frozen=True, slots=True)
class CreativeClaimDiagnostic:
    product_snapshot_id: UUID
    allowed_refs: tuple[str, ...]
    mismatches: tuple[CreativeClaimMismatch, ...]

    def safe_fields(self) -> dict[str, object]:
        # Fits the Audit 4096-byte ceiling even at the output contract's upper bound.
        return {
            "diagnostic_version": 1,
            "product_snapshot_id": str(self.product_snapshot_id),
            "allowed_reference_count": len(self.allowed_refs),
            "allowed_references": list(self.allowed_refs[:8]),
            "allowed_references_digest": canonical_digest(self.allowed_refs),
            "allowed_references_truncated": len(self.allowed_refs) > 8,
            "mismatch_count": len(self.mismatches),
            "mismatches": [item.safe_fields() for item in self.mismatches[:5]],
            "mismatches_truncated": len(self.mismatches) > 5,
        }


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
class ApprovedExperimentContext:
    proposal_id: UUID
    proposal_digest: str
    report_id: UUID
    report_digest: str
    data_trust_level: str
    hypothesis: str
    primary_variable: str
    controlled_elements: tuple[str, ...]
    target_metric: str
    platform: str
    measurement_window: str
    creative_direction: str


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
    approved_experiment: ApprovedExperimentContext | None = None

    def refs(self) -> tuple[Mapping[str, object], ...]:
        refs: list[Mapping[str, object]] = [
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
        ]
        if self.approved_experiment is not None:
            refs.append(
                {
                    "kind": "approved_experiment_proposal",
                    "id": str(self.approved_experiment.proposal_id),
                    "digest": self.approved_experiment.proposal_digest,
                    "report_id": str(self.approved_experiment.report_id),
                    "report_digest": self.approved_experiment.report_digest,
                    "data_trust_level": self.approved_experiment.data_trust_level,
                }
            )
        return tuple(refs)


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
    experiment_proposal_id: UUID | None = None
    experiment_proposal_digest: str | None = None

    def __post_init__(self) -> None:
        if not MIN_CONCEPTS <= len(self.concepts) <= MAX_CONCEPTS:
            raise InvalidCreativeOutput("concept set must contain 3 to 5 concepts")
        if any(item.concept_set_id != self.id for item in self.concepts):
            raise InvalidCreativeOutput("concept belongs to another concept set")
        expected = canonical_digest(self.semantic_content())
        if expected != self.semantic_digest:
            raise InvalidCreativeOutput("concept set digest does not match content")

    def semantic_content(self) -> dict[str, object]:
        content: dict[str, object] = {
            "schema_version": self.schema_version,
            "product_snapshot_id": str(self.product_snapshot_id),
            "product_snapshot_digest": self.product_snapshot_digest,
            "research_snapshot_id": str(self.research_snapshot_id),
            "research_snapshot_digest": self.research_snapshot_digest,
            "input_context_digest": self.input_context_digest,
            "concepts": [dict(item.payload) for item in self.concepts],
        }
        if self.experiment_proposal_id is not None:
            content["experiment_proposal_id"] = str(self.experiment_proposal_id)
            content["experiment_proposal_digest"] = self.experiment_proposal_digest
        return content


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


def research_finding_assertion_digest(finding: Finding) -> str:
    """Digest only the assertion a Concept relied on, intentionally excluding citations."""

    return canonical_digest(
        {
            "key": finding.key,
            "category": finding.category.value,
            "statement": finding.statement,
            "confidence": finding.confidence.value,
            "scope": finding.scope,
            "implication": finding.implication,
        }
    )


@dataclass(frozen=True, slots=True)
class RevalidatedFindingAssertion:
    finding_key: str
    original_assertion_digest: str
    current_assertion_digest: str | None

    def primitive(self) -> dict[str, str | None]:
        return {
            "finding_key": self.finding_key,
            "original_assertion_digest": self.original_assertion_digest,
            "current_assertion_digest": self.current_assertion_digest,
        }


@dataclass(frozen=True, slots=True)
class CreativeConceptRevalidation:
    tenant_id: UUID
    product_id: UUID
    concept_id: UUID
    concept_digest: str
    original_concept_set_id: UUID
    original_research_snapshot_id: UUID
    original_research_snapshot_digest: str
    current_research_snapshot_id: UUID
    current_research_snapshot_digest: str
    product_snapshot_id: UUID
    product_snapshot_digest: str
    original_decision_id: UUID
    result: CreativeRevalidationResult
    reason_codes: tuple[CreativeRevalidationReason, ...]
    referenced_finding_assertions: tuple[RevalidatedFindingAssertion, ...]
    created_by: UUID
    semantic_digest: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("revalidation reason codes must be unique")
        if self.result is CreativeRevalidationResult.REVALIDATED_FOR_PRODUCTION:
            if self.reason_codes or any(
                item.current_assertion_digest != item.original_assertion_digest
                for item in self.referenced_finding_assertions
            ):
                raise ValueError("successful revalidation cannot contain authority mismatches")
        elif not self.reason_codes:
            raise ValueError("restrategy revalidation requires at least one reason")
        if self.semantic_digest != canonical_digest(self.semantic_content()):
            raise ValueError("revalidation digest does not match immutable authority")

    def semantic_content(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "concept_id": str(self.concept_id),
            "concept_digest": self.concept_digest,
            "original_concept_set_id": str(self.original_concept_set_id),
            "original_research_snapshot_id": str(self.original_research_snapshot_id),
            "original_research_snapshot_digest": self.original_research_snapshot_digest,
            "current_research_snapshot_id": str(self.current_research_snapshot_id),
            "current_research_snapshot_digest": self.current_research_snapshot_digest,
            "product_snapshot_id": str(self.product_snapshot_id),
            "product_snapshot_digest": self.product_snapshot_digest,
            "original_decision_id": str(self.original_decision_id),
            "result": self.result.value,
            "reason_codes": [item.value for item in self.reason_codes],
            "referenced_finding_assertions": [
                item.primitive() for item in self.referenced_finding_assertions
            ],
        }


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
