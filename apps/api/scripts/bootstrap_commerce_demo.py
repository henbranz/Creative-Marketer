"""Create an isolated Fake Store fixture; never maps an existing Product."""

import asyncio
import os
from datetime import UTC, datetime
from uuid import UUID

from creative_marketer.commerce.application import CommerceService
from creative_marketer.commerce.domain import SyncType
from creative_marketer.commerce.provider import FakeCommerceProvider
from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.infrastructure.database.commerce_uow import (
    SqlAlchemyCommerceUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer_api.config import Settings


async def run() -> None:
    settings = Settings()
    if settings.app_env not in {"development", "test"}:
        raise SystemExit("Commerce demo bootstrap is forbidden outside development/test")
    # The demo command composes the exact Agent declarations and tenant permissions after
    # the platform Tool contracts have been bootstrapped.
    from scripts.bootstrap_commerce_agent import run as bootstrap_commerce_agent

    await bootstrap_commerce_agent()
    try:
        tenant_id, user_id = (
            UUID(os.environ["BOOTSTRAP_TENANT_ID"]),
            UUID(os.environ["BOOTSTRAP_USER_ID"]),
        )
    except (KeyError, ValueError) as error:
        raise SystemExit("BOOTSTRAP_TENANT_ID and BOOTSTRAP_USER_ID must be UUIDs") from error
    ctx = ExecutionContext(
        tenant_id=tenant_id,
        actor=Actor(ActorKind.USER, user_id),
        user_id=user_id,
        membership_role=MembershipRole.OWNER,
        membership_status=MembershipStatus.ACTIVE,
        environment=settings.app_env,
        authentication=AuthenticationAssurance(
            datetime.now(UTC), "commerce-demo-bootstrap", "explicit"
        ),
    )
    provider = FakeCommerceProvider()
    factory = SqlAlchemyCommerceUnitOfWorkFactory(
        create_session_factory(str(settings.database_url))
    )
    service = CommerceService(factory, provider)
    existing = await service.list_connections(ctx)
    connection = next(
        (item for item in existing if item.external_store_id == f"fake-store-{tenant_id}"), None
    )
    if connection is None:
        connection = await service.create_fake_connection(ctx)
    provider.seed_store(connection.external_store_id)
    for kind in (SyncType.CATALOG, SyncType.INVENTORY, SyncType.ORDERS):
        cursor = None
        while True:
            result = await service.sync(ctx, connection.id, kind, cursor=cursor)
            cursor = result.next_cursor
            if cursor is None:
                break
    print(f"Fake Store ready: {connection.id}")
    print("No Creative Marketer Product was mapped; mapping requires explicit user confirmation.")


if __name__ == "__main__":
    asyncio.run(run())
