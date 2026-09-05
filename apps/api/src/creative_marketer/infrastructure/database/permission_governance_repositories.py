from datetime import datetime
from uuid import UUID

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.infrastructure.database.permission_governance_schema import (
    tool_permission_activations,
    tool_permission_versions,
    tool_permissions,
)
from creative_marketer.permission_governance.domain import (
    ApprovalBehavior,
    PermissionEffect,
    PermissionGovernanceConflict,
    PermissionStatus,
    ToolPermission,
    ToolPermissionActivation,
    ToolPermissionVersion,
    ToolPermissionVersionConfiguration,
)


def _permission(row: object) -> ToolPermission:
    data = row._mapping  # type: ignore[attr-defined]
    return ToolPermission(
        id=data["id"],
        tenant_id=data["tenant_id"],
        agent_definition_id=data["agent_definition_id"],
        tool_definition_id=data["tool_definition_id"],
        status=PermissionStatus(data["status"]),
        created_by_actor_kind=data["created_by_actor_kind"],
        created_by_actor_id=data["created_by_actor_id"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


def _version(row: object) -> ToolPermissionVersion:
    data = row._mapping  # type: ignore[attr-defined]
    config = ToolPermissionVersionConfiguration(
        effect=PermissionEffect(data["effect"]),
        allowed_scopes=tuple(data["allowed_scopes"]),
        allowed_environments=tuple(data["allowed_environments"]),
        approval_behavior=ApprovalBehavior(data["approval_behavior"]),
        policy_schema_version=data["policy_schema_version"],
    )
    version = ToolPermissionVersion(
        id=data["id"],
        permission_id=data["permission_id"],
        tenant_id=data["tenant_id"],
        version_number=data["version_number"],
        configuration=config,
        created_by_actor_kind=data["created_by_actor_kind"],
        created_by_actor_id=data["created_by_actor_id"],
        created_at=data["created_at"],
    )
    if version.configuration_digest != data["configuration_digest"]:
        raise ValueError("persisted permission configuration digest does not match")
    return version


def _activation(row: object) -> ToolPermissionActivation:
    data = row._mapping  # type: ignore[attr-defined]
    return ToolPermissionActivation(**dict(data))


class SqlAlchemyPermissionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, permission: ToolPermission) -> None:
        try:
            await self._session.execute(
                insert(tool_permissions).values(
                    id=permission.id,
                    tenant_id=permission.tenant_id,
                    agent_definition_id=permission.agent_definition_id,
                    tool_definition_id=permission.tool_definition_id,
                    status=permission.status.value,
                    created_by_actor_kind=permission.created_by_actor_kind,
                    created_by_actor_id=permission.created_by_actor_id,
                    created_at=permission.created_at,
                    updated_at=permission.updated_at,
                )
            )
        except IntegrityError as error:
            raise PermissionGovernanceConflict(
                "permission identity already exists or is invalid"
            ) from error

    async def get(self, permission_id: UUID, *, for_update: bool = False) -> ToolPermission | None:
        query = select(tool_permissions).where(tool_permissions.c.id == permission_id)
        if for_update:
            query = query.with_for_update()
        row = (await self._session.execute(query)).first()
        return None if row is None else _permission(row)

    async def get_for_subject(
        self, agent_definition_id: UUID, tool_definition_id: UUID
    ) -> ToolPermission | None:
        row = (
            await self._session.execute(
                select(tool_permissions).where(
                    tool_permissions.c.agent_definition_id == agent_definition_id,
                    tool_permissions.c.tool_definition_id == tool_definition_id,
                )
            )
        ).first()
        return None if row is None else _permission(row)

    async def set_status(
        self, permission_id: UUID, status: PermissionStatus, updated_at: datetime
    ) -> None:
        await self._session.execute(
            update(tool_permissions)
            .where(tool_permissions.c.id == permission_id)
            .values(status=status.value, updated_at=updated_at)
        )


class SqlAlchemyPermissionVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, version: ToolPermissionVersion) -> None:
        config = version.configuration
        try:
            await self._session.execute(
                insert(tool_permission_versions).values(
                    id=version.id,
                    permission_id=version.permission_id,
                    tenant_id=version.tenant_id,
                    version_number=version.version_number,
                    effect=config.effect.value,
                    allowed_scopes=list(config.allowed_scopes),
                    allowed_environments=list(config.allowed_environments),
                    approval_behavior=config.approval_behavior.value,
                    policy_schema_version=config.policy_schema_version,
                    configuration_digest=version.configuration_digest,
                    created_by_actor_kind=version.created_by_actor_kind,
                    created_by_actor_id=version.created_by_actor_id,
                    created_at=version.created_at,
                )
            )
        except IntegrityError as error:
            raise PermissionGovernanceConflict(
                "permission version already exists or is invalid"
            ) from error

    async def get(self, version_id: UUID) -> ToolPermissionVersion | None:
        row = (
            await self._session.execute(
                select(tool_permission_versions).where(tool_permission_versions.c.id == version_id)
            )
        ).first()
        return None if row is None else _version(row)

    async def next_version_number(self, permission_id: UUID) -> int:
        value = await self._session.scalar(
            select(func.coalesce(func.max(tool_permission_versions.c.version_number), 0) + 1).where(
                tool_permission_versions.c.permission_id == permission_id
            )
        )
        if value is None:
            raise RuntimeError("database did not allocate permission version")
        return int(value)


class SqlAlchemyPermissionActivationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, permission_id: UUID) -> ToolPermissionActivation | None:
        row = (
            await self._session.execute(
                select(tool_permission_activations).where(
                    tool_permission_activations.c.permission_id == permission_id
                )
            )
        ).first()
        return None if row is None else _activation(row)

    async def set(self, activation: ToolPermissionActivation) -> None:
        statement = pg_insert(tool_permission_activations).values(
            permission_id=activation.permission_id,
            tenant_id=activation.tenant_id,
            active_version_id=activation.active_version_id,
            activated_by_actor_kind=activation.activated_by_actor_kind,
            activated_by_actor_id=activation.activated_by_actor_id,
            activated_at=activation.activated_at,
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[tool_permission_activations.c.permission_id],
                set_={
                    "active_version_id": statement.excluded.active_version_id,
                    "activated_by_actor_kind": statement.excluded.activated_by_actor_kind,
                    "activated_by_actor_id": statement.excluded.activated_by_actor_id,
                    "activated_at": statement.excluded.activated_at,
                },
            )
        )
