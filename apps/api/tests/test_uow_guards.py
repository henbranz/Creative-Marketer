from uuid import uuid4

import pytest

from creative_marketer.identity.application.context import TenantContext
from creative_marketer.infrastructure.database.agent_governance_uow import (
    SqlAlchemyAgentRegistryUnitOfWork,
)
from creative_marketer.infrastructure.database.uow import SqlAlchemyUnitOfWork


class NeverUsedSessionFactory:
    def __call__(self) -> None:
        raise AssertionError("not used")


@pytest.mark.asyncio
async def test_commit_requires_entered_unit_of_work() -> None:
    uow = SqlAlchemyUnitOfWork(NeverUsedSessionFactory(), None)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="not been entered"):
        await uow.commit()
    registry_uow = SqlAlchemyAgentRegistryUnitOfWork(
        NeverUsedSessionFactory(),  # type: ignore[arg-type]
        TenantContext(uuid4()),
    )
    with pytest.raises(RuntimeError, match="not been entered"):
        await registry_uow.commit()
