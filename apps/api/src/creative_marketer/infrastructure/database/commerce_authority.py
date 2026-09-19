from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from creative_marketer.agent_runtime.domain import canonical_digest
from creative_marketer.commerce.domain import (
    ActionJobStatus,
    ActionType,
    CommerceActionProposal,
    CommerceNotFound,
    CommercePermissionDenied,
    ConnectionStatus,
    OperationStatus,
    canonical_money_text,
    proposal_digest,
)
from creative_marketer.commerce.provider import MutationDisposition, ProviderMutationResult

from .commerce_schema import (
    action_jobs,
    action_proposals,
    action_results,
    connections,
    inventory_observations,
    order_observations,
)


def _proposal(row: Mapping[str, Any]) -> CommerceActionProposal:
    return CommerceActionProposal(
        row["tenant_id"],
        row["connection_id"],
        row["product_id"],
        ActionType(row["action_type"]),
        row["external_product_id"],
        row["external_variant_id"],
        row["external_order_id"],
        row["exact_quantity"],
        Decimal(row["exact_amount"]) if row["exact_amount"] is not None else None,
        row["currency"],
        row["reason"],
        tuple(row["evidence_refs"]),
        row["semantic_digest"],
        row["id"],
        row["schema_version"],
        row["created_at"],
    )


class SqlAlchemyCommerceExecutionAuthority:
    """Reloads immutable proposal and current external facts immediately before provider I/O."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    @staticmethod
    async def _tenant(session: AsyncSession, tenant_id: UUID) -> None:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )

    async def authorize_resource(self, tenant_id: UUID, proposal_id: UUID) -> None:
        async with self._factory() as session, session.begin():
            await self._tenant(session, tenant_id)
            found = await session.scalar(
                select(action_proposals.c.id).where(
                    action_proposals.c.tenant_id == tenant_id,
                    action_proposals.c.id == proposal_id,
                )
            )
            if found is None:
                raise CommerceNotFound("commerce proposal not found")

    async def prepare(
        self, tenant_id: UUID, proposal_id: UUID
    ) -> tuple[CommerceActionProposal, str]:
        async with self._factory() as session, session.begin():
            await self._tenant(session, tenant_id)
            proposal, external_store_id = await self._load(session, tenant_id, proposal_id)
            await self._validate_current_authority(session, proposal)
            return proposal, external_store_id

    async def operation(
        self, tenant_id: UUID, proposal_id: UUID
    ) -> tuple[CommerceActionProposal, str, str]:
        async with self._factory() as session, session.begin():
            await self._tenant(session, tenant_id)
            proposal, external_store_id = await self._load(session, tenant_id, proposal_id)
            operation_id = await session.scalar(
                select(action_jobs.c.external_operation_id)
                .where(
                    action_jobs.c.tenant_id == tenant_id,
                    action_jobs.c.proposal_id == proposal_id,
                    action_jobs.c.proposal_digest == proposal.semantic_digest,
                    action_jobs.c.status.in_(
                        [
                            ActionJobStatus.SUBMITTED.value,
                            ActionJobStatus.OUTCOME_UNKNOWN.value,
                        ]
                    ),
                )
                .order_by(action_jobs.c.created_at.desc())
                .limit(1)
            )
            if operation_id is None:
                raise CommercePermissionDenied("no submitted operation is available to reconcile")
            return proposal, external_store_id, str(operation_id)

    async def begin(
        self, tenant_id: UUID, proposal: CommerceActionProposal, idempotency_key: str
    ) -> None:
        async with self._factory() as session, session.begin():
            await self._tenant(session, tenant_id)
            await session.execute(
                pg_insert(action_jobs)
                .values(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    proposal_id=proposal.id,
                    proposal_digest=proposal.semantic_digest,
                    idempotency_key=idempotency_key,
                    status=ActionJobStatus.SUBMITTING.value,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key"])
            )

    async def record(
        self,
        tenant_id: UUID,
        proposal: CommerceActionProposal,
        result: ProviderMutationResult,
    ) -> None:
        status = {
            MutationDisposition.ACCEPTED: ActionJobStatus.SUCCEEDED,
            MutationDisposition.FAILED: ActionJobStatus.FAILED,
            MutationDisposition.OUTCOME_UNKNOWN: ActionJobStatus.OUTCOME_UNKNOWN,
        }[result.disposition]
        now = datetime.now(UTC)
        async with self._factory() as session, session.begin():
            await self._tenant(session, tenant_id)
            changed = (
                await session.execute(
                    update(action_jobs)
                    .where(
                        action_jobs.c.tenant_id == tenant_id,
                        action_jobs.c.proposal_id == proposal.id,
                        action_jobs.c.proposal_digest == proposal.semantic_digest,
                        action_jobs.c.status.in_(
                            [
                                ActionJobStatus.SUBMITTING.value,
                                ActionJobStatus.SUBMITTED.value,
                                ActionJobStatus.OUTCOME_UNKNOWN.value,
                            ]
                        ),
                    )
                    .values(
                        status=status.value,
                        external_operation_id=result.operation_id,
                        safe_failure_code=result.safe_failure_code,
                        updated_at=now,
                    )
                    .returning(action_jobs.c.id)
                )
            ).scalar_one_or_none()
            if changed is None:
                replay = await session.scalar(
                    select(action_jobs.c.id).where(
                        action_jobs.c.tenant_id == tenant_id,
                        action_jobs.c.proposal_id == proposal.id,
                        action_jobs.c.proposal_digest == proposal.semantic_digest,
                        action_jobs.c.external_operation_id == result.operation_id,
                    )
                )
                if replay is None:
                    raise CommercePermissionDenied("commerce action job authority changed")
            if result.disposition is MutationDisposition.ACCEPTED:
                material: dict[str, object] = {
                    "proposal_id": str(proposal.id),
                    "proposal_digest": proposal.semantic_digest,
                    "action_type": proposal.action_type.value,
                    "provider": "fake",
                    "external_operation_id": result.operation_id,
                    "status": OperationStatus.SUCCEEDED.value,
                    "observed_fact_refs": [],
                }
                await session.execute(
                    pg_insert(action_results)
                    .values(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        proposal_id=proposal.id,
                        proposal_digest=proposal.semantic_digest,
                        action_type=proposal.action_type.value,
                        provider="fake",
                        status=OperationStatus.SUCCEEDED.value,
                        external_operation_id=result.operation_id,
                        observed_fact_refs=[],
                        semantic_digest=canonical_digest(material),
                        completed_at=now,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["tenant_id", "proposal_id", "proposal_digest"]
                    )
                )

    async def _load(
        self, session: AsyncSession, tenant_id: UUID, proposal_id: UUID
    ) -> tuple[CommerceActionProposal, str]:
        row = (
            (
                await session.execute(
                    select(action_proposals, connections.c.external_store_id, connections.c.status)
                    .join(
                        connections,
                        (connections.c.tenant_id == action_proposals.c.tenant_id)
                        & (connections.c.id == action_proposals.c.connection_id),
                    )
                    .where(
                        action_proposals.c.tenant_id == tenant_id,
                        action_proposals.c.id == proposal_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise CommerceNotFound("commerce proposal not found")
        if row["status"] != ConnectionStatus.ACTIVE.value:
            raise CommercePermissionDenied("commerce connection is not active")
        proposal = _proposal(cast(Mapping[str, Any], row))
        self._validate_digest(proposal)
        return proposal, str(row["external_store_id"])

    @staticmethod
    def _validate_digest(proposal: CommerceActionProposal) -> None:
        material: dict[str, object] = {
            "connection_id": str(proposal.connection_id),
            "product_id": str(proposal.product_id),
            "action_type": proposal.action_type.value,
        }
        if proposal.action_type is ActionType.INVENTORY_ADJUSTMENT:
            material.update(
                {
                    "external_product_id": proposal.external_product_id,
                    "external_variant_id": proposal.external_variant_id,
                    "exact_quantity": proposal.exact_quantity,
                    "reason": proposal.reason,
                    "evidence_refs": [dict(value) for value in proposal.evidence_refs],
                    "strategy": "SET_AVAILABLE_TO",
                    "schema_version": proposal.schema_version,
                }
            )
        else:
            material.update(
                {
                    "external_order_id": proposal.external_order_id,
                    "exact_amount": canonical_money_text(cast(Decimal, proposal.exact_amount)),
                    "currency": proposal.currency,
                    "reason": proposal.reason,
                    "evidence_refs": [dict(value) for value in proposal.evidence_refs],
                    "schema_version": proposal.schema_version,
                }
            )
        if proposal.semantic_digest != proposal_digest(material):
            raise CommercePermissionDenied("commerce proposal digest binding is invalid")

    @staticmethod
    async def _validate_current_authority(
        session: AsyncSession, proposal: CommerceActionProposal
    ) -> None:
        already_succeeded = await session.scalar(
            select(action_results.c.id).where(
                action_results.c.tenant_id == proposal.tenant_id,
                action_results.c.proposal_id == proposal.id,
                action_results.c.proposal_digest == proposal.semantic_digest,
                action_results.c.status == OperationStatus.SUCCEEDED.value,
            )
        )
        if already_succeeded is not None:
            raise CommercePermissionDenied("commerce proposal already executed")
        if proposal.action_type is ActionType.INVENTORY_ADJUSTMENT:
            current = (
                await session.execute(
                    select(inventory_observations.c.id)
                    .where(
                        inventory_observations.c.tenant_id == proposal.tenant_id,
                        inventory_observations.c.connection_id == proposal.connection_id,
                        inventory_observations.c.external_variant_id
                        == proposal.external_variant_id,
                    )
                    .order_by(inventory_observations.c.captured_at.desc())
                    .limit(1)
                )
            ).first()
            if current is None:
                raise CommercePermissionDenied("inventory target is no longer observed")
            return
        current_order = (
            await session.execute(
                select(
                    order_observations.c.financial_status,
                    order_observations.c.currency,
                    order_observations.c.total,
                )
                .where(
                    order_observations.c.tenant_id == proposal.tenant_id,
                    order_observations.c.connection_id == proposal.connection_id,
                    order_observations.c.external_order_id == proposal.external_order_id,
                )
                .order_by(order_observations.c.captured_at.desc())
                .limit(1)
            )
        ).first()
        if (
            current_order is None
            or current_order.financial_status != "PAID"
            or current_order.currency != proposal.currency
            or Decimal(current_order.total) < cast(Decimal, proposal.exact_amount)
        ):
            raise CommercePermissionDenied("refund authority is no longer current")
