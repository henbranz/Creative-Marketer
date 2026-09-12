from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from creative_marketer.agent_runtime.domain import ModelImageInputRef
from creative_marketer.catalog.asset_application import ObjectStore
from creative_marketer.infrastructure.database.catalog_schema import assets


class DatabaseObjectStoreImageMaterializer:
    """Revalidates tenant, lifecycle, rights, and digest before reading private bytes."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        object_store: ObjectStore,
        *,
        maximum_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        self._session_factory = session_factory
        self._object_store = object_store
        self._maximum_bytes = maximum_bytes

    async def materialize(self, reference: ModelImageInputRef) -> tuple[bytes, str]:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(reference.tenant_id)},
            )
            row = (
                await session.execute(select(assets).where(assets.c.id == reference.asset_id))
            ).first()
            if row is None:
                raise ValueError("authorized model image Asset is unavailable")
            value = row._mapping
            allowed = {str(item).casefold() for item in value["allowed_uses"]}
            if (
                value["status"].casefold() != "ready"
                or value["kind"].casefold() != "image"
                or value["rights_status"].casefold() != "confirmed"
                or "generation_input" not in allowed
                or value["digest"] != reference.digest
                or value["object_key"] is None
            ):
                raise ValueError("model image Asset failed current rights or integrity policy")
            mime_type = str(value["detected_mime_type"])
            key = str(value["object_key"])
        body = bytearray()
        async for chunk in self._object_store.stream(key=key):
            body.extend(chunk)
            if len(body) > self._maximum_bytes:
                raise ValueError("model image Asset exceeds the materialization bound")
        return bytes(body), mime_type
