from types import TracebackType

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from creative_marketer.events.application import ConsumerUnitOfWork, InboxRepository
from creative_marketer.identity.application.context import TenantContext
from creative_marketer.infrastructure.database.event_delivery import PostgresInboxRepository


class SqlAlchemyConsumerUnitOfWork:
    inbox: InboxRepository

    def __init__(self, factory: async_sessionmaker[AsyncSession], context: TenantContext) -> None:
        self._factory, self._context = factory, context
        self._session: AsyncSession | None = None
        self._transaction: AsyncSessionTransaction | None = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("consumer unit of work has not been entered")
        return self._session

    async def __aenter__(self) -> "SqlAlchemyConsumerUnitOfWork":
        self._session = self._factory()
        self._transaction = await self._session.begin()
        await self._session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(self._context.tenant_id)},
        )
        self.inbox = PostgresInboxRepository(self._session)
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
            raise RuntimeError("consumer unit of work has not been entered")
        await self._transaction.commit()


class SqlAlchemyConsumerUnitOfWorkFactory:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    def __call__(self, context: TenantContext) -> ConsumerUnitOfWork:
        return SqlAlchemyConsumerUnitOfWork(self._factory, context)
