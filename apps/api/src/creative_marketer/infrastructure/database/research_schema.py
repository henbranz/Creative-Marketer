from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData(schema="research")

sources = Table(
    "sources",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("source_type", String(32), nullable=False),
    Column("canonical_url", String(2048), nullable=False),
    Column("display_name", String(200), nullable=False),
    Column("category", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_by", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tenant_id", "id"),
)

source_fetches = Table(
    "source_fetches",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("source_id", UUID(as_uuid=True), nullable=False),
    Column("requested_url", String(2048), nullable=False),
    Column("requested_by", UUID(as_uuid=True), nullable=False),
    Column("raw_object_key", String(1024), nullable=False),
    Column("status", String(32), nullable=False),
    Column("final_url", String(2048)),
    Column("http_status", Integer),
    Column("content_type", String(100)),
    Column("raw_digest", String(71)),
    Column("raw_byte_size", BigInteger),
    Column("evidence_snapshot_id", UUID(as_uuid=True)),
    Column("failure_code", String(64)),
    Column("started_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["tenant_id", "source_id"], ["research.sources.tenant_id", "research.sources.id"]
    ),
    UniqueConstraint("tenant_id", "id"),
)

evidence_snapshots = Table(
    "evidence_snapshots",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("source_id", UUID(as_uuid=True), nullable=False),
    Column("source_fetch_id", UUID(as_uuid=True), nullable=False),
    Column("final_url", String(2048), nullable=False),
    Column("title", String(500)),
    Column("content_blocks", JSONB, nullable=False),
    Column("outbound_links", JSONB, nullable=False),
    Column("structured_metadata", JSONB, nullable=False),
    Column("raw_digest", String(71), nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("extractor_version", String(64), nullable=False),
    Column("instruction_like_content", Boolean, nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["tenant_id", "source_id"], ["research.sources.tenant_id", "research.sources.id"]
    ),
    ForeignKeyConstraint(
        ["tenant_id", "source_fetch_id"],
        ["research.source_fetches.tenant_id", "research.source_fetches.id"],
    ),
    UniqueConstraint("tenant_id", "id"),
)
