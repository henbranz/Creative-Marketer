from typing import Any

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, MetaData, String, Table
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
        schema="assembly",
    )


assembly_plans = _table(
    "assembly_plans",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("production_plan_id", UUID(as_uuid=True), nullable=False),
    Column("production_plan_digest", String(71), nullable=False),
    Column("creative_concept_id", UUID(as_uuid=True), nullable=False),
    Column("creative_concept_digest", String(71), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("render_profile_key", String(64), nullable=False),
    Column("render_profile_version", Integer, nullable=False),
    Column("timeline_duration_ms", BigInteger, nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("created_by_user_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
assembly_items = _table(
    "assembly_items",
    Column("assembly_plan_id", UUID(as_uuid=True), nullable=False),
    Column("item_key", String(64), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("source_kind", String(32), nullable=False),
    Column("source_asset_id", UUID(as_uuid=True), nullable=False),
    Column("source_asset_digest", String(71), nullable=False),
    Column("source_media_kind", String(16), nullable=False),
    Column("production_shot_ids", JSONB, nullable=False),
    Column("production_shot_keys", JSONB, nullable=False),
    Column("generation_segment_id", UUID(as_uuid=True)),
    Column("timeline_start_ms", BigInteger, nullable=False),
    Column("timeline_duration_ms", BigInteger, nullable=False),
    Column("source_in_ms", BigInteger, nullable=False),
    Column("source_out_ms", BigInteger),
    Column("fit_mode", String(32), nullable=False),
    Column("audio_behavior", String(16), nullable=False),
    Column("transition_in", String(16), nullable=False),
    Column("transition_out", String(16), nullable=False),
    Column("transition_duration_ms", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
caption_cues = _table(
    "caption_cues",
    Column("assembly_plan_id", UUID(as_uuid=True), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("caption_kind", String(32), nullable=False),
    Column("text", String(500), nullable=False),
    Column("start_ms", BigInteger, nullable=False),
    Column("end_ms", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
overlay_instructions = _table(
    "overlay_instructions",
    Column("assembly_plan_id", UUID(as_uuid=True), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("text", String(500), nullable=False),
    Column("start_ms", BigInteger, nullable=False),
    Column("end_ms", BigInteger, nullable=False),
    Column("position", String(32), nullable=False),
    Column("style_token", String(32), nullable=False),
    Column("source_provenance", String(256), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
manual_source_bindings = _table(
    "manual_source_bindings",
    Column("production_shot_id", UUID(as_uuid=True), nullable=False),
    Column("asset_id", UUID(as_uuid=True), nullable=False),
    Column("bound_by_user_id", UUID(as_uuid=True), nullable=False),
    Column("bound_at", DateTime(timezone=True), nullable=False),
)
assembly_jobs = _table(
    "assembly_jobs",
    Column("assembly_plan_id", UUID(as_uuid=True), nullable=False),
    Column("idempotency_key", String(256), nullable=False),
    Column("status", String(16), nullable=False),
    Column("failure_code", String(64)),
    Column("output_asset_id", UUID(as_uuid=True)),
    Column("final_creative_id", UUID(as_uuid=True)),
    Column("renderer", String(32)),
    Column("renderer_version", String(128)),
    Column("created_by_user_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
final_creatives = _table(
    "final_creatives",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("assembly_plan_id", UUID(as_uuid=True), nullable=False),
    Column("assembly_plan_digest", String(71), nullable=False),
    Column("production_plan_id", UUID(as_uuid=True), nullable=False),
    Column("production_plan_digest", String(71), nullable=False),
    Column("creative_concept_id", UUID(as_uuid=True), nullable=False),
    Column("creative_concept_digest", String(71), nullable=False),
    Column("output_asset_id", UUID(as_uuid=True), nullable=False),
    Column("output_asset_digest", String(71), nullable=False),
    Column("render_profile_key", String(64), nullable=False),
    Column("render_profile_version", Integer, nullable=False),
    Column("renderer", String(32), nullable=False),
    Column("renderer_version", String(128), nullable=False),
    Column("duration_ms", BigInteger, nullable=False),
    Column("width", Integer, nullable=False),
    Column("height", Integer, nullable=False),
    Column("fps", Integer, nullable=False),
    Column("has_audio", Boolean, nullable=False),
    Column("source_count", Integer, nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
final_creative_decisions = _table(
    "final_creative_decisions",
    Column("final_creative_id", UUID(as_uuid=True), nullable=False),
    Column("final_creative_digest", String(71), nullable=False),
    Column("output_asset_id", UUID(as_uuid=True), nullable=False),
    Column("output_asset_digest", String(71), nullable=False),
    Column("state", String(32), nullable=False),
    Column("decided_by_user_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
