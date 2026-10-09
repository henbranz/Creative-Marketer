from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table
from sqlalchemy.dialects.postgresql import JSONB, UUID

from creative_marketer.infrastructure.database.schema import NAMING_CONVENTION

metadata = MetaData(naming_convention=NAMING_CONVENTION)

concept_sets = Table(
    "concept_sets",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("agent_run_id", UUID(as_uuid=True), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("product_snapshot_id", UUID(as_uuid=True), nullable=False),
    Column("product_snapshot_digest", String(71), nullable=False),
    Column("research_snapshot_id", UUID(as_uuid=True), nullable=False),
    Column("research_snapshot_digest", String(71), nullable=False),
    Column("input_context_digest", String(71), nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("experiment_proposal_id", UUID(as_uuid=True)),
    Column("experiment_proposal_digest", String(71)),
    schema="creative",
)

concepts = Table(
    "concepts",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("concept_set_id", UUID(as_uuid=True), nullable=False),
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("concept_key", String(64), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("concept_payload", JSONB, nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    schema="creative",
)

concept_decisions = Table(
    "concept_decisions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("concept_id", UUID(as_uuid=True), nullable=False),
    Column("state", String(32), nullable=False),
    Column("decided_by", UUID(as_uuid=True), nullable=False),
    Column("reason_code", String(64)),
    Column("note", String(1000)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    schema="creative",
)

concept_revalidations = Table(
    "concept_revalidations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("concept_id", UUID(as_uuid=True), nullable=False),
    Column("concept_digest", String(71), nullable=False),
    Column("original_concept_set_id", UUID(as_uuid=True), nullable=False),
    Column("original_research_snapshot_id", UUID(as_uuid=True), nullable=False),
    Column("original_research_snapshot_digest", String(71), nullable=False),
    Column("current_research_snapshot_id", UUID(as_uuid=True), nullable=False),
    Column("current_research_snapshot_digest", String(71), nullable=False),
    Column("product_snapshot_id", UUID(as_uuid=True), nullable=False),
    Column("product_snapshot_digest", String(71), nullable=False),
    Column("original_decision_id", UUID(as_uuid=True), nullable=False),
    Column("result", String(32), nullable=False),
    Column("reason_codes", JSONB, nullable=False),
    Column("referenced_finding_assertions", JSONB, nullable=False),
    Column("created_by", UUID(as_uuid=True), nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    schema="creative",
)
