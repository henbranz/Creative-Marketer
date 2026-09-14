"""Idempotently add zero-network fake social destinations to the local demo tenant."""

import asyncio
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.publishing_schema import social_accounts
from creative_marketer_api.config import Settings

TENANT_ID = uuid5(NAMESPACE_URL, "creative-marketer:local-demo:tenant")


async def run() -> None:
    settings = Settings()
    if settings.app_env not in {"development", "test"}:
        raise SystemExit("Social demo bootstrap is forbidden outside development/test")
    sessions = create_session_factory(str(settings.database_url))
    now = datetime.now(UTC)
    created = 0
    async with sessions() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(TENANT_ID)},
        )
        for platform in ("instagram", "facebook", "tiktok"):
            account_id = uuid5(NAMESPACE_URL, f"creative-marketer:local-demo:social:{platform}")
            result = await session.execute(
                insert(social_accounts)
                .values(
                    id=account_id,
                    tenant_id=TENANT_ID,
                    platform=platform,
                    display_name=f"Fake {platform.title()}",
                    external_account_id=f"fake-demo-{platform}",
                    username="@fake-demo",
                    status="ACTIVE",
                    capabilities={"media_kinds": ["video"], "supports_schedule": True},
                    provider="fake",
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=["tenant_id", "platform", "external_account_id"]
                )
                .returning(social_accounts.c.id)
            )
            if result.scalar_one_or_none() is not None:
                created += 1
        total = len((await session.execute(select(social_accounts.c.id))).all())
    print(f"Fake social destinations ready: created={created}, total={total}")


if __name__ == "__main__":
    asyncio.run(run())
