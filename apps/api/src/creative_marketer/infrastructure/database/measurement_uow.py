from types import TracebackType
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from creative_marketer.audit.application import AuditWriter
from creative_marketer.events.application import OutboxWriter
from creative_marketer.infrastructure.database.audit import PostgresAuditWriter
from creative_marketer.measurement.application import MeasurementRepository, MeasurementUnitOfWork

from .event_delivery import PostgresOutboxWriter
from .measurement_repositories import SqlAlchemyMeasurementRepository


class SqlAlchemyMeasurementUnitOfWork:
    measurement: MeasurementRepository
    audit: AuditWriter
    outbox: OutboxWriter

    def __init__(self, factory: async_sessionmaker[AsyncSession], tenant_id: UUID) -> None:
        self.factory = factory
        self.tenant_id = tenant_id
        self.session: AsyncSession | None = None
        self.transaction: AsyncSessionTransaction | None = None

    async def __aenter__(self) -> "SqlAlchemyMeasurementUnitOfWork":
        self.session = self.factory()
        self.transaction = await self.session.begin()
        await self.session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(self.tenant_id)},
        )
        self.measurement = SqlAlchemyMeasurementRepository(self.session)
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


class SqlAlchemyMeasurementUnitOfWorkFactory:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory

    def __call__(self, tenant_id: UUID) -> MeasurementUnitOfWork:
        return SqlAlchemyMeasurementUnitOfWork(self.factory, tenant_id)
