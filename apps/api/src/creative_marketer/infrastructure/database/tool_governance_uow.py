from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from creative_marketer.audit.application import AuditWriter
from creative_marketer.infrastructure.database.audit import PostgresAuditWriter
from creative_marketer.infrastructure.database.tool_governance_repositories import (
    SqlAlchemyToolActivationRepository,
    SqlAlchemyToolDefinitionRepository,
    SqlAlchemyToolVersionRepository,
)
from creative_marketer.tool_governance.application import (
    ToolActivationRepository,
    ToolDefinitionRepository,
    ToolRegistryUnitOfWork,
    ToolVersionRepository,
)


class SqlAlchemyToolRegistryUnitOfWork:
    definitions: ToolDefinitionRepository
    versions: ToolVersionRepository
    activations: ToolActivationRepository
    audit: AuditWriter

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._transaction: AsyncSessionTransaction | None = None

    async def __aenter__(self) -> "SqlAlchemyToolRegistryUnitOfWork":
        self._session = self._session_factory()
        self._transaction = await self._session.begin()
        self.definitions = SqlAlchemyToolDefinitionRepository(self._session)
        self.versions = SqlAlchemyToolVersionRepository(self._session)
        self.activations = SqlAlchemyToolActivationRepository(self._session)
        self.audit = PostgresAuditWriter(self._session)
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
            raise RuntimeError("tool registry unit of work has not been entered")
        await self._transaction.commit()


class SqlAlchemyToolRegistryUnitOfWorkFactory:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def __call__(self) -> ToolRegistryUnitOfWork:
        return SqlAlchemyToolRegistryUnitOfWork(self._session_factory)
