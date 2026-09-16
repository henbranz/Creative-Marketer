from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID, uuid4


class MeasurementError(Exception):
    code = "MEASUREMENT_ERROR"


class MeasurementNotFound(MeasurementError):
    code = "MEASUREMENT_NOT_FOUND"


class MeasurementPermissionDenied(MeasurementError):
    code = "MEASUREMENT_PERMISSION_DENIED"


class MeasurementProviderFailed(MeasurementError):
    code = "MEASUREMENT_PROVIDER_FAILED"


class AttributionReferenceInvalid(MeasurementError):
    code = "ATTRIBUTION_REFERENCE_INVALID"


class ConversionConflict(MeasurementError):
    code = "CONVERSION_IDEMPOTENCY_CONFLICT"


class MetricSemantics(StrEnum):
    CUMULATIVE = "CUMULATIVE"
    INTERVAL = "INTERVAL"
    DURATION = "DURATION"
    RATIO = "RATIO"


class CollectionStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class Freshness(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    NO_DATA = "NO_DATA"


class AttributionMethod(StrEnum):
    DIRECT_REFERENCE = "DIRECT_REFERENCE"


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    key: str
    semantics: MetricSemantics
    unit: str


METRIC_TAXONOMY: Mapping[str, MetricDefinition] = MappingProxyType(
    {
        key: MetricDefinition(key, semantics, unit)
        for key, semantics, unit in (
            ("impressions", MetricSemantics.CUMULATIVE, "count"),
            ("reach", MetricSemantics.CUMULATIVE, "count"),
            ("video_views", MetricSemantics.CUMULATIVE, "count"),
            ("likes", MetricSemantics.CUMULATIVE, "count"),
            ("comments", MetricSemantics.CUMULATIVE, "count"),
            ("shares", MetricSemantics.CUMULATIVE, "count"),
            ("saves", MetricSemantics.CUMULATIVE, "count"),
            ("clicks", MetricSemantics.CUMULATIVE, "count"),
            ("watch_time_seconds", MetricSemantics.DURATION, "seconds"),
            ("completions", MetricSemantics.CUMULATIVE, "count"),
            ("profile_visits", MetricSemantics.CUMULATIVE, "count"),
        )
    }
)


def _digest(value: Mapping[str, object]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ProviderMetric:
    key: str
    value: Decimal
    observed_at: datetime
    available: bool = True

    def __post_init__(self) -> None:
        if self.key not in METRIC_TAXONOMY:
            raise ValueError("unsupported normalized metric")
        if self.value < 0 or not self.value.is_finite():
            raise ValueError("metric value must be finite and non-negative")
        if self.observed_at.tzinfo is None:
            raise ValueError("metric observation time must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ProviderMetricPage:
    metrics: tuple[ProviderMetric, ...]
    next_cursor: str | None
    provider: str = "fake"
    provider_version: str = "fake-v1"

    def __post_init__(self) -> None:
        if self.provider != "fake":
            raise ValueError("real social metrics providers are not enabled")


@dataclass(frozen=True, slots=True)
class PerformanceObservation:
    tenant_id: UUID
    product_id: UUID
    publication_id: UUID
    metric_key: str
    semantics: MetricSemantics
    value: Decimal
    unit: str
    observed_at: datetime
    provider: str
    provider_version: str
    source_digest: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        definition = METRIC_TAXONOMY.get(self.metric_key)
        if definition is None or definition.semantics is not self.semantics:
            raise ValueError("metric taxonomy/semantics mismatch")
        if definition.unit != self.unit or self.value < 0 or not self.value.is_finite():
            raise ValueError("metric value/unit is invalid")
        if self.observed_at.tzinfo is None or self.provider != "fake":
            raise ValueError("observation source is invalid")

    @classmethod
    def from_provider(
        cls,
        *,
        tenant_id: UUID,
        product_id: UUID,
        publication_id: UUID,
        metric: ProviderMetric,
        provider: str,
        provider_version: str,
    ) -> PerformanceObservation:
        definition = METRIC_TAXONOMY[metric.key]
        material = {
            "publication_id": str(publication_id),
            "metric_key": metric.key,
            "semantics": definition.semantics.value,
            "value": str(metric.value),
            "unit": definition.unit,
            "observed_at": metric.observed_at.astimezone(UTC).isoformat(),
            "provider": provider,
            "provider_version": provider_version,
        }
        return cls(
            tenant_id,
            product_id,
            publication_id,
            metric.key,
            definition.semantics,
            metric.value,
            definition.unit,
            metric.observed_at,
            provider,
            provider_version,
            _digest(material),
        )


@dataclass(frozen=True, slots=True)
class DerivedMetric:
    key: str
    value: Decimal | None
    formula_version: str
    unavailable_reason: str | None = None


FORMULA_VERSION = "measurement-formulas-v1"


def _ratio(
    numerator: Decimal | None, denominator: Decimal | None
) -> tuple[Decimal | None, str | None]:
    if numerator is None or denominator is None:
        return None, "MISSING_INPUT"
    if denominator == 0:
        return None, "ZERO_DENOMINATOR"
    return numerator / denominator, None


def derive_metrics(
    latest: Mapping[str, Decimal], *, attributed_conversions: int
) -> tuple[DerivedMetric, ...]:
    ctr, ctr_reason = _ratio(latest.get("clicks"), latest.get("impressions"))
    engagement_inputs = ("likes", "comments", "shares", "saves")
    engagement_numerator = (
        sum((latest[key] for key in engagement_inputs), Decimal(0))
        if all(key in latest for key in engagement_inputs)
        else None
    )
    engagement, engagement_reason = _ratio(engagement_numerator, latest.get("impressions"))
    conversion, conversion_reason = _ratio(Decimal(attributed_conversions), latest.get("clicks"))
    return (
        DerivedMetric("ctr", ctr, FORMULA_VERSION, ctr_reason),
        DerivedMetric("engagement_rate", engagement, FORMULA_VERSION, engagement_reason),
        DerivedMetric("conversion_rate", conversion, FORMULA_VERSION, conversion_reason),
    )


@dataclass(frozen=True, slots=True)
class AttributionReference:
    tenant_id: UUID
    publication_id: UUID
    public_code_hash: str
    destination_url: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @staticmethod
    def issue_code() -> str:
        return "cmr_" + secrets.token_urlsafe(32)

    @staticmethod
    def hash_code(code: str) -> str:
        if not code.startswith("cmr_") or len(code) < 40:
            raise AttributionReferenceInvalid("invalid attribution reference")
        return hashlib.sha256(code.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ConversionObservation:
    tenant_id: UUID
    source: str
    external_id: str
    amount: Decimal
    currency: str
    observed_at: datetime
    attribution_code_hash: str | None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.source != "fake" or not self.external_id.strip():
            raise ValueError("conversion source is invalid")
        if self.amount < 0 or not self.amount.is_finite():
            raise ValueError("conversion amount is invalid")
        if len(self.currency) != 3 or not self.currency.isalpha() or not self.currency.isupper():
            raise ValueError("currency must be an ISO-style uppercase code")
        if self.observed_at.tzinfo is None:
            raise ValueError("conversion observation time must be timezone-aware")

    @property
    def semantic_digest(self) -> str:
        return _digest(
            {
                "source": self.source,
                "external_id": self.external_id,
                "amount": str(self.amount),
                "currency": self.currency,
                "observed_at": self.observed_at.astimezone(UTC).isoformat(),
                "attribution_code_hash": self.attribution_code_hash,
            }
        )


@dataclass(frozen=True, slots=True)
class AttributionResult:
    tenant_id: UUID
    conversion_observation_id: UUID
    attribution_reference_id: UUID
    publication_id: UUID
    method: AttributionMethod = AttributionMethod.DIRECT_REFERENCE
    formula_version: str = "direct-reference-v1"
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class PerformanceSnapshot:
    tenant_id: UUID
    product_id: UUID
    publication_id: UUID
    observation_ids: tuple[UUID, ...]
    latest_metrics: Mapping[str, Decimal]
    derived_metrics: tuple[DerivedMetric, ...]
    attributed_conversions: int
    attributed_revenue: Mapping[str, Decimal]
    freshness: Freshness
    semantic_digest: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        object.__setattr__(self, "latest_metrics", MappingProxyType(dict(self.latest_metrics)))
        object.__setattr__(
            self, "attributed_revenue", MappingProxyType(dict(self.attributed_revenue))
        )

    @classmethod
    def build(
        cls,
        *,
        tenant_id: UUID,
        product_id: UUID,
        publication_id: UUID,
        observations: Iterable[PerformanceObservation],
        attributed_conversions: int,
        attributed_revenue: Mapping[str, Decimal],
        freshness: Freshness,
    ) -> PerformanceSnapshot:
        ordered = sorted(
            observations, key=lambda item: (item.observed_at, item.created_at, item.id)
        )
        latest: dict[str, Decimal] = {}
        ids: list[UUID] = []
        for item in ordered:
            latest[item.metric_key] = item.value
            ids.append(item.id)
        derived = derive_metrics(latest, attributed_conversions=attributed_conversions)
        material = {
            "publication_id": str(publication_id),
            "observation_ids": [str(value) for value in ids],
            "latest_metrics": {key: str(value) for key, value in sorted(latest.items())},
            "derived_metrics": [
                {
                    "key": item.key,
                    "value": str(item.value) if item.value is not None else None,
                    "formula_version": item.formula_version,
                    "unavailable_reason": item.unavailable_reason,
                }
                for item in derived
            ],
            "attributed_conversions": attributed_conversions,
            "attributed_revenue": {
                key: str(value) for key, value in sorted(attributed_revenue.items())
            },
            "freshness": freshness.value,
        }
        return cls(
            tenant_id,
            product_id,
            publication_id,
            tuple(ids),
            latest,
            derived,
            attributed_conversions,
            attributed_revenue,
            freshness,
            _digest(material),
        )


@dataclass(frozen=True, slots=True)
class CollectionRun:
    tenant_id: UUID
    publication_id: UUID
    status: CollectionStatus
    checkpoint: str
    cursor: str | None = None
    failure_code: str | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
