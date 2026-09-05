from types import TracebackType

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from creative_marketer.approval_governance.application import (
    ApprovalDecisionRepository,
    ApprovalRequestRepository,
    ApprovalRevocationRepository,
)
from creative_marketer.audit.application import AuditWriter
from creative_marketer.events.application import OutboxWriter
from creative_marketer.execution_control.application import IdempotencyRepository
from creative_marketer.identity.application.context import TenantContext
from creative_marketer.infrastructure.database.approval_repositories import (
    SqlAlchemyApprovalDecisionRepository,
    SqlAlchemyApprovalRequestRepository,
    SqlAlchemyApprovalRevocationRepository,
)
from creative_marketer.infrastructure.database.audit import PostgresAuditWriter
from creative_marketer.infrastructure.database.event_delivery import PostgresOutboxWriter
from creative_marketer.infrastructure.database.execution_control_repositories import (
    SqlAlchemyIdempotencyRepository,
)
from creative_marketer.infrastructure.database.tool_execution_repositories import (
    SqlAlchemyToolCallRepository,
)
from creative_marketer.tool_execution.application import GatewayUnitOfWork, ToolCallRepository
from creative_marketer.tool_execution.domain import ToolCall


class SqlAlchemyGatewayUnitOfWork:
    audit: AuditWriter
    outbox: OutboxWriter
    idempotency: IdempotencyRepository
    requests: ApprovalRequestRepository
    decisions: ApprovalDecisionRepository
    revocations: ApprovalRevocationRepository
    tool_calls: ToolCallRepository

    def __init__(self, factory: async_sessionmaker[AsyncSession], context: TenantContext) -> None:
        self._factory, self._context = factory, context
        self._session: AsyncSession | None = None
        self._transaction: AsyncSessionTransaction | None = None

    async def __aenter__(self) -> "SqlAlchemyGatewayUnitOfWork":
        self._session = self._factory()
        self._transaction = await self._session.begin()
        await self._session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(self._context.tenant_id)},
        )
        self.tool_calls = SqlAlchemyToolCallRepository(self._session)
        self.idempotency = SqlAlchemyIdempotencyRepository(self._session)
        self.requests = SqlAlchemyApprovalRequestRepository(self._session)
        self.decisions = SqlAlchemyApprovalDecisionRepository(self._session)
        self.revocations = SqlAlchemyApprovalRevocationRepository(self._session)
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
            raise RuntimeError("gateway unit of work has not been entered")
        await self._transaction.commit()

    async def verify_authorization_snapshot(self, call: ToolCall) -> bool:
        if self._session is None:
            raise RuntimeError("gateway unit of work has not been entered")
        binding = call.binding
        statement = text("""
            SELECT EXISTS (
              SELECT 1 FROM agent_governance.agent_definitions requested
              WHERE requested.id = :requested_agent
                AND requested.tenant_id = :tenant AND requested.status = 'active'
                AND (
                  (requested.id = :resolved_agent AND EXISTS (
                    SELECT 1 FROM agent_governance.agent_activations aa
                    WHERE aa.definition_id = requested.id
                      AND aa.active_version_id = :agent_version
                  )) OR
                  (requested.platform_template_id = :resolved_agent AND EXISTS (
                    SELECT 1 FROM agent_governance.agent_definitions platform_agent
                    JOIN agent_governance.agent_activations aa
                      ON aa.definition_id = platform_agent.id
                    WHERE platform_agent.id = :resolved_agent
                      AND platform_agent.scope_kind = 'platform'
                      AND platform_agent.status = 'active'
                      AND aa.active_version_id = :agent_version
                  ))
                )
                AND EXISTS (
                  SELECT 1 FROM tool_governance.tool_definitions td
                  JOIN tool_governance.tool_activations ta ON ta.definition_id = td.id
                  WHERE td.id = :tool_definition AND td.status = 'active'
                    AND ta.active_version_id = :tool_version
                )
                AND EXISTS (
                  SELECT 1 FROM permission_governance.tool_permissions permission
                  JOIN permission_governance.tool_permission_activations pa
                    ON pa.permission_id = permission.id
                    AND pa.tenant_id = permission.tenant_id
                  WHERE permission.id = :permission AND permission.tenant_id = :tenant
                    AND permission.status = 'active'
                    AND permission.agent_definition_id = :requested_agent
                    AND permission.tool_definition_id = :tool_definition
                    AND pa.active_version_id = :permission_version
                )
            )
        """)
        return bool(
            await self._session.scalar(
                statement,
                {
                    "tenant": binding.tenant_id,
                    "requested_agent": binding.requested_agent_definition_id,
                    "resolved_agent": binding.resolved_agent_definition_id,
                    "agent_version": binding.agent_version_id,
                    "tool_definition": binding.tool_definition_id,
                    "tool_version": binding.tool_version_id,
                    "permission": binding.permission_id,
                    "permission_version": binding.permission_version_id,
                },
            )
        )


class SqlAlchemyGatewayUnitOfWorkFactory:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    def __call__(self, context: TenantContext) -> GatewayUnitOfWork:
        return SqlAlchemyGatewayUnitOfWork(self._factory, context)
