from sqlalchemy import BigInteger, Column, DateTime, MetaData, String, Table
from sqlalchemy.dialects.postgresql import JSONB, UUID

from creative_marketer.infrastructure.database.schema import NAMING_CONVENTION

metadata = MetaData(naming_convention=NAMING_CONVENTION)

projection_nodes = Table(
    "projection_nodes",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("node_key", String(340), primary_key=True),
    Column("node_type", String(64), nullable=False),
    Column("canonical_id", String(256), nullable=False),
    Column("projection_digest", String(71), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    schema="knowledge_projection",
)

projection_changes = Table(
    "projection_changes",
    metadata,
    Column("revision", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("node_key", String(340), nullable=False),
    Column("node_type", String(64), nullable=False),
    Column("canonical_id", String(256), nullable=False),
    Column("operation", String(16), nullable=False),
    Column("payload", JSONB),
    Column("created_at", DateTime(timezone=True), nullable=False),
    schema="knowledge_projection",
)
