from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.execution_control.domain import (
    IdempotencyRecord,
    IdempotencyState,
    ReconciliationOutcome,
)
from creative_marketer.infrastructure.database.execution_control_schema import idempotency_records


def _record(row: object) -> IdempotencyRecord:
    data = row._mapping  # type: ignore[attr-defined]
    reconciliation = data["reconciliation_outcome"]
    return IdempotencyRecord(
        id=data["id"],
        tenant_id=data["tenant_id"],
        tool_definition_id=data["tool_definition_id"],
        tool_version_id=data["tool_version_id"],
        idempotency_key=data["idempotency_key"],
        request_digest=data["request_digest"],
        state=IdempotencyState(data["state"]),
        attempt_count=data["attempt_count"],
        current_attempt_id=data["current_attempt_id"],
        lease_expires_at=data["lease_expires_at"],
        result_ref=data["result_ref"],
        reconciliation_outcome=None
        if reconciliation is None
        else ReconciliationOutcome(reconciliation),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


class SqlAlchemyIdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve(self, candidate: IdempotencyRecord) -> tuple[IdempotencyRecord, bool]:
        statement = (
            insert(idempotency_records)
            .values(
                id=candidate.id,
                tenant_id=candidate.tenant_id,
                tool_definition_id=candidate.tool_definition_id,
                tool_version_id=candidate.tool_version_id,
                idempotency_key=candidate.idempotency_key,
                request_digest=candidate.request_digest,
                state=candidate.state.value,
                attempt_count=candidate.attempt_count,
                created_at=candidate.created_at,
                updated_at=candidate.updated_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    idempotency_records.c.tenant_id,
                    idempotency_records.c.tool_definition_id,
                    idempotency_records.c.idempotency_key,
                ]
            )
            .returning(idempotency_records)
        )
        inserted = (await self._session.execute(statement)).first()
        if inserted is not None:
            return _record(inserted), True
        existing = await self.get_by_key(candidate.tool_definition_id, candidate.idempotency_key)
        if existing is None:
            raise RuntimeError("conflicting idempotency record is not visible")
        return existing, False

    async def get(self, record_id: UUID, *, for_update: bool = False) -> IdempotencyRecord | None:
        query = select(idempotency_records).where(idempotency_records.c.id == record_id)
        if for_update:
            query = query.with_for_update()
        row = (await self._session.execute(query)).first()
        return None if row is None else _record(row)

    async def get_by_key(
        self, tool_definition_id: UUID, idempotency_key: str
    ) -> IdempotencyRecord | None:
        row = (
            await self._session.execute(
                select(idempotency_records).where(
                    idempotency_records.c.tool_definition_id == tool_definition_id,
                    idempotency_records.c.idempotency_key == idempotency_key,
                )
            )
        ).first()
        return None if row is None else _record(row)

    async def update(self, record: IdempotencyRecord) -> None:
        await self._session.execute(
            update(idempotency_records)
            .where(idempotency_records.c.id == record.id)
            .values(
                state=record.state.value,
                attempt_count=record.attempt_count,
                current_attempt_id=record.current_attempt_id,
                lease_expires_at=record.lease_expires_at,
                result_ref=record.result_ref,
                reconciliation_outcome=None
                if record.reconciliation_outcome is None
                else record.reconciliation_outcome.value,
                updated_at=record.updated_at,
            )
        )
