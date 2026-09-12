from typing import Any

from sqlalchemy import Column, DateTime, Integer, MetaData, Numeric, String, Table
from sqlalchemy.dialects.postgresql import JSONB, UUID

from creative_marketer.infrastructure.database.schema import NAMING_CONVENTION

metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _table(name: str, *columns: Any) -> Table:
    return Table(
        name,
        metadata,
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("tenant_id", UUID(as_uuid=True), nullable=False),
        *columns,
        schema="production",
    )


production_plans = _table(
    "production_plans",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("agent_run_id", UUID(as_uuid=True), nullable=False),
    Column("concept_id", UUID(as_uuid=True), nullable=False),
    Column("concept_digest", String(71), nullable=False),
    Column("concept_set_id", UUID(as_uuid=True), nullable=False),
    Column("concept_set_digest", String(71), nullable=False),
    Column("product_snapshot_id", UUID(as_uuid=True), nullable=False),
    Column("product_snapshot_digest", String(71), nullable=False),
    Column("research_snapshot_id", UUID(as_uuid=True), nullable=False),
    Column("research_snapshot_digest", String(71), nullable=False),
    Column("creative_decision_id", UUID(as_uuid=True), nullable=False),
    Column("context_digest", String(71), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("strategy", String(3000), nullable=False),
    Column("required_assets", JSONB, nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("estimated_max_video_cost", Numeric(20, 6), nullable=False),
    Column("estimated_max_image_cost", Numeric(20, 6), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("video_pricing_version", String(128), nullable=False),
    Column("image_pricing_version", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
production_scenes = _table(
    "production_scenes",
    Column("production_plan_id", UUID(as_uuid=True), nullable=False),
    Column("scene_key", String(64), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("duration_seconds", Integer, nullable=False),
    Column("content", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
production_shots = _table(
    "production_shots",
    Column("production_plan_id", UUID(as_uuid=True), nullable=False),
    Column("production_scene_id", UUID(as_uuid=True), nullable=False),
    Column("shot_key", String(64), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("source_strategy", String(32), nullable=False),
    Column("specification", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
generation_segments = _table(
    "generation_segments",
    Column("production_plan_id", UUID(as_uuid=True), nullable=False),
    Column("segment_key", String(64), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("media_kind", String(8), nullable=False),
    Column("duration_seconds", Integer),
    Column("shot_keys", JSONB, nullable=False),
    Column("continuity", JSONB, nullable=False),
    Column("reference_assets", JSONB, nullable=False),
    Column("generation_spec", JSONB, nullable=False),
    Column("generation_spec_digest", String(71), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
plan_decisions = _table(
    "plan_decisions",
    Column("production_plan_id", UUID(as_uuid=True), nullable=False),
    Column("plan_digest", String(71), nullable=False),
    Column("state", String(32), nullable=False),
    Column("decided_by", UUID(as_uuid=True), nullable=False),
    Column("video_route_version", String(128), nullable=False),
    Column("image_route_version", String(128), nullable=False),
    Column("video_pricing_version", String(128), nullable=False),
    Column("image_pricing_version", String(128), nullable=False),
    Column("estimated_max_cost", Numeric(20, 6), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
generation_jobs = _table(
    "generation_jobs",
    Column("production_plan_id", UUID(as_uuid=True), nullable=False),
    Column("generation_segment_id", UUID(as_uuid=True), nullable=False),
    Column("kind", String(8), nullable=False),
    Column("status", String(32), nullable=False),
    Column("media_profile", String(64), nullable=False),
    Column("route_version", String(128), nullable=False),
    Column("provider", String(64), nullable=False),
    Column("model", String(128), nullable=False),
    Column("pricing_version", String(128), nullable=False),
    Column("generation_spec_digest", String(71), nullable=False),
    Column("input_assets", JSONB, nullable=False),
    Column("provider_operation_ref", String(256)),
    Column("reserved_cost", Numeric(20, 6), nullable=False),
    Column("actual_cost", Numeric(20, 6), nullable=False),
    Column("unknown_cost", Numeric(20, 6), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("output_asset_id", UUID(as_uuid=True)),
    Column("failure_code", String(64)),
    Column("initiated_by_user_id", UUID(as_uuid=True)),
    Column("executed_by_workload_id", String(128)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
media_budget_usage = _table(
    "media_budget_usage",
    Column("generation_job_id", UUID(as_uuid=True), nullable=False),
    Column("entry_kind", String(16), nullable=False),
    Column("amount", Numeric(20, 6), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
asset_lineage = Table(
    "asset_lineage",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("parent_asset_id", UUID(as_uuid=True), nullable=False),
    Column("child_asset_id", UUID(as_uuid=True), nullable=False),
    Column("relationship_type", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    schema="catalog",
)
