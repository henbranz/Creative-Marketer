from types import TracebackType

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from creative_marketer.audit.application import AuditWriter
from creative_marketer.events.application import OutboxWriter
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.infrastructure.database.audit import PostgresAuditWriter
from creative_marketer.infrastructure.database.event_delivery import PostgresOutboxWriter
from creative_marketer.infrastructure.database.research_repositories import (
    SqlAlchemyEvidenceRepository,
    SqlAlchemyFetchRepository,
    SqlAlchemyProductReferenceReader,
    SqlAlchemySourceRepository,
)
from creative_marketer.research.application import (
    EvidenceRepository,
    FetchRepository,
    ProductReferenceReader,
    ResearchUnitOfWork,
    SourceRepository,
)


class SqlAlchemyResearchUnitOfWork:
    sources: SourceRepository
    fetches: FetchRepository
    evidence: EvidenceRepository
    products: ProductReferenceReader
    audit: AuditWriter
    outbox: OutboxWriter

    def __init__(
        self, factory: async_sessionmaker[AsyncSession], context: ExecutionContext
    ) -> None:
        self._factory, self._context = factory, context
        self._session: AsyncSession | None = None
        self._transaction: AsyncSessionTransaction | None = None

    async def __aenter__(self) -> "SqlAlchemyResearchUnitOfWork":
        self._session = self._factory()
        self._transaction = await self._session.begin()
        await self._session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(self._context.tenant_id)},
        )
        self.sources = SqlAlchemySourceRepository(self._session)
        self.fetches = SqlAlchemyFetchRepository(self._session)
        self.evidence = SqlAlchemyEvidenceRepository(self._session)
        self.products = SqlAlchemyProductReferenceReader(self._session)
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
            raise RuntimeError("research unit of work has not been entered")
        await self._transaction.commit()


class SqlAlchemyResearchUnitOfWorkFactory:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    def __call__(self, context: ExecutionContext) -> ResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(self._factory, context)
