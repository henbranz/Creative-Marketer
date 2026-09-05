from types import TracebackType

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from creative_marketer.audit.application import AuditWriter
from creative_marketer.catalog.application import (
    BrandProfileRepository,
    BrandRepository,
    CatalogUnitOfWork,
    ProductBriefRepository,
    ProductProfileRepository,
    ProductRepository,
    SnapshotRepository,
)
from creative_marketer.events.application import OutboxWriter
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.infrastructure.database.audit import PostgresAuditWriter
from creative_marketer.infrastructure.database.catalog_repositories import (
    SqlAlchemyBrandProfileRepository,
    SqlAlchemyBrandRepository,
    SqlAlchemyProductBriefRepository,
    SqlAlchemyProductProfileRepository,
    SqlAlchemyProductRepository,
    SqlAlchemySnapshotRepository,
)
from creative_marketer.infrastructure.database.event_delivery import PostgresOutboxWriter


class SqlAlchemyCatalogUnitOfWork:
    brands: BrandRepository
    brand_profiles: BrandProfileRepository
    products: ProductRepository
    product_profiles: ProductProfileRepository
    product_briefs: ProductBriefRepository
    snapshots: SnapshotRepository
    audit: AuditWriter
    outbox: OutboxWriter

    def __init__(
        self, factory: async_sessionmaker[AsyncSession], context: ExecutionContext
    ) -> None:
        self._factory, self._context = factory, context
        self._session: AsyncSession | None = None
        self._transaction: AsyncSessionTransaction | None = None

    async def __aenter__(self) -> "SqlAlchemyCatalogUnitOfWork":
        self._session = self._factory()
        self._transaction = await self._session.begin()
        await self._session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(self._context.tenant_id)},
        )
        self.brands = SqlAlchemyBrandRepository(self._session)
        self.brand_profiles = SqlAlchemyBrandProfileRepository(self._session)
        self.products = SqlAlchemyProductRepository(self._session)
        self.product_profiles = SqlAlchemyProductProfileRepository(self._session)
        self.product_briefs = SqlAlchemyProductBriefRepository(self._session)
        self.snapshots = SqlAlchemySnapshotRepository(self._session)
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
            raise RuntimeError("catalog unit of work has not been entered")
        await self._transaction.commit()


class SqlAlchemyCatalogUnitOfWorkFactory:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    def __call__(self, context: ExecutionContext) -> CatalogUnitOfWork:
        return SqlAlchemyCatalogUnitOfWork(self._factory, context)
