from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.publishing.application import PublishingService
from creative_marketer.publishing.domain import PublicationNotFound
from creative_marketer.publishing.execution import ExecutablePublication
from creative_marketer.workflow_orchestration.contracts import PublicationWorkflowResult

from .agent_governance_schema import agent_definitions
from .publishing_schema import publication_drafts
from .schema import memberships, tenants, users

PUBLISHING_WORKLOAD_ACTOR_ID = uuid5(
    NAMESPACE_URL, "creative-marketer:social-publishing-execution-workload"
)


class SqlAlchemyPublicationExecutionAuthority:
    """Reconstruct current publishing authority from tenant-scoped PostgreSQL facts."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        service: PublishingService,
        environment: str,
    ) -> None:
        self._sessions = sessions
        self._service = service
        self._environment = environment

    @staticmethod
    async def _tenant(session: AsyncSession, tenant_id: UUID) -> None:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )

    async def authorize_resource(self, tenant_id: UUID, draft_id: UUID) -> None:
        async with self._sessions() as session, session.begin():
            await self._tenant(session, tenant_id)
            value = await session.scalar(
                select(publication_drafts.c.id).where(
                    publication_drafts.c.tenant_id == tenant_id,
                    publication_drafts.c.id == draft_id,
                )
            )
        if value is None:
            raise PublicationNotFound("PublicationDraft not found")

    async def prepare(self, tenant_id: UUID, draft_id: UUID) -> ExecutablePublication:
        async with self._sessions() as session, session.begin():
            await self._tenant(session, tenant_id)
            row = (
                (
                    await session.execute(
                        select(
                            publication_drafts.c.created_by_user_id,
                            memberships.c.role,
                            memberships.c.status.label("membership_status"),
                            users.c.status.label("user_status"),
                            tenants.c.status.label("tenant_status"),
                        )
                        .join(
                            memberships,
                            (memberships.c.tenant_id == publication_drafts.c.tenant_id)
                            & (memberships.c.user_id == publication_drafts.c.created_by_user_id),
                        )
                        .join(users, users.c.id == memberships.c.user_id)
                        .join(tenants, tenants.c.id == memberships.c.tenant_id)
                        .where(
                            publication_drafts.c.tenant_id == tenant_id,
                            publication_drafts.c.id == draft_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            principals = (
                (
                    await session.execute(
                        select(agent_definitions.c.id).where(
                            agent_definitions.c.tenant_id == tenant_id,
                            agent_definitions.c.agent_type == "publishing_execution_workload",
                            agent_definitions.c.status == "active",
                        )
                    )
                )
                .scalars()
                .all()
            )
        if (
            row is None
            or row["membership_status"] != MembershipStatus.ACTIVE.value
            or row["user_status"] != "active"
            or row["tenant_status"] != "active"
        ):
            raise PublicationNotFound("current initiating publication authority is unavailable")
        if len(principals) != 1:
            raise PublicationNotFound("publishing execution principal is unavailable")
        user_id = cast(UUID, row["created_by_user_id"])
        context = ExecutionContext(
            tenant_id,
            Actor(ActorKind.WORKLOAD, PUBLISHING_WORKLOAD_ACTOR_ID),
            user_id,
            MembershipRole(str(row["role"])),
            MembershipStatus(str(row["membership_status"])),
            self._environment,
            AuthenticationAssurance(
                datetime.now(UTC), "durable-publication", "postgresql-authority"
            ),
        )
        record = await self._service.get_draft(context, draft_id)
        if record.job is None:
            raise PublicationNotFound("PublicationJob not found")
        return ExecutablePublication(
            context,
            cast(UUID, principals[0]),
            record.job.status,
            record.job.operation_id,
        )

    async def execute_tool(
        self, tenant_id: UUID, draft_id: UUID, operation: str
    ) -> PublicationWorkflowResult:
        execution = await self.prepare(tenant_id, draft_id)
        if operation == "submit":
            record = await self._service.execute(execution.context, draft_id)
        elif operation == "reconcile":
            record = await self._service.reconcile(execution.context, draft_id)
        elif operation == "cancel":
            record = await self._service.cancel(execution.context, draft_id)
        else:
            raise ValueError("unsupported publishing operation")
        publication_id = await self._publication_id(
            execution.context, record.draft.product_id, draft_id
        )
        return PublicationWorkflowResult(
            str(draft_id),
            record.job.status.value if record.job else "FAILED",
            str(publication_id) if publication_id else None,
            record.job.failure_code if record.job else "PUBLICATION_JOB_MISSING",
        )

    async def current(self, tenant_id: UUID, draft_id: UUID) -> PublicationWorkflowResult:
        execution = await self.prepare(tenant_id, draft_id)
        record = await self._service.get_draft(execution.context, draft_id)
        publication_id = await self._publication_id(
            execution.context, record.draft.product_id, draft_id
        )
        return PublicationWorkflowResult(
            str(draft_id),
            record.job.status.value if record.job else "FAILED",
            str(publication_id) if publication_id else None,
            record.job.failure_code if record.job else "PUBLICATION_JOB_MISSING",
        )

    async def _publication_id(
        self, context: ExecutionContext, product_id: UUID, draft_id: UUID
    ) -> UUID | None:
        values = await self._service.list_publications(context, product_id)
        value = next((item for item in values if item.publication_draft_id == draft_id), None)
        return value.id if value else None
