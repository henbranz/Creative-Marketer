from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from .domain import MeasurementProviderFailed, ProviderMetric, ProviderMetricPage


class SocialMetricsProvider(Protocol):
    async def collect(self, external_post_id: str, cursor: str | None) -> ProviderMetricPage: ...


@dataclass(slots=True)
class FakeSocialMetricsProvider:
    """A deterministic, zero-network metrics source with explicit cursor states."""

    sequences: Mapping[str, tuple[Mapping[str, int | str | Decimal | None], ...]] | None = None
    fail_posts: frozenset[str] = frozenset()

    async def collect(self, external_post_id: str, cursor: str | None) -> ProviderMetricPage:
        if external_post_id in self.fail_posts:
            raise MeasurementProviderFailed("fake metrics provider failure")
        sequence = (self.sequences or {}).get(external_post_id) or (
            {
                "impressions": 100,
                "reach": 80,
                "video_views": 65,
                "likes": 8,
                "comments": 2,
                "shares": 1,
                "saves": 3,
                "clicks": 5,
            },
            {
                "impressions": 250,
                "reach": 190,
                "video_views": 160,
                "likes": 18,
                "comments": 4,
                "shares": 3,
                "saves": 7,
                "clicks": 14,
            },
            {
                "impressions": 500,
                "reach": 370,
                "video_views": 330,
                "likes": 35,
                "comments": 8,
                "shares": 6,
                "saves": 12,
                "clicks": 31,
            },
        )
        try:
            index = int(cursor.removeprefix("fake:")) if cursor else 0
        except ValueError as error:
            raise MeasurementProviderFailed("invalid fake cursor") from error
        index = min(index, len(sequence) - 1)
        observed_at = datetime(2026, 9, 16, 12 + index, tzinfo=UTC)
        metrics = tuple(
            ProviderMetric(key, Decimal(str(value)), observed_at)
            for key, value in sorted(sequence[index].items())
            if value is not None
        )
        next_cursor = f"fake:{min(index + 1, len(sequence) - 1)}"
        return ProviderMetricPage(metrics, next_cursor)
