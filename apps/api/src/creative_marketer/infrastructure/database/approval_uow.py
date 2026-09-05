from types import TracebackType

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from creative_marketer.approval_governance.application import (
    ApprovalDecisionRepository,
    ApprovalRequestRepository,
    ApprovalRevocationRepository,
    ApprovalUnitOfWork,
)
from creative_marketer.audit.application import AuditWriter
from creative_marketer.identity.application.context import TenantContext
from creative_marketer.infrastructure.database.approval_repositories import (
    SqlAlchemyApprovalDecisionRepository,
    SqlAlchemyApprovalRequestRepository,
    SqlAlchemyApprovalRevocationRepository,
)
from creative_marketer.infrastructure.database.audit import PostgresAuditWriter


class SqlAlchemyApprovalUnitOfWork:
    requests: ApprovalRequestRepository
    decisions: ApprovalDecisionRepository
    revocations: ApprovalRevocationRepository
    audit: AuditWriter

    def __init__(self, factory: async_sessionmaker[AsyncSession], context: TenantContext) -> None:
        self._factory, self._context = factory, context
        self._session: AsyncSession | None = None
        self._transaction: AsyncSessionTransaction | None = None

    async def __aenter__(self) -> "SqlAlchemyApprovalUnitOfWork":
        self._session = self._factory()
        self._transaction = await self._session.begin()
        await self._session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(self._context.tenant_id)},
        )
        self.requests = SqlAlchemyApprovalRequestRepository(self._session)
        self.decisions = SqlAlchemyApprovalDecisionRepository(self._session)
        self.revocations = SqlAlchemyApprovalRevocationRepository(self._session)
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
            raise RuntimeError("approval unit of work has not been entered")
        await self._transaction.commit()


class SqlAlchemyApprovalUnitOfWorkFactory:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    def __call__(self, context: TenantContext) -> ApprovalUnitOfWork:
        return SqlAlchemyApprovalUnitOfWork(self._factory, context)
