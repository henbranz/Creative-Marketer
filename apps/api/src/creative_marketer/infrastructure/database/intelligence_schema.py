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
        schema="intelligence",
    )


context_manifests = _table(
    "context_manifests",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("manifest", JSONB, nullable=False),
    Column("data_trust_level", String(16), nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
creative_feature_snapshots = _table(
    "creative_feature_snapshots",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("publication_id", UUID(as_uuid=True), nullable=False),
    Column("final_creative_id", UUID(as_uuid=True), nullable=False),
    Column("extraction_version", String(64), nullable=False),
    Column("features", JSONB, nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
performance_comparisons = _table(
    "performance_comparisons",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("publication_id", UUID(as_uuid=True), nullable=False),
    Column("final_creative_id", UUID(as_uuid=True), nullable=False),
    Column("comparison_window", String(16), nullable=False),
    Column("metric_key", String(64), nullable=False),
    Column("observed_value", Numeric(30, 9), nullable=False),
    Column("baseline_value", Numeric(30, 9), nullable=False),
    Column("absolute_delta", Numeric(30, 9), nullable=False),
    Column("relative_delta", Numeric(30, 9)),
    Column("sample_size", Integer, nullable=False),
    Column("baseline_publication_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
    Column("source_snapshot_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
    Column("comparability_policy_version", String(64), nullable=False),
    Column("calculation_version", String(64), nullable=False),
    Column("data_trust_level", String(16), nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
reports = _table(
    "reports",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("agent_run_id", UUID(as_uuid=True), nullable=False),
    Column("agent_version_id", UUID(as_uuid=True), nullable=False),
    Column("context_manifest_id", UUID(as_uuid=True), nullable=False),
    Column("context_manifest_digest", String(71), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("data_trust_level", String(16), nullable=False),
    Column("summary", String(1200), nullable=False),
    Column("observations", JSONB, nullable=False),
    Column("comparative_findings", JSONB, nullable=False),
    Column("limitations", JSONB, nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
insight_candidates = _table(
    "insight_candidates",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("report_id", UUID(as_uuid=True), nullable=False),
    Column("statement", String(800), nullable=False),
    Column("evidence_refs", JSONB, nullable=False),
    Column("sample_size", Integer, nullable=False),
    Column("metric", String(64), nullable=False),
    Column("baseline", Numeric(30, 9)),
    Column("observed_delta", Numeric(30, 9)),
    Column("confidence", String(16), nullable=False),
    Column("scope", JSONB, nullable=False),
    Column("limitations", JSONB, nullable=False),
    Column("data_trust_level", String(16), nullable=False),
    Column("status", String(32), nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("valid_until", DateTime(timezone=True)),
)
insight_decisions = _table(
    "insight_decisions",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("candidate_id", UUID(as_uuid=True), nullable=False),
    Column("candidate_digest", String(71), nullable=False),
    Column("decision", String(32), nullable=False),
    Column("decided_by", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
experiment_proposals = _table(
    "experiment_proposals",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("report_id", UUID(as_uuid=True), nullable=False),
    Column("report_digest", String(71), nullable=False),
    Column("candidate_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
    Column("hypothesis", String(600), nullable=False),
    Column("primary_variable", String(200), nullable=False),
    Column("controlled_elements", JSONB, nullable=False),
    Column("target_metric", String(64), nullable=False),
    Column("platform", String(64), nullable=False),
    Column("measurement_window", String(16), nullable=False),
    Column("creative_direction", String(800), nullable=False),
    Column("rationale", String(600), nullable=False),
    Column("expected_learning", String(600), nullable=False),
    Column("data_trust_level", String(16), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
experiment_decisions = _table(
    "experiment_decisions",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("proposal_id", UUID(as_uuid=True), nullable=False),
    Column("proposal_digest", String(71), nullable=False),
    Column("decision", String(32), nullable=False),
    Column("decided_by", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
