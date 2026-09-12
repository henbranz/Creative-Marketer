from types import TracebackType
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from creative_marketer.assembly.application import AssemblyRepository, AssemblyUnitOfWork
from creative_marketer.audit.application import AuditWriter
from creative_marketer.events.application import OutboxWriter

from .assembly_repositories import SqlAlchemyAssemblyRepository
from .audit import PostgresAuditWriter
from .event_delivery import PostgresOutboxWriter


class SqlAlchemyAssemblyUnitOfWork:
    assembly: AssemblyRepository
    audit: AuditWriter
    outbox: OutboxWriter

    def __init__(self, factory: async_sessionmaker[AsyncSession], tenant_id: UUID) -> None:
        self._factory, self._tenant_id = factory, tenant_id
        self._session: AsyncSession | None = None
        self._transaction: AsyncSessionTransaction | None = None

    async def __aenter__(self) -> "SqlAlchemyAssemblyUnitOfWork":
        self._session = self._factory()
        self._transaction = await self._session.begin()
        await self._session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(self._tenant_id)},
        )
        self.assembly = SqlAlchemyAssemblyRepository(self._session)
        self.audit = PostgresAuditWriter(self._session)
        self.outbox = PostgresOutboxWriter(self._session)
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
            raise RuntimeError("Assembly unit of work has not been entered")
        await self._transaction.commit()


class SqlAlchemyAssemblyUnitOfWorkFactory:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    def __call__(self, tenant_id: UUID) -> AssemblyUnitOfWork:
        return SqlAlchemyAssemblyUnitOfWork(self._factory, tenant_id)
