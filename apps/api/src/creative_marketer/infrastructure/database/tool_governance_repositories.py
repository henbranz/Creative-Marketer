from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.infrastructure.database.tool_governance_schema import (
    tool_activations,
    tool_definitions,
    tool_versions,
)
from creative_marketer.tool_governance.domain import (
    CredentialBoundary,
    ExecutionClass,
    IdempotencyRequirement,
    RiskLevel,
    SideEffectClass,
    ToolActivation,
    ToolContractSchema,
    ToolDefinition,
    ToolDefinitionStatus,
    ToolRegistryConflict,
    ToolVersion,
    ToolVersionConfiguration,
    canonical_json,
)
from creative_marketer.tool_governance.schema_validation import validate_contract_schema


def _definition(row: object) -> ToolDefinition:
    data = row._mapping  # type: ignore[attr-defined]
    return ToolDefinition(
        id=data["id"],
        tool_key=data["tool_key"],
        category=data["category"],
        status=ToolDefinitionStatus(data["status"]),
        created_by_actor_kind=data["created_by_actor_kind"],
        created_by_actor_id=data["created_by_actor_id"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


def _schema(data: dict[str, Any], digest: str) -> ToolContractSchema:
    schema = ToolContractSchema(canonical_json(data), digest)
    validated = validate_contract_schema(schema.primitive())
    if validated.digest != digest:
        raise ValueError("persisted tool schema digest does not match its content")
    return schema


def _version(row: object) -> ToolVersion:
    data = row._mapping  # type: ignore[attr-defined]
    configuration = ToolVersionConfiguration(
        display_name=data["display_name"],
        description=data["description"],
        risk_level=RiskLevel(data["risk_level"]),
        side_effect_class=SideEffectClass(data["side_effect_class"]),
        execution_class=ExecutionClass(data["execution_class"]),
        credential_boundary=CredentialBoundary(data["credential_boundary"]),
        idempotency_requirement=IdempotencyRequirement(data["idempotency_requirement"]),
        input_schema=_schema(data["input_schema"], data["input_schema_digest"]),
        output_schema=_schema(data["output_schema"], data["output_schema_digest"]),
        capability_tags=tuple(data["capability_tags"]),
        configuration_schema_version=data["configuration_schema_version"],
    )
    version = ToolVersion(
        id=data["id"],
        definition_id=data["definition_id"],
        version_number=data["version_number"],
        configuration=configuration,
        created_by_actor_kind=data["created_by_actor_kind"],
        created_by_actor_id=data["created_by_actor_id"],
        created_at=data["created_at"],
    )
    if version.configuration_digest != data["configuration_digest"]:
        raise ValueError("persisted tool configuration digest does not match its content")
    return version


def _activation(row: object) -> ToolActivation:
    data = row._mapping  # type: ignore[attr-defined]
    return ToolActivation(
        definition_id=data["definition_id"],
        active_version_id=data["active_version_id"],
        activated_by_actor_kind=data["activated_by_actor_kind"],
        activated_by_actor_id=data["activated_by_actor_id"],
        activated_at=data["activated_at"],
    )


class SqlAlchemyToolDefinitionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, definition: ToolDefinition) -> None:
        try:
            await self._session.execute(
                insert(tool_definitions).values(
                    id=definition.id,
                    tool_key=definition.tool_key,
                    category=definition.category,
                    status=definition.status.value,
                    created_by_actor_kind=definition.created_by_actor_kind,
                    created_by_actor_id=definition.created_by_actor_id,
                    created_at=definition.created_at,
                    updated_at=definition.updated_at,
                )
            )
        except IntegrityError as error:
            raise ToolRegistryConflict("tool definition already exists or is invalid") from error

    async def get_by_id(
        self, definition_id: UUID, *, for_update: bool = False
    ) -> ToolDefinition | None:
        query = select(tool_definitions).where(tool_definitions.c.id == definition_id)
        if for_update:
            query = query.with_for_update()
        row = (await self._session.execute(query)).first()
        return None if row is None else _definition(row)

    async def get_by_key(self, tool_key: str) -> ToolDefinition | None:
        row = (
            await self._session.execute(
                select(tool_definitions).where(tool_definitions.c.tool_key == tool_key)
            )
        ).first()
        return None if row is None else _definition(row)

    async def list(self) -> list[ToolDefinition]:
        rows = await self._session.execute(
            select(tool_definitions).order_by(tool_definitions.c.tool_key)
        )
        return [_definition(row) for row in rows]

    async def set_status(
        self, definition_id: UUID, status: ToolDefinitionStatus, updated_at: datetime
    ) -> None:
        await self._session.execute(
            update(tool_definitions)
            .where(tool_definitions.c.id == definition_id)
            .values(status=status.value, updated_at=updated_at)
        )


class SqlAlchemyToolVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, version: ToolVersion) -> None:
        config = version.configuration
        try:
            await self._session.execute(
                insert(tool_versions).values(
                    id=version.id,
                    definition_id=version.definition_id,
                    version_number=version.version_number,
                    display_name=config.display_name,
                    description=config.description,
                    risk_level=config.risk_level.value,
                    side_effect_class=config.side_effect_class.value,
                    execution_class=config.execution_class.value,
                    credential_boundary=config.credential_boundary.value,
                    idempotency_requirement=config.idempotency_requirement.value,
                    input_schema=config.input_schema.primitive(),
                    output_schema=config.output_schema.primitive(),
                    input_schema_digest=config.input_schema.digest,
                    output_schema_digest=config.output_schema.digest,
                    capability_tags=list(config.capability_tags),
                    configuration_schema_version=config.configuration_schema_version,
                    configuration_digest=version.configuration_digest,
                    created_by_actor_kind=version.created_by_actor_kind,
                    created_by_actor_id=version.created_by_actor_id,
                    created_at=version.created_at,
                )
            )
        except IntegrityError as error:
            raise ToolRegistryConflict("tool version already exists or is invalid") from error

    async def get(self, version_id: UUID) -> ToolVersion | None:
        row = (
            await self._session.execute(
                select(tool_versions).where(tool_versions.c.id == version_id)
            )
        ).first()
        return None if row is None else _version(row)

    async def list_for_definition(self, definition_id: UUID) -> list[ToolVersion]:
        rows = await self._session.execute(
            select(tool_versions)
            .where(tool_versions.c.definition_id == definition_id)
            .order_by(tool_versions.c.version_number)
        )
        return [_version(row) for row in rows]

    async def next_version_number(self, definition_id: UUID) -> int:
        value = await self._session.scalar(
            select(func.coalesce(func.max(tool_versions.c.version_number), 0) + 1).where(
                tool_versions.c.definition_id == definition_id
            )
        )
        if value is None:
            raise RuntimeError("database did not allocate a tool version number")
        return int(value)


class SqlAlchemyToolActivationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, definition_id: UUID) -> ToolActivation | None:
        row = (
            await self._session.execute(
                select(tool_activations).where(tool_activations.c.definition_id == definition_id)
            )
        ).first()
        return None if row is None else _activation(row)

    async def set(self, activation: ToolActivation) -> None:
        statement = postgresql_insert(tool_activations).values(
            definition_id=activation.definition_id,
            active_version_id=activation.active_version_id,
            activated_by_actor_kind=activation.activated_by_actor_kind,
            activated_by_actor_id=activation.activated_by_actor_id,
            activated_at=activation.activated_at,
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[tool_activations.c.definition_id],
                set_={
                    "active_version_id": statement.excluded.active_version_id,
                    "activated_by_actor_kind": statement.excluded.activated_by_actor_kind,
                    "activated_by_actor_id": statement.excluded.activated_by_actor_id,
                    "activated_at": statement.excluded.activated_at,
                },
            )
        )
