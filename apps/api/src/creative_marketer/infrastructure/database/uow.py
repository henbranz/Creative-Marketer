from types import TracebackType

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from creative_marketer.identity.application.context import TenantContext
from creative_marketer.identity.application.ports import (
    MembershipRepository,
    TenantRepository,
    UnitOfWork,
    UserRepository,
)
from creative_marketer.infrastructure.database.repositories import (
    SqlAlchemyMembershipRepository,
    SqlAlchemyTenantRepository,
    SqlAlchemyUserRepository,
)


class SqlAlchemyUnitOfWork:
    """One session and transaction per application use case."""

    tenants: TenantRepository
    users: UserRepository
    memberships: MembershipRepository

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        context: TenantContext | None,
    ) -> None:
        self._session_factory = session_factory
        self._context = context
        self._session: AsyncSession | None = None
        self._transaction: AsyncSessionTransaction | None = None

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        self._session = self._session_factory()
        self._transaction = await self._session.begin()
        if self._context is not None:
            await self._session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(self._context.tenant_id)},
            )
        self.tenants = SqlAlchemyTenantRepository(self._session)
        self.users = SqlAlchemyUserRepository(self._session)
        self.memberships = SqlAlchemyMembershipRepository(self._session, self._context)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._transaction is not None and self._transaction.is_active:
            await self._transaction.rollback()
        if self._session is not None:
            await self._session.close()

    async def commit(self) -> None:
        if self._transaction is None:
            raise RuntimeError("unit of work has not been entered")
        await self._transaction.commit()


class SqlAlchemyUnitOfWorkFactory:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def __call__(self, context: TenantContext | None = None) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(self._session_factory, context)
