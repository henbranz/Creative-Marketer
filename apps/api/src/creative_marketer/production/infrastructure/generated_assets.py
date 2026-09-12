from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from creative_marketer.catalog.asset_application import AssetService
from creative_marketer.catalog.asset_domain import (
    AllowedUse,
    AssetKind,
    AssetRole,
    RightsStatus,
)
from creative_marketer.production.domain import MediaKind
from creative_marketer.production.execution import ExecutableGeneration

from ...infrastructure.database.production_schema import asset_lineage


@dataclass(slots=True)
class ApplicationGeneratedAssetImporter:
    """Import validated provider bytes through Catalog, then append neutral lineage."""

    assets: AssetService
    session_factory: async_sessionmaker[AsyncSession]
    local_demo: bool = False

    async def import_result(
        self, execution: ExecutableGeneration, content: bytes, media_type: str
    ) -> UUID:
        if (
            execution.initiating_context is None
            or execution.brand_id is None
            or execution.product_id is None
        ):
            raise ValueError("generated Asset import lacks authoritative Product context")
        kind = AssetKind.IMAGE if execution.job.kind is MediaKind.IMAGE else AssetKind.VIDEO
        asset = await self.assets.ingest_generated(
            execution.initiating_context,
            brand_id=execution.brand_id,
            product_id=execution.product_id,
            kind=kind,
            role=AssetRole.GENERATED_FRAME if kind is AssetKind.IMAGE else AssetRole.GENERATED_SHOT,
            content=content,
            media_type=media_type,
            rights_status=RightsStatus.CONFIRMED,
            allowed_uses=(AllowedUse.INTERNAL_ANALYSIS, AllowedUse.GENERATION_INPUT),
            original_filename=(
                f"LOCAL-DEMO-generated-{kind.value}.{media_type.rsplit('/', 1)[-1]}"
                if self.local_demo
                else f"generated-{execution.job.id}.{media_type.rsplit('/', 1)[-1]}"
            ),
        )
        if execution.job.input_assets:
            async with self.session_factory() as session, session.begin():
                await session.execute(
                    text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(execution.job.tenant_id)},
                )
                for reference in execution.job.input_assets:
                    await session.execute(
                        insert(asset_lineage).values(
                            id=uuid4(),
                            tenant_id=execution.job.tenant_id,
                            parent_asset_id=reference.asset_id,
                            child_asset_id=asset.id,
                            relationship_type="DERIVED_FROM",
                            created_at=datetime.now(UTC),
                        )
                    )
        return asset.id
