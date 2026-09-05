from sqlalchemy import Column, DateTime, Integer, MetaData, Numeric, String, Table, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from creative_marketer.infrastructure.database.schema import NAMING_CONVENTION

metadata = MetaData(naming_convention=NAMING_CONVENTION)

brands = Table(
    "brands",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("name", String(200), nullable=False),
    Column("slug", String(100), nullable=False),
    Column("website_url", String(2048)),
    Column("status", String(32), nullable=False),
    Column("created_by", UUID(as_uuid=True), nullable=False),
    schema="catalog",
)

brand_profiles = Table(
    "brand_profiles",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("brand_id", UUID(as_uuid=True), primary_key=True),
    Column("industry", String(200), nullable=False),
    Column("description", Text(), nullable=False),
    Column("brand_positioning", Text(), nullable=False),
    Column("brand_voice", Text(), nullable=False),
    Column("tone_attributes", JSONB, nullable=False),
    Column("visual_style_keywords", JSONB, nullable=False),
    Column("target_markets", JSONB, nullable=False),
    Column("primary_language", String(16), nullable=False),
    Column("allowed_claims", JSONB, nullable=False),
    Column("prohibited_claims", JSONB, nullable=False),
    Column("competitors", JSONB, nullable=False),
    Column("provenance", String(32), nullable=False),
    schema="catalog",
)

products = Table(
    "products",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("brand_id", UUID(as_uuid=True), nullable=False),
    Column("name", String(200), nullable=False),
    Column("slug", String(100), nullable=False),
    Column("sku", String(100)),
    Column("category", String(200), nullable=False),
    Column("short_description", String(1000), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_by", UUID(as_uuid=True), nullable=False),
    schema="catalog",
)

product_profiles = Table(
    "product_profiles",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("product_id", UUID(as_uuid=True), primary_key=True),
    Column("description", Text(), nullable=False),
    Column("features", JSONB, nullable=False),
    Column("benefits", JSONB, nullable=False),
    Column("materials", JSONB, nullable=False),
    Column("variants", JSONB, nullable=False),
    Column("price", Numeric(19, 2)),
    Column("currency", String(3)),
    Column("estimated_margin", Numeric(7, 4)),
    Column("target_audiences", JSONB, nullable=False),
    Column("problems_solved", JSONB, nullable=False),
    Column("use_cases", JSONB, nullable=False),
    Column("differentiators", JSONB, nullable=False),
    Column("purchase_objections", JSONB, nullable=False),
    Column("allowed_claims", JSONB, nullable=False),
    Column("prohibited_claims", JSONB, nullable=False),
    Column("shipping_summary", Text()),
    Column("seasonality_notes", Text()),
    Column("landing_page_url", String(2048)),
    Column("competitor_product_refs", JSONB, nullable=False),
    Column("provenance", String(32), nullable=False),
    schema="catalog",
)

product_briefs = Table(
    "product_briefs",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("product_id", UUID(as_uuid=True), primary_key=True),
    Column("content", JSONB, nullable=False),
    Column("provenance", String(32), nullable=False),
    Column("revision", Integer(), nullable=False),
    schema="catalog",
)

product_knowledge_snapshots = Table(
    "product_knowledge_snapshots",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("schema_version", Integer(), nullable=False),
    Column("source_revision", Integer(), nullable=False),
    Column("content", JSONB, nullable=False),
    Column("digest", String(71), nullable=False),
    Column("created_by", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    schema="catalog",
)
