from types import TracebackType
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from creative_marketer.audit.application import AuditWriter
from creative_marketer.events.application import OutboxWriter
from creative_marketer.infrastructure.database.audit import PostgresAuditWriter
from creative_marketer.infrastructure.database.event_delivery import PostgresOutboxWriter
from creative_marketer.orchestration.application import (
    OrchestrationRepository,
    OrchestrationUnitOfWork,
)

from .orchestration_repositories import SqlAlchemyOrchestrationRepository


class SqlAlchemyOrchestrationUnitOfWork:
    cycles: OrchestrationRepository
    audit: AuditWriter
    outbox: OutboxWriter

    def __init__(self, factory: async_sessionmaker[AsyncSession], tenant_id: UUID) -> None:
        self.factory, self.tenant_id = factory, tenant_id
        self.session: AsyncSession | None = None
        self.transaction: AsyncSessionTransaction | None = None

    async def __aenter__(self) -> "SqlAlchemyOrchestrationUnitOfWork":
        self.session = self.factory()
        self.transaction = await self.session.begin()
        await self.session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant, true)"),
            {"tenant": str(self.tenant_id)},
        )
        self.cycles = SqlAlchemyOrchestrationRepository(self.session)
        self.audit = PostgresAuditWriter(self.session)
        self.outbox = PostgresOutboxWriter(self.session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self.transaction is not None and self.transaction.is_active:
            await self.transaction.rollback()
        if self.session is not None:
            await self.session.close()

    async def commit(self) -> None:
        if self.transaction is None:
            raise RuntimeError("unit of work has not been entered")
        await self.transaction.commit()


class SqlAlchemyOrchestrationUnitOfWorkFactory:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory

    def __call__(self, tenant_id: UUID) -> OrchestrationUnitOfWork:
        return SqlAlchemyOrchestrationUnitOfWork(self.factory, tenant_id)
