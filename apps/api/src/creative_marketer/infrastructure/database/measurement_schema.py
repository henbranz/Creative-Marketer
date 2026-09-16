from typing import Any

from sqlalchemy import Column, DateTime, Integer, MetaData, Numeric, String, Table
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from creative_marketer.infrastructure.database.schema import NAMING_CONVENTION

metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _table(name: str, *columns: Any) -> Table:
    return Table(
        name,
        metadata,
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("tenant_id", UUID(as_uuid=True), nullable=False),
        *columns,
        schema="measurement",
    )


performance_observations = _table(
    "performance_observations",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("publication_id", UUID(as_uuid=True), nullable=False),
    Column("metric_key", String(64), nullable=False),
    Column("semantics", String(16), nullable=False),
    Column("value", Numeric(30, 9), nullable=False),
    Column("unit", String(32), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("provider", String(64), nullable=False),
    Column("provider_version", String(128), nullable=False),
    Column("source_digest", String(71), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
collection_runs = _table(
    "collection_runs",
    Column("publication_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(16), nullable=False),
    Column("checkpoint", String(64), nullable=False),
    Column("cursor", String(256)),
    Column("failure_code", String(64)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
)
attribution_references = _table(
    "attribution_references",
    Column("publication_id", UUID(as_uuid=True), nullable=False),
    Column("public_code_hash", String(64), nullable=False),
    Column("destination_url", String(2000), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
conversion_observations = _table(
    "conversion_observations",
    Column("source", String(64), nullable=False),
    Column("external_id", String(256), nullable=False),
    Column("amount", Numeric(30, 9), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("attribution_code_hash", String(64)),
    Column("semantic_digest", String(71), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
attribution_results = _table(
    "attribution_results",
    Column("conversion_observation_id", UUID(as_uuid=True), nullable=False),
    Column("attribution_reference_id", UUID(as_uuid=True), nullable=False),
    Column("publication_id", UUID(as_uuid=True), nullable=False),
    Column("method", String(32), nullable=False),
    Column("formula_version", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
performance_snapshots = _table(
    "performance_snapshots",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("publication_id", UUID(as_uuid=True), nullable=False),
    Column("observation_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
    Column("latest_metrics", JSONB, nullable=False),
    Column("derived_metrics", JSONB, nullable=False),
    Column("attributed_conversions", Integer, nullable=False),
    Column("attributed_revenue", JSONB, nullable=False),
    Column("freshness", String(16), nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
