from __future__ import annotations

from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.creative.domain import (
    CreativeConcept,
    CreativeConceptDecision,
    CreativeConceptSet,
    CreativeDecisionState,
)

from .creative_schema import concept_decisions, concept_sets, concepts


def _concept(row: object) -> CreativeConcept:
    d = row._mapping  # type: ignore[attr-defined]
    return CreativeConcept(
        d["tenant_id"],
        d["product_id"],
        d["concept_set_id"],
        d["concept_key"],
        d["ordinal"],
        d["concept_payload"],
        d["semantic_digest"],
        d["id"],
        d["created_at"],
    )


async def _set_with_concepts(session: AsyncSession, row: object) -> CreativeConceptSet:
    d = row._mapping  # type: ignore[attr-defined]
    concept_rows = (
        await session.execute(
            select(concepts)
            .where(concepts.c.concept_set_id == d["id"])
            .order_by(concepts.c.ordinal)
        )
    ).all()
    return CreativeConceptSet(
        d["tenant_id"],
        d["product_id"],
        d["agent_run_id"],
        d["product_snapshot_id"],
        d["product_snapshot_digest"],
        d["research_snapshot_id"],
        d["research_snapshot_digest"],
        d["input_context_digest"],
        tuple(_concept(item) for item in concept_rows),
        d["semantic_digest"],
        id=d["id"],
        schema_version=d["schema_version"],
        created_at=d["created_at"],
    )


class SqlAlchemyCreativeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_set(self, value: CreativeConceptSet) -> None:
        await self._session.execute(
            insert(concept_sets).values(
                id=value.id,
                tenant_id=value.tenant_id,
                product_id=value.product_id,
                agent_run_id=value.agent_run_id,
                schema_version=value.schema_version,
                product_snapshot_id=value.product_snapshot_id,
                product_snapshot_digest=value.product_snapshot_digest,
                research_snapshot_id=value.research_snapshot_id,
                research_snapshot_digest=value.research_snapshot_digest,
                input_context_digest=value.input_context_digest,
                semantic_digest=value.semantic_digest,
                created_at=value.created_at,
            )
        )
        await self._session.execute(
            insert(concepts),
            [
                {
                    "id": item.id,
                    "tenant_id": item.tenant_id,
                    "concept_set_id": item.concept_set_id,
                    "product_id": item.product_id,
                    "concept_key": item.concept_key,
                    "ordinal": item.ordinal,
                    "concept_payload": dict(item.payload),
                    "semantic_digest": item.semantic_digest,
                    "created_at": item.created_at,
                }
                for item in value.concepts
            ],
        )

    async def get_concept(
        self, concept_id: UUID, *, for_update: bool = False
    ) -> CreativeConcept | None:
        query = select(concepts).where(concepts.c.id == concept_id)
        if for_update:
            query = query.with_for_update()
        row = (await self._session.execute(query)).first()
        return _concept(row) if row else None

    async def get_set(self, set_id: UUID) -> CreativeConceptSet | None:
        row = (
            await self._session.execute(select(concept_sets).where(concept_sets.c.id == set_id))
        ).first()
        return await _set_with_concepts(self._session, row) if row else None

    async def list_sets(self, product_id: UUID) -> tuple[CreativeConceptSet, ...]:
        rows = (
            await self._session.execute(
                select(concept_sets)
                .where(concept_sets.c.product_id == product_id)
                .order_by(concept_sets.c.created_at.desc())
            )
        ).all()
        return tuple([await _set_with_concepts(self._session, row) for row in rows])

    async def add_decision(self, value: CreativeConceptDecision) -> None:
        # Locking the Concept serializes competing current-state decisions; the last committed
        # append is authoritative while every historical decision remains immutable.
        await self.get_concept(value.concept_id, for_update=True)
        await self._session.execute(
            insert(concept_decisions).values(
                id=value.id,
                tenant_id=value.tenant_id,
                product_id=value.product_id,
                concept_id=value.concept_id,
                state=value.state.value,
                decided_by=value.decided_by,
                reason_code=value.reason_code,
                note=value.note,
                created_at=value.created_at,
            )
        )

    async def current_decision(self, concept_id: UUID) -> CreativeConceptDecision | None:
        row = (
            await self._session.execute(
                select(concept_decisions)
                .where(concept_decisions.c.concept_id == concept_id)
                .order_by(concept_decisions.c.created_at.desc(), concept_decisions.c.id.desc())
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        d = row._mapping
        return CreativeConceptDecision(
            d["tenant_id"],
            d["product_id"],
            d["concept_id"],
            CreativeDecisionState(d["state"]),
            d["decided_by"],
            d["reason_code"],
            d["note"],
            d["id"],
            d["created_at"],
        )
