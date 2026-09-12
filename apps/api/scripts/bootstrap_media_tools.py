"""Explicit development/test bootstrap for the four governed media Tool contracts."""

import asyncio
import os
from uuid import UUID, uuid4

from creative_marketer.identity.application.authentication import ActorKind
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.tool_governance_uow import (
    SqlAlchemyToolRegistryUnitOfWorkFactory,
)
from creative_marketer.production.tool_contracts import media_tool_contracts
from creative_marketer.tool_governance.application import (
    ActivateToolVersion,
    CreateToolDefinition,
    CreateToolVersion,
    PlatformControlContext,
)
from creative_marketer_api.config import Settings


async def run() -> None:
    settings = Settings()
    if settings.app_env not in {"development", "test"}:
        raise SystemExit("Media Tool bootstrap is forbidden outside development/test")
    try:
        actor_id = UUID(os.environ["BOOTSTRAP_PLATFORM_ACTOR_ID"])
    except (KeyError, ValueError) as error:
        raise SystemExit("BOOTSTRAP_PLATFORM_ACTOR_ID must be a UUID") from error
    context = PlatformControlContext(ActorKind.WORKLOAD, actor_id, settings.app_env, uuid4())
    factory = SqlAlchemyToolRegistryUnitOfWorkFactory(
        create_session_factory(str(settings.database_url))
    )
    for contract in media_tool_contracts():
        async with factory() as uow:
            definition = await uow.definitions.get_by_key(contract.tool_key)
        if definition is None:
            definition = await CreateToolDefinition(factory)(
                context, tool_key=contract.tool_key, category="media"
            )
        version = await CreateToolVersion(factory)(context, definition.id, contract.configuration)
        await ActivateToolVersion(factory)(context, definition.id, version.id)
        print(f"Activated {contract.tool_key} version {version.version_number}")


if __name__ == "__main__":
    asyncio.run(run())
