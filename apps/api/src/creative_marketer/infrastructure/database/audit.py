from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from creative_marketer.audit.domain import AuditRecord, AuditScopeKind
from creative_marketer.audit.safety import persisted_metadata
from creative_marketer.infrastructure.database.schema import audit_records


class PostgresAuditWriter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, record: AuditRecord) -> None:
        await self._session.execute(
            insert(audit_records).values(
                id=record.id,
                scope_kind=record.scope_kind.value,
                tenant_id=record.tenant_id,
                actor_kind=record.actor_kind.value,
                actor_id=record.actor_id,
                action=record.action,
                resource_type=record.resource_type,
                resource_id=record.resource_id,
                outcome=record.outcome.value,
                reason_code=record.reason_code,
                correlation_id=record.correlation_id,
                causation_id=record.causation_id,
                occurred_at=record.occurred_at,
                environment=record.environment,
                policy_version=record.policy_version,
                tool_name=record.tool_name,
                tool_version=record.tool_version,
                tool_definition_id=record.tool_definition_id,
                tool_version_id=record.tool_version_id,
                agent_definition_id=record.agent_definition_id,
                agent_version_id=record.agent_version_id,
                agent_run_id=record.agent_run_id,
                permission_id=record.permission_id,
                permission_version_id=record.permission_version_id,
                approval_request_id=record.approval_request_id,
                idempotency_record_id=record.idempotency_record_id,
                attempt_id=record.attempt_id,
                before_digest=record.before_digest,
                after_digest=record.after_digest,
                safe_metadata=persisted_metadata(record.safe_metadata),
                audit_schema_version=record.audit_schema_version,
            )
        )


class PostgresStandaloneAuditWriter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def append(self, record: AuditRecord) -> None:
        if record.scope_kind is not AuditScopeKind.PLATFORM:
            raise ValueError(
                "standalone audit accepts platform records only; "
                "tenant audit requires a trusted context-bound transaction"
            )
        async with self._session_factory.begin() as session:
            await PostgresAuditWriter(session).append(record)
