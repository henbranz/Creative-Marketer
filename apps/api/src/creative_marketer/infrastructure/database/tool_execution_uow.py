from types import TracebackType

from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from creative_marketer.agent_governance.domain import AgentScopeKind
from creative_marketer.approval_governance.application import (
    ApprovalDecisionRepository,
    ApprovalRequestRepository,
    ApprovalRevocationRepository,
)
from creative_marketer.audit.application import AuditWriter
from creative_marketer.events.application import OutboxWriter
from creative_marketer.execution_control.application import IdempotencyRepository
from creative_marketer.identity.application.context import TenantContext
from creative_marketer.infrastructure.database.agent_governance_schema import (
    agent_activations,
    agent_definitions,
)
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
from creative_marketer.infrastructure.database.permission_governance_schema import (
    tool_permission_activations,
    tool_permissions,
)
from creative_marketer.infrastructure.database.tool_execution_repositories import (
    SqlAlchemyToolCallRepository,
)
from creative_marketer.infrastructure.database.tool_governance_schema import (
    tool_activations,
    tool_definitions,
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
        requested = (
            await self._session.execute(
                select(agent_definitions.c.platform_template_id)
                .where(
                    agent_definitions.c.id == binding.requested_agent_definition_id,
                    agent_definitions.c.tenant_id == binding.tenant_id,
                    agent_definitions.c.status == "active",
                )
                .with_for_update(read=True)
            )
        ).first()
        if requested is None:
            return False
        expected_resolved = binding.requested_agent_definition_id
        if requested.platform_template_id == binding.resolved_agent_definition_id:
            expected_resolved = requested.platform_template_id
            platform_active = (
                await self._session.execute(
                    select(agent_definitions.c.id)
                    .where(
                        agent_definitions.c.id == expected_resolved,
                        agent_definitions.c.scope_kind == AgentScopeKind.PLATFORM.value,
                        agent_definitions.c.status == "active",
                    )
                    .with_for_update(read=True)
                )
            ).first()
            if platform_active is None:
                return False
        elif binding.resolved_agent_definition_id != binding.requested_agent_definition_id:
            return False
        agent_active = (
            await self._session.execute(
                select(agent_activations.c.active_version_id)
                .where(agent_activations.c.definition_id == expected_resolved)
                .with_for_update(read=True)
            )
        ).scalar_one_or_none()
        tool_active = (
            await self._session.execute(
                select(tool_activations.c.active_version_id)
                .join(tool_definitions, tool_definitions.c.id == tool_activations.c.definition_id)
                .where(
                    tool_activations.c.definition_id == binding.tool_definition_id,
                    tool_definitions.c.status == "active",
                )
                .with_for_update(read=True)
            )
        ).scalar_one_or_none()
        permission_active = (
            await self._session.execute(
                select(tool_permission_activations.c.active_version_id)
                .join(
                    tool_permissions,
                    and_(
                        tool_permissions.c.id == tool_permission_activations.c.permission_id,
                        tool_permissions.c.tenant_id == tool_permission_activations.c.tenant_id,
                    ),
                )
                .where(
                    tool_permission_activations.c.tenant_id == binding.tenant_id,
                    tool_permission_activations.c.permission_id == binding.permission_id,
                    tool_permissions.c.status == "active",
                    tool_permissions.c.agent_definition_id == binding.requested_agent_definition_id,
                    tool_permissions.c.tool_definition_id == binding.tool_definition_id,
                )
                .with_for_update(read=True)
            )
        ).scalar_one_or_none()
        return (
            agent_active == binding.agent_version_id
            and tool_active == binding.tool_version_id
            and permission_active == binding.permission_version_id
        )


class SqlAlchemyGatewayUnitOfWorkFactory:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    def __call__(self, context: TenantContext) -> GatewayUnitOfWork:
        return SqlAlchemyGatewayUnitOfWork(self._factory, context)
