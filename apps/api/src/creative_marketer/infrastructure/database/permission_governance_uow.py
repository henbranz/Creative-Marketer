from types import TracebackType

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from creative_marketer.audit.application import AuditWriter
from creative_marketer.identity.application.context import TenantContext
from creative_marketer.infrastructure.database.audit import PostgresAuditWriter
from creative_marketer.infrastructure.database.permission_governance_repositories import (
    SqlAlchemyPermissionActivationRepository,
    SqlAlchemyPermissionRepository,
    SqlAlchemyPermissionVersionRepository,
)
from creative_marketer.permission_governance.application import (
    PermissionActivationRepository,
    PermissionRepository,
    PermissionUnitOfWork,
    PermissionVersionRepository,
)


class SqlAlchemyPermissionUnitOfWork:
    permissions: PermissionRepository
    versions: PermissionVersionRepository
    activations: PermissionActivationRepository
    audit: AuditWriter

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], context: TenantContext
    ) -> None:
        self._factory, self._context = session_factory, context
        self._session: AsyncSession | None = None
        self._transaction: AsyncSessionTransaction | None = None

    async def __aenter__(self) -> "SqlAlchemyPermissionUnitOfWork":
        self._session = self._factory()
        self._transaction = await self._session.begin()
        await self._session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(self._context.tenant_id)},
        )
        self.permissions = SqlAlchemyPermissionRepository(self._session)
        self.versions = SqlAlchemyPermissionVersionRepository(self._session)
        self.activations = SqlAlchemyPermissionActivationRepository(self._session)
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
            raise RuntimeError("permission unit of work has not been entered")
        await self._transaction.commit()


class SqlAlchemyPermissionUnitOfWorkFactory:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    def __call__(self, context: TenantContext) -> PermissionUnitOfWork:
        return SqlAlchemyPermissionUnitOfWork(self._factory, context)
