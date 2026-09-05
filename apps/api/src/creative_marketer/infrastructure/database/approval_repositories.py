from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.action_binding import ActionBindingV1
from creative_marketer.approval_governance.domain import (
    ApprovalConflict,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRevocation,
    HumanDecision,
)
from creative_marketer.infrastructure.database.approval_schema import (
    approval_decisions,
    approval_requests,
    approval_revocations,
)
from creative_marketer.tool_governance.domain import RiskLevel


def _binding(data: object) -> ActionBindingV1:
    row = data._mapping  # type: ignore[attr-defined]
    return ActionBindingV1(
        tenant_id=row["tenant_id"],
        requested_agent_definition_id=row["requested_agent_definition_id"],
        resolved_agent_definition_id=row["resolved_agent_definition_id"],
        agent_version_id=row["agent_version_id"],
        agent_configuration_digest=row["agent_configuration_digest"],
        tool_definition_id=row["tool_definition_id"],
        tool_version_id=row["tool_version_id"],
        tool_configuration_digest=row["tool_configuration_digest"],
        tool_key=row["tool_key"],
        risk_level=RiskLevel(row["risk_level"]),
        permission_id=row["permission_id"],
        permission_version_id=row["permission_version_id"],
        permission_configuration_digest=row["permission_configuration_digest"],
        permission_engine_version=row["permission_engine_version"],
        scope_request_digest=row["scope_request_digest"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        environment=row["environment"],
        normalized_input_digest=row["normalized_input_digest"],
        idempotency_key=row["idempotency_key"],
        canonicalization_version=row["canonicalization_version"],
    )


def _request(row: object) -> ApprovalRequest:
    data = row._mapping  # type: ignore[attr-defined]
    request = ApprovalRequest(
        id=data["id"],
        binding=_binding(row),
        requested_by_actor_kind=data["requested_by_actor_kind"],
        requested_by_actor_id=data["requested_by_actor_id"],
        created_at=data["created_at"],
        expires_at=data["expires_at"],
    )
    if request.action_digest != data["action_digest"]:
        raise ValueError("persisted approval action digest mismatch")
    return request


def _decision(row: object) -> ApprovalDecision:
    data = row._mapping  # type: ignore[attr-defined]
    return ApprovalDecision(
        id=data["id"],
        approval_request_id=data["approval_request_id"],
        tenant_id=data["tenant_id"],
        decision=HumanDecision(data["decision"]),
        decided_by_user_id=data["decided_by_user_id"],
        decided_by_actor_kind=data["decided_by_actor_kind"],
        decided_at=data["decided_at"],
        reason_code=data["reason_code"],
        safe_note=data["safe_note"],
    )


def _revocation(row: object) -> ApprovalRevocation:
    data = row._mapping  # type: ignore[attr-defined]
    return ApprovalRevocation(
        id=data["id"],
        approval_request_id=data["approval_request_id"],
        tenant_id=data["tenant_id"],
        revoked_by_user_id=data["revoked_by_user_id"],
        revoked_at=data["revoked_at"],
        reason_code=data["reason_code"],
    )


class SqlAlchemyApprovalRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, request: ApprovalRequest) -> None:
        binding = request.binding
        try:
            await self._session.execute(
                insert(approval_requests).values(
                    id=request.id,
                    tenant_id=binding.tenant_id,
                    requested_by_actor_kind=request.requested_by_actor_kind,
                    requested_by_actor_id=request.requested_by_actor_id,
                    requested_agent_definition_id=binding.requested_agent_definition_id,
                    resolved_agent_definition_id=binding.resolved_agent_definition_id,
                    agent_version_id=binding.agent_version_id,
                    agent_configuration_digest=binding.agent_configuration_digest,
                    tool_definition_id=binding.tool_definition_id,
                    tool_version_id=binding.tool_version_id,
                    tool_configuration_digest=binding.tool_configuration_digest,
                    tool_key=binding.tool_key,
                    risk_level=binding.risk_level.value,
                    permission_id=binding.permission_id,
                    permission_version_id=binding.permission_version_id,
                    permission_configuration_digest=binding.permission_configuration_digest,
                    permission_engine_version=binding.permission_engine_version,
                    scope_request_digest=binding.scope_request_digest,
                    resource_type=binding.resource_type,
                    resource_id=binding.resource_id,
                    environment=binding.environment,
                    normalized_input_digest=binding.normalized_input_digest,
                    idempotency_key=binding.idempotency_key,
                    canonicalization_version=binding.canonicalization_version,
                    action_digest=binding.action_digest,
                    created_at=request.created_at,
                    expires_at=request.expires_at,
                )
            )
        except IntegrityError as error:
            raise ApprovalConflict("approval request already exists or is invalid") from error

    async def get(self, approval_id: UUID, *, for_update: bool = False) -> ApprovalRequest | None:
        query = select(approval_requests).where(approval_requests.c.id == approval_id)
        if for_update:
            query = query.with_for_update()
        row = (await self._session.execute(query)).first()
        return None if row is None else _request(row)


class SqlAlchemyApprovalDecisionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, decision: ApprovalDecision) -> None:
        try:
            await self._session.execute(
                insert(approval_decisions).values(
                    id=decision.id,
                    tenant_id=decision.tenant_id,
                    approval_request_id=decision.approval_request_id,
                    decision=decision.decision.value,
                    decided_by_user_id=decision.decided_by_user_id,
                    decided_by_actor_kind=decision.decided_by_actor_kind,
                    decided_at=decision.decided_at,
                    reason_code=decision.reason_code,
                    safe_note=decision.safe_note,
                )
            )
        except IntegrityError as error:
            raise ApprovalConflict("approval request is already decided") from error

    async def get(self, approval_id: UUID) -> ApprovalDecision | None:
        row = (
            await self._session.execute(
                select(approval_decisions).where(
                    approval_decisions.c.approval_request_id == approval_id
                )
            )
        ).first()
        return None if row is None else _decision(row)


class SqlAlchemyApprovalRevocationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, revocation: ApprovalRevocation) -> None:
        try:
            await self._session.execute(
                insert(approval_revocations).values(
                    id=revocation.id,
                    tenant_id=revocation.tenant_id,
                    approval_request_id=revocation.approval_request_id,
                    revoked_by_user_id=revocation.revoked_by_user_id,
                    revoked_at=revocation.revoked_at,
                    reason_code=revocation.reason_code,
                )
            )
        except IntegrityError as error:
            raise ApprovalConflict("approval request is already revoked") from error

    async def get(self, approval_id: UUID) -> ApprovalRevocation | None:
        row = (
            await self._session.execute(
                select(approval_revocations).where(
                    approval_revocations.c.approval_request_id == approval_id
                )
            )
        ).first()
        return None if row is None else _revocation(row)
