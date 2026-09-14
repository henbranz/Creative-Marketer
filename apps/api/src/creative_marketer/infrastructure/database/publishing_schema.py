from typing import Any

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table
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
        schema="publishing",
    )


social_accounts = _table(
    "social_accounts",
    Column("platform", String(32), nullable=False),
    Column("display_name", String(200), nullable=False),
    Column("external_account_id", String(256), nullable=False),
    Column("username", String(200)),
    Column("account_type", String(64)),
    Column("status", String(32), nullable=False),
    Column("capabilities", JSONB, nullable=False),
    Column("provider", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
publication_drafts = _table(
    "publication_drafts",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("final_creative_id", UUID(as_uuid=True), nullable=False),
    Column("final_creative_digest", String(71), nullable=False),
    Column("output_asset_id", UUID(as_uuid=True), nullable=False),
    Column("output_asset_digest", String(71), nullable=False),
    Column("platform", String(32), nullable=False),
    Column("social_account_id", UUID(as_uuid=True), nullable=False),
    Column("external_destination_id", String(256), nullable=False),
    Column("caption", String(5000), nullable=False),
    Column("title", String(500)),
    Column("hashtags", JSONB, nullable=False),
    Column("destination_url", String(2000)),
    Column("mode", String(16), nullable=False),
    Column("scheduled_at", DateTime(timezone=True)),
    Column("platform_settings", JSONB, nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("semantic_digest", String(71), nullable=False),
    Column("created_by_user_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
publication_decisions = _table(
    "publication_decisions",
    Column("publication_draft_id", UUID(as_uuid=True), nullable=False),
    Column("publication_draft_digest", String(71), nullable=False),
    Column("final_creative_digest", String(71), nullable=False),
    Column("output_asset_digest", String(71), nullable=False),
    Column("platform", String(32), nullable=False),
    Column("social_account_id", UUID(as_uuid=True), nullable=False),
    Column("external_destination_id", String(256), nullable=False),
    Column("caption_digest", String(71), nullable=False),
    Column("platform_settings_digest", String(71), nullable=False),
    Column("scheduled_at", DateTime(timezone=True)),
    Column("action_digest", String(71), nullable=False),
    Column("state", String(16), nullable=False),
    Column("decided_by_user_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
publication_jobs = _table(
    "publication_jobs",
    Column("publication_draft_id", UUID(as_uuid=True), nullable=False),
    Column("operation_id", String(35), nullable=False),
    Column("status", String(32), nullable=False),
    Column("failure_code", String(64)),
    Column("external_operation_id", String(256)),
    Column("created_by_user_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
publications = _table(
    "publications",
    Column("product_id", UUID(as_uuid=True), nullable=False),
    Column("publication_draft_id", UUID(as_uuid=True), nullable=False),
    Column("final_creative_id", UUID(as_uuid=True), nullable=False),
    Column("output_asset_id", UUID(as_uuid=True), nullable=False),
    Column("platform", String(32), nullable=False),
    Column("social_account_id", UUID(as_uuid=True), nullable=False),
    Column("external_post_id", String(256), nullable=False),
    Column("external_operation_id", String(256)),
    Column("canonical_permalink", String(2000)),
    Column("provider", String(64), nullable=False),
    Column("connector_version", String(128), nullable=False),
    Column("request_digest", String(71), nullable=False),
    Column("response_metadata_digest", String(71)),
    Column("status", String(32), nullable=False),
    Column("submitted_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True)),
    Column("semantic_digest", String(71), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
