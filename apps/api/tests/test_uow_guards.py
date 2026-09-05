import pytest

from creative_marketer.infrastructure.database.uow import SqlAlchemyUnitOfWork


class NeverUsedSessionFactory:
    def __call__(self) -> None:
        raise AssertionError("not used")


@pytest.mark.asyncio
async def test_commit_requires_entered_unit_of_work() -> None:
    uow = SqlAlchemyUnitOfWork(NeverUsedSessionFactory(), None)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="not been entered"):
        await uow.commit()
