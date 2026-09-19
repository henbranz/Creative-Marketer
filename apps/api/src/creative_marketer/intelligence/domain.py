from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from statistics import median
from types import MappingProxyType
from uuid import UUID, uuid4

from creative_marketer.agent_runtime.domain import canonical_digest

FEATURE_VERSION = "creative-features-v1"
COMPARABILITY_VERSION = "intelligence-comparability-v1"
CALCULATION_VERSION = "intelligence-calculation-v1"
REPORT_SCHEMA_VERSION = 1
MIN_BASELINE_SAMPLE = 3


def canonical_decimal(value: Decimal) -> str:
    """Stable decimal text across PostgreSQL numeric scale normalization."""

    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


MATCHED_WINDOWS = ("+1h", "+6h", "+24h", "+72h", "+7d")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
CAUSAL_LANGUAGE = re.compile(
    r"\b(caused?|proves?|proven(?:\s+winner)?|winners?|guaranteed|customers?\s+prefer|works?\s+better)\b",
    re.IGNORECASE,
)
SPEND_LANGUAGE = re.compile(
    r"\b(increase|decrease|raise|lower|optimi[sz]e)\s+(the\s+)?(ad\s+)?budget\b|\b(roas|cpa|cpc|cpm)\b",
    re.IGNORECASE,
)


class IntelligenceError(Exception):
    code = "INTELLIGENCE_ERROR"


class IntelligenceNotFound(IntelligenceError):
    code = "INTELLIGENCE_NOT_FOUND"


class IntelligencePermissionDenied(IntelligenceError):
    code = "INTELLIGENCE_PERMISSION_DENIED"


class IntelligenceNotReady(IntelligenceError):
    code = "INTELLIGENCE_NOT_READY"


class InvalidIntelligenceOutput(IntelligenceError):
    code = "INVALID_INTELLIGENCE_OUTPUT"


class InsightTransitionDenied(IntelligenceError):
    code = "INSIGHT_TRANSITION_DENIED"


class ExperimentHandoffDenied(IntelligenceError):
    code = "EXPERIMENT_HANDOFF_DENIED"


class DataTrustLevel(StrEnum):
    SYNTHETIC = "SYNTHETIC"
    OBSERVED = "OBSERVED"


class Confidence(StrEnum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"


class InsightStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    PROPOSED = "PROPOSED"
    REJECTED = "REJECTED"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    SUPERSEDED = "SUPERSEDED"


class InsightDecisionKind(StrEnum):
    PROPOSE_FOR_TESTING = "PROPOSE_FOR_TESTING"
    REJECT = "REJECT"


class ExperimentDecisionKind(StrEnum):
    APPROVED_FOR_CREATIVE = "APPROVED_FOR_CREATIVE"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class CanonicalRef:
    kind: str
    id: UUID
    digest: str | None = None

    def __post_init__(self) -> None:
        if self.digest is not None and not DIGEST.fullmatch(self.digest):
            raise ValueError("canonical reference digest is invalid")

    def primitive(self) -> dict[str, str]:
        value = {"kind": self.kind, "id": str(self.id)}
        if self.digest is not None:
            value["digest"] = self.digest
        return value


@dataclass(frozen=True, slots=True)
class CreativeFeatureSnapshot:
    tenant_id: UUID
    product_id: UUID
    publication_id: UUID
    final_creative_id: UUID
    features: Mapping[str, object]
    semantic_digest: str
    id: UUID = field(default_factory=uuid4)
    extraction_version: str = FEATURE_VERSION
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))
        expected = canonical_digest(
            {
                "extraction_version": self.extraction_version,
                "product_id": str(self.product_id),
                "publication_id": str(self.publication_id),
                "final_creative_id": str(self.final_creative_id),
                "features": dict(self.features),
            }
        )
        if self.extraction_version != FEATURE_VERSION or self.semantic_digest != expected:
            raise ValueError("creative feature snapshot is not canonical")

    @classmethod
    def extract(
        cls,
        *,
        tenant_id: UUID,
        product_id: UUID,
        publication_id: UUID,
        final_creative_id: UUID,
        concept: Mapping[str, object] | None = None,
        production_plan: Mapping[str, object] | None = None,
        assembly_plan: Mapping[str, object] | None = None,
        final_creative: Mapping[str, object] | None = None,
        publication: Mapping[str, object] | None = None,
    ) -> CreativeFeatureSnapshot:
        features = extract_canonical_features(
            concept=concept or {},
            production_plan=production_plan or {},
            assembly_plan=assembly_plan or {},
            final_creative=final_creative or {},
            publication=publication or {},
        )
        content = {
            "extraction_version": FEATURE_VERSION,
            "product_id": str(product_id),
            "publication_id": str(publication_id),
            "final_creative_id": str(final_creative_id),
            "features": features,
        }
        return cls(
            tenant_id,
            product_id,
            publication_id,
            final_creative_id,
            features,
            canonical_digest(content),
        )


def _first(source: Mapping[str, object], *keys: str) -> object | None:
    for key in keys:
        value = source.get(key)
        if value is not None and value != "":
            return value
    return None


def extract_canonical_features(
    *,
    concept: Mapping[str, object],
    production_plan: Mapping[str, object],
    assembly_plan: Mapping[str, object],
    final_creative: Mapping[str, object],
    publication: Mapping[str, object],
) -> dict[str, object]:
    """Extract only explicitly persisted metadata; never inspect media bytes."""

    features: dict[str, object] = {}
    hook = concept.get("hook")
    if isinstance(hook, Mapping):
        hook_type = _first(hook, "type", "hook_type")
        if hook_type is not None:
            features["hook_type"] = str(hook_type)
    angle = _first(concept, "creative_angle", "angle")
    if angle is not None:
        features["creative_angle"] = str(angle)
    duration = _first(final_creative, "duration_seconds", "duration") or _first(
        concept, "estimated_duration_seconds", "duration_seconds"
    )
    if duration is not None:
        normalized_duration = Decimal(str(duration))
        features["duration_seconds"] = (
            int(normalized_duration)
            if normalized_duration == normalized_duration.to_integral_value()
            else format(normalized_duration.normalize(), "f")
        )
    scenes = concept.get("scenes")
    if isinstance(scenes, (list, tuple)):
        features["shot_count"] = len(scenes)
    cta = concept.get("cta")
    if isinstance(cta, Mapping):
        features["cta_present"] = True
        cta_type = _first(cta, "type", "cta_type")
        if cta_type is not None:
            features["cta_type"] = str(cta_type)
    elif isinstance(cta, str) and cta.strip():
        features["cta_present"] = True
    caption = _first(publication, "caption", "caption_text")
    if isinstance(caption, str):
        features["caption_present"] = bool(caption.strip())
    for name, source, keys in (
        ("aspect_ratio", final_creative, ("aspect_ratio",)),
        ("platform", publication, ("platform",)),
        ("publication_mode", publication, ("publication_mode", "mode")),
        ("media_type", final_creative, ("media_type", "kind")),
        ("end_card", assembly_plan, ("end_card", "has_end_card")),
        ("product_visibility_strategy", production_plan, ("product_visibility_strategy",)),
        ("generated_source_count", assembly_plan, ("generated_source_count",)),
        ("existing_source_count", assembly_plan, ("existing_source_count",)),
    ):
        value = _first(source, *keys)
        if value is not None:
            features[name] = value
    return dict(sorted(features.items()))


@dataclass(frozen=True, slots=True)
class ComparableSnapshot:
    snapshot_id: UUID
    snapshot_digest: str
    publication_id: UUID
    final_creative_id: UUID
    tenant_id: UUID
    product_id: UUID
    platform: str
    publication_type: str
    window: str
    metrics: Mapping[str, Decimal]
    trust_level: DataTrustLevel

    def __post_init__(self) -> None:
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))
        if self.window not in MATCHED_WINDOWS or not DIGEST.fullmatch(self.snapshot_digest):
            raise ValueError("comparable snapshot is invalid")


def are_comparable(subject: ComparableSnapshot, candidate: ComparableSnapshot) -> bool:
    return (
        subject.tenant_id == candidate.tenant_id
        and subject.product_id == candidate.product_id
        and subject.publication_id != candidate.publication_id
        and subject.platform == candidate.platform
        and subject.publication_type == candidate.publication_type
        and subject.window == candidate.window
        and bool(set(subject.metrics).intersection(candidate.metrics))
    )


@dataclass(frozen=True, slots=True)
class PerformanceComparison:
    tenant_id: UUID
    product_id: UUID
    publication_id: UUID
    final_creative_id: UUID
    comparison_window: str
    metric_key: str
    observed_value: Decimal
    baseline_value: Decimal
    absolute_delta: Decimal
    relative_delta: Decimal | None
    sample_size: int
    baseline_publication_ids: tuple[UUID, ...]
    source_snapshot_ids: tuple[UUID, ...]
    data_trust_level: DataTrustLevel
    semantic_digest: str
    id: UUID = field(default_factory=uuid4)
    comparability_policy_version: str = COMPARABILITY_VERSION
    calculation_version: str = CALCULATION_VERSION
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.comparison_window not in MATCHED_WINDOWS or self.sample_size < MIN_BASELINE_SAMPLE:
            raise ValueError("performance comparison lacks a reliable matched baseline")
        if self.absolute_delta != self.observed_value - self.baseline_value:
            raise ValueError("performance comparison delta is inconsistent")
        expected_relative = (
            None
            if self.baseline_value == 0
            else (self.absolute_delta / self.baseline_value).quantize(Decimal("0.000001"))
        )
        if self.relative_delta != expected_relative:
            raise ValueError("performance comparison relative delta is inconsistent")
        if self.semantic_digest != canonical_digest(self.semantic_content()):
            raise ValueError("performance comparison digest mismatch")

    def semantic_content(self) -> dict[str, object]:
        return {
            "publication_id": str(self.publication_id),
            "final_creative_id": str(self.final_creative_id),
            "comparison_window": self.comparison_window,
            "metric_key": self.metric_key,
            "observed_value": canonical_decimal(self.observed_value),
            "baseline_value": canonical_decimal(self.baseline_value),
            "absolute_delta": canonical_decimal(self.absolute_delta),
            "relative_delta": canonical_decimal(self.relative_delta)
            if self.relative_delta is not None
            else None,
            "sample_size": self.sample_size,
            "baseline_publication_ids": [str(item) for item in self.baseline_publication_ids],
            "source_snapshot_ids": [str(item) for item in self.source_snapshot_ids],
            "comparability_policy_version": self.comparability_policy_version,
            "calculation_version": self.calculation_version,
            "data_trust_level": self.data_trust_level.value,
        }


def build_comparisons(
    subject: ComparableSnapshot,
    candidates: Iterable[ComparableSnapshot],
) -> tuple[PerformanceComparison, ...]:
    population = tuple(item for item in candidates if are_comparable(subject, item))
    results: list[PerformanceComparison] = []
    for metric_key in sorted(subject.metrics):
        metric_population = tuple(item for item in population if metric_key in item.metrics)
        if len(metric_population) < MIN_BASELINE_SAMPLE:
            continue
        baseline = Decimal(str(median([item.metrics[metric_key] for item in metric_population])))
        observed = subject.metrics[metric_key]
        absolute = observed - baseline
        relative = None if baseline == 0 else (absolute / baseline).quantize(Decimal("0.000001"))
        trust = (
            DataTrustLevel.SYNTHETIC
            if subject.trust_level is DataTrustLevel.SYNTHETIC
            or any(item.trust_level is DataTrustLevel.SYNTHETIC for item in metric_population)
            else DataTrustLevel.OBSERVED
        )
        content = {
            "publication_id": str(subject.publication_id),
            "final_creative_id": str(subject.final_creative_id),
            "comparison_window": subject.window,
            "metric_key": metric_key,
            "observed_value": canonical_decimal(observed),
            "baseline_value": canonical_decimal(baseline),
            "absolute_delta": canonical_decimal(absolute),
            "relative_delta": canonical_decimal(relative) if relative is not None else None,
            "sample_size": len(metric_population),
            "baseline_publication_ids": [str(item.publication_id) for item in metric_population],
            "source_snapshot_ids": [
                str(subject.snapshot_id),
                *[str(item.snapshot_id) for item in metric_population],
            ],
            "comparability_policy_version": COMPARABILITY_VERSION,
            "calculation_version": CALCULATION_VERSION,
            "data_trust_level": trust.value,
        }
        results.append(
            PerformanceComparison(
                subject.tenant_id,
                subject.product_id,
                subject.publication_id,
                subject.final_creative_id,
                subject.window,
                metric_key,
                observed,
                baseline,
                absolute,
                relative,
                len(metric_population),
                tuple(item.publication_id for item in metric_population),
                tuple([subject.snapshot_id, *[item.snapshot_id for item in metric_population]]),
                trust,
                canonical_digest(content),
            )
        )
    return tuple(results)


@dataclass(frozen=True, slots=True)
class IntelligenceContextManifest:
    tenant_id: UUID
    product_id: UUID
    product_snapshot: CanonicalRef
    research_snapshot: CanonicalRef | None
    creative_concepts: tuple[CanonicalRef, ...]
    final_creatives: tuple[CanonicalRef, ...]
    publications: tuple[CanonicalRef, ...]
    performance_snapshots: tuple[CanonicalRef, ...]
    attribution_results: tuple[CanonicalRef, ...]
    comparison_ids: tuple[UUID, ...]
    feature_snapshot_ids: tuple[UUID, ...]
    data_trust_level: DataTrustLevel
    semantic_digest: str
    id: UUID = field(default_factory=uuid4)
    comparison_policy_version: str = COMPARABILITY_VERSION
    feature_extraction_version: str = FEATURE_VERSION
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.semantic_digest != canonical_digest(self.semantic_content()):
            raise ValueError("intelligence context manifest digest mismatch")

    def semantic_content(self) -> dict[str, object]:
        return {
            "product_snapshot": self.product_snapshot.primitive(),
            "research_snapshot": self.research_snapshot.primitive()
            if self.research_snapshot
            else None,
            "creative_concepts": [item.primitive() for item in self.creative_concepts],
            "final_creatives": [item.primitive() for item in self.final_creatives],
            "publications": [item.primitive() for item in self.publications],
            "performance_snapshots": [item.primitive() for item in self.performance_snapshots],
            "attribution_results": [item.primitive() for item in self.attribution_results],
            "comparison_ids": [str(item) for item in self.comparison_ids],
            "feature_snapshot_ids": [str(item) for item in self.feature_snapshot_ids],
            "comparison_policy_version": self.comparison_policy_version,
            "feature_extraction_version": self.feature_extraction_version,
            "data_trust_level": self.data_trust_level.value,
        }


@dataclass(frozen=True, slots=True)
class IntelligenceReport:
    tenant_id: UUID
    product_id: UUID
    agent_run_id: UUID
    agent_version_id: UUID
    context_manifest_id: UUID
    context_manifest_digest: str
    data_trust_level: DataTrustLevel
    summary: str
    observations: tuple[Mapping[str, object], ...]
    comparative_findings: tuple[Mapping[str, object], ...]
    limitations: tuple[str, ...]
    semantic_digest: str
    id: UUID = field(default_factory=uuid4)
    schema_version: int = REPORT_SCHEMA_VERSION
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observations", tuple(MappingProxyType(dict(x)) for x in self.observations)
        )
        object.__setattr__(
            self,
            "comparative_findings",
            tuple(MappingProxyType(dict(x)) for x in self.comparative_findings),
        )
        if not self.limitations:
            raise InvalidIntelligenceOutput("every IntelligenceReport requires limitations")
        text = " ".join(
            [self.summary, *self.limitations, *[str(dict(x)) for x in self.comparative_findings]]
        )
        reject_unsafe_claims(text)
        if self.data_trust_level is DataTrustLevel.SYNTHETIC and not any(
            "synthetic" in item.casefold() for item in self.limitations
        ):
            raise InvalidIntelligenceOutput("synthetic source limitation is required")
        if self.semantic_digest != canonical_digest(self.semantic_content()):
            raise InvalidIntelligenceOutput("report digest mismatch")

    def semantic_content(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "context_manifest_id": str(self.context_manifest_id),
            "context_manifest_digest": self.context_manifest_digest,
            "data_trust_level": self.data_trust_level.value,
            "summary": self.summary,
            "observations": [dict(item) for item in self.observations],
            "comparative_findings": [dict(item) for item in self.comparative_findings],
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class IntelligenceResult:
    """One immutable accepted model result and its governed child artifacts."""

    report: IntelligenceReport
    candidates: tuple[InsightCandidate, ...]
    proposals: tuple[ExperimentProposal, ...]


@dataclass(frozen=True, slots=True)
class InsightCandidate:
    tenant_id: UUID
    product_id: UUID
    report_id: UUID
    statement: str
    evidence_refs: tuple[CanonicalRef, ...]
    sample_size: int
    metric: str
    baseline: Decimal | None
    observed_delta: Decimal | None
    confidence: Confidence
    scope: Mapping[str, object]
    limitations: tuple[str, ...]
    data_trust_level: DataTrustLevel
    semantic_digest: str
    id: UUID = field(default_factory=uuid4)
    status: InsightStatus = InsightStatus.CANDIDATE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    valid_until: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope", MappingProxyType(dict(self.scope)))
        reject_unsafe_claims(self.statement)
        if self.status is not InsightStatus.CANDIDATE:
            raise InsightTransitionDenied("agents may create CANDIDATE insights only")
        if (
            self.data_trust_level is DataTrustLevel.SYNTHETIC
            and self.confidence is not Confidence.LOW
        ):
            raise InvalidIntelligenceOutput("synthetic evidence caps confidence at LOW")
        if self.sample_size < MIN_BASELINE_SAMPLE and self.confidence is not Confidence.LOW:
            raise InvalidIntelligenceOutput("small samples cap confidence at LOW")
        if not self.evidence_refs or not self.limitations:
            raise InvalidIntelligenceOutput("candidate evidence and limitations are required")
        if self.semantic_digest != canonical_digest(self.semantic_content()):
            raise InvalidIntelligenceOutput("candidate digest mismatch")

    def semantic_content(self) -> dict[str, object]:
        return {
            "report_id": str(self.report_id),
            "statement": self.statement,
            "evidence_refs": [item.primitive() for item in self.evidence_refs],
            "sample_size": self.sample_size,
            "metric": self.metric,
            "baseline": str(self.baseline) if self.baseline is not None else None,
            "observed_delta": str(self.observed_delta) if self.observed_delta is not None else None,
            "confidence": self.confidence.value,
            "scope": dict(self.scope),
            "limitations": list(self.limitations),
            "data_trust_level": self.data_trust_level.value,
        }


@dataclass(frozen=True, slots=True)
class InsightDecision:
    tenant_id: UUID
    product_id: UUID
    candidate_id: UUID
    candidate_digest: str
    decision: InsightDecisionKind
    decided_by: UUID
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ExperimentProposal:
    tenant_id: UUID
    product_id: UUID
    source_intelligence_report_id: UUID
    source_intelligence_report_digest: str
    source_insight_candidate_ids: tuple[UUID, ...]
    hypothesis: str
    primary_variable: str
    controlled_elements: tuple[str, ...]
    target_metric: str
    platform: str
    recommended_measurement_window: str
    creative_direction: str
    rationale: str
    expected_learning: str
    data_trust_level: DataTrustLevel
    semantic_digest: str
    id: UUID = field(default_factory=uuid4)
    schema_version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.primary_variable.strip() or not self.controlled_elements:
            raise InvalidIntelligenceOutput("experiment must isolate a primary variable")
        if self.recommended_measurement_window not in MATCHED_WINDOWS:
            raise InvalidIntelligenceOutput("experiment measurement window is unsupported")
        reject_unsafe_claims(" ".join((self.hypothesis, self.rationale, self.expected_learning)))
        if self.semantic_digest != canonical_digest(self.semantic_content()):
            raise InvalidIntelligenceOutput("experiment proposal digest mismatch")

    def semantic_content(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_intelligence_report_id": str(self.source_intelligence_report_id),
            "source_intelligence_report_digest": self.source_intelligence_report_digest,
            "source_insight_candidate_ids": [
                str(item) for item in self.source_insight_candidate_ids
            ],
            "hypothesis": self.hypothesis,
            "primary_variable": self.primary_variable,
            "controlled_elements": list(self.controlled_elements),
            "target_metric": self.target_metric,
            "platform": self.platform,
            "recommended_measurement_window": self.recommended_measurement_window,
            "creative_direction": self.creative_direction,
            "rationale": self.rationale,
            "expected_learning": self.expected_learning,
            "data_trust_level": self.data_trust_level.value,
        }


@dataclass(frozen=True, slots=True)
class ExperimentDecision:
    tenant_id: UUID
    product_id: UUID
    proposal_id: UUID
    proposal_digest: str
    decision: ExperimentDecisionKind
    decided_by: UUID
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def reject_unsafe_claims(text: str) -> None:
    if CAUSAL_LANGUAGE.search(text):
        raise InvalidIntelligenceOutput("unsupported causal certainty is prohibited")
    if SPEND_LANGUAGE.search(text):
        raise InvalidIntelligenceOutput("spend or ROAS recommendations are prohibited")


def current_insight_status(
    candidate: InsightCandidate, decisions: Iterable[InsightDecision]
) -> InsightStatus:
    latest = max(decisions, key=lambda value: (value.created_at, str(value.id)), default=None)
    if latest is None:
        return InsightStatus.CANDIDATE
    if latest.candidate_id != candidate.id or latest.candidate_digest != candidate.semantic_digest:
        raise InsightTransitionDenied("decision does not bind the exact candidate")
    return (
        InsightStatus.PROPOSED
        if latest.decision is InsightDecisionKind.PROPOSE_FOR_TESTING
        else InsightStatus.REJECTED
    )
