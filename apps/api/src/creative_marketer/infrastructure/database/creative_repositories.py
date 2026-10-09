from __future__ import annotations

from uuid import UUID

from sqlalchemy import insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.catalog.domain import ProductKnowledgeSnapshot
from creative_marketer.creative.application import CreativeRevalidationPreparation
from creative_marketer.creative.domain import (
    CreativeConcept,
    CreativeConceptDecision,
    CreativeConceptRevalidation,
    CreativeConceptSet,
    CreativeDecisionState,
    CreativeRevalidationReason,
    CreativeRevalidationResult,
    RevalidatedFindingAssertion,
)

from .agent_runtime_repositories import SqlAlchemyAgentRunRepository, _snapshot
from .agent_runtime_schema import research_snapshots
from .catalog_schema import product_knowledge_snapshots
from .creative_schema import concept_decisions, concept_revalidations, concept_sets, concepts


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
        experiment_proposal_id=d["experiment_proposal_id"],
        experiment_proposal_digest=d["experiment_proposal_digest"],
    )


def _decision(row: object) -> CreativeConceptDecision:
    d = row._mapping  # type: ignore[attr-defined]
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


def _revalidation(row: object) -> CreativeConceptRevalidation:
    d = row._mapping  # type: ignore[attr-defined]
    return CreativeConceptRevalidation(
        d["tenant_id"],
        d["product_id"],
        d["concept_id"],
        d["concept_digest"],
        d["original_concept_set_id"],
        d["original_research_snapshot_id"],
        d["original_research_snapshot_digest"],
        d["current_research_snapshot_id"],
        d["current_research_snapshot_digest"],
        d["product_snapshot_id"],
        d["product_snapshot_digest"],
        d["original_decision_id"],
        CreativeRevalidationResult(d["result"]),
        tuple(CreativeRevalidationReason(item) for item in d["reason_codes"]),
        tuple(RevalidatedFindingAssertion(**item) for item in d["referenced_finding_assertions"]),
        d["created_by"],
        d["semantic_digest"],
        d["id"],
        d["created_at"],
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
                experiment_proposal_id=value.experiment_proposal_id,
                experiment_proposal_digest=value.experiment_proposal_digest,
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
            # The immutable Concepts table intentionally grants no UPDATE privilege. A
            # transaction-scoped advisory lock serializes decision appends without widening
            # that database role or pretending the Concept row itself will be updated.
            await self._session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"creative-concept-decision:{concept_id}"},
            )
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
        return _decision(row)

    async def prepare_revalidation(
        self, concept_id: UUID
    ) -> CreativeRevalidationPreparation | None:
        concept_row = (
            await self._session.execute(select(concepts).where(concepts.c.id == concept_id))
        ).first()
        if concept_row is None:
            return None
        concept = _concept(concept_row)
        set_row = (
            await self._session.execute(
                select(concept_sets).where(concept_sets.c.id == concept.concept_set_id)
            )
        ).first()
        decision_row = (
            await self._session.execute(
                select(concept_decisions)
                .where(concept_decisions.c.concept_id == concept_id)
                .order_by(concept_decisions.c.created_at.desc(), concept_decisions.c.id.desc())
                .limit(1)
            )
        ).first()
        if set_row is None or decision_row is None:
            return None
        concept_set = await _set_with_concepts(self._session, set_row)
        original_row = (
            await self._session.execute(
                select(research_snapshots).where(
                    research_snapshots.c.id == concept_set.research_snapshot_id
                )
            )
        ).first()
        current_row = (
            await self._session.execute(
                select(research_snapshots)
                .where(research_snapshots.c.product_id == concept.product_id)
                .order_by(research_snapshots.c.created_at.desc(), research_snapshots.c.id.desc())
                .limit(1)
            )
        ).first()
        product_row = (
            await self._session.execute(
                select(product_knowledge_snapshots)
                .where(product_knowledge_snapshots.c.product_id == concept.product_id)
                .order_by(
                    product_knowledge_snapshots.c.created_at.desc(),
                    product_knowledge_snapshots.c.id.desc(),
                )
                .limit(1)
            )
        ).first()
        if original_row is None or current_row is None or product_row is None:
            return None
        original, current = _snapshot(original_row), _snapshot(current_row)
        p = product_row._mapping
        product = ProductKnowledgeSnapshot(
            id=p["id"],
            tenant_id=p["tenant_id"],
            product_id=p["product_id"],
            schema_version=p["schema_version"],
            source_revision=p["source_revision"],
            content=p["content"],
            digest=p["digest"],
            created_by=p["created_by"],
            created_at=p["created_at"],
        )
        freshness = await SqlAlchemyAgentRunRepository(self._session).snapshot_freshness(current)
        return CreativeRevalidationPreparation(
            concept,
            concept_set,
            _decision(decision_row),
            original,
            current,
            freshness,
            product,
        )

    async def find_revalidation(
        self,
        concept_id: UUID,
        current_research_snapshot_id: UUID,
        original_decision_id: UUID,
    ) -> CreativeConceptRevalidation | None:
        row = (
            await self._session.execute(
                select(concept_revalidations).where(
                    concept_revalidations.c.concept_id == concept_id,
                    concept_revalidations.c.current_research_snapshot_id
                    == current_research_snapshot_id,
                    concept_revalidations.c.original_decision_id == original_decision_id,
                )
            )
        ).first()
        return _revalidation(row) if row else None

    async def add_revalidation(self, value: CreativeConceptRevalidation) -> bool:
        result = await self._session.execute(
            pg_insert(concept_revalidations)
            .values(
                id=value.id,
                tenant_id=value.tenant_id,
                product_id=value.product_id,
                concept_id=value.concept_id,
                concept_digest=value.concept_digest,
                original_concept_set_id=value.original_concept_set_id,
                original_research_snapshot_id=value.original_research_snapshot_id,
                original_research_snapshot_digest=value.original_research_snapshot_digest,
                current_research_snapshot_id=value.current_research_snapshot_id,
                current_research_snapshot_digest=value.current_research_snapshot_digest,
                product_snapshot_id=value.product_snapshot_id,
                product_snapshot_digest=value.product_snapshot_digest,
                original_decision_id=value.original_decision_id,
                result=value.result.value,
                reason_codes=[item.value for item in value.reason_codes],
                referenced_finding_assertions=[
                    item.primitive() for item in value.referenced_finding_assertions
                ],
                created_by=value.created_by,
                semantic_digest=value.semantic_digest,
                created_at=value.created_at,
            )
            .on_conflict_do_nothing(constraint="uq_concept_revalidations_authority")
            .returning(concept_revalidations.c.id)
        )
        return result.scalar_one_or_none() is not None

    async def list_revalidations(self, concept_id: UUID) -> tuple[CreativeConceptRevalidation, ...]:
        rows = (
            await self._session.execute(
                select(concept_revalidations)
                .where(concept_revalidations.c.concept_id == concept_id)
                .order_by(
                    concept_revalidations.c.created_at.desc(),
                    concept_revalidations.c.id.desc(),
                )
            )
        ).all()
        return tuple(_revalidation(row) for row in rows)
