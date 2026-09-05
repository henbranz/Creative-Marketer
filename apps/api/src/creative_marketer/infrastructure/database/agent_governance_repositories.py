from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.agent_governance.domain import (
    AgentActivation,
    AgentDefinition,
    AgentDefinitionStatus,
    AgentRegistryConflict,
    AgentScopeKind,
    AgentVersion,
    AgentVersionConfiguration,
    BudgetPeriod,
    ModelPolicy,
    PeriodBudgetPolicy,
    RunBudgetPolicy,
)
from creative_marketer.infrastructure.database.agent_governance_schema import (
    agent_activations,
    agent_definitions,
    agent_versions,
)


def _definition(row: object) -> AgentDefinition:
    data = row._mapping  # type: ignore[attr-defined]
    return AgentDefinition(
        id=data["id"],
        scope_kind=AgentScopeKind(data["scope_kind"]),
        tenant_id=data["tenant_id"],
        platform_template_id=data["platform_template_id"],
        agent_key=data["agent_key"],
        agent_type=data["agent_type"],
        status=AgentDefinitionStatus(data["status"]),
        created_by_actor_kind=data["created_by_actor_kind"],
        created_by_actor_id=data["created_by_actor_id"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


def _configuration(data: Any) -> AgentVersionConfiguration:
    model = cast(dict[str, Any], data["model_policy"])
    run = cast(dict[str, Any], data["run_budget_policy"])
    period = cast(dict[str, Any], data["period_budget_policy"])
    return AgentVersionConfiguration(
        display_name=data["display_name"],
        mission=data["mission"],
        responsibilities=tuple(data["responsibilities"]),
        system_instructions=data["system_instructions"],
        prompt_revision=data["prompt_revision"],
        model_policy=ModelPolicy(
            profile_key=model["profile_key"],
            required_capabilities=tuple(model["required_capabilities"]),
            max_turns=model["max_turns"],
            structured_output_required=model["structured_output_required"],
            fallback_allowed=model["fallback_allowed"],
        ),
        run_budget_policy=RunBudgetPolicy(
            max_model_calls=run["max_model_calls"],
            max_tool_calls=run["max_tool_calls"],
            max_total_tokens=run["max_total_tokens"],
            max_cost=Decimal(run["max_cost"]),
            currency=run["currency"],
        ),
        period_budget_policy=PeriodBudgetPolicy(
            period=BudgetPeriod(period["period"]),
            max_runs=period["max_runs"],
            max_cost=Decimal(period["max_cost"]),
            currency=period["currency"],
        ),
        read_scopes=tuple(data["read_scopes"]),
        write_scopes=tuple(data["write_scopes"]),
        memory_scopes=tuple(data["memory_scopes"]),
        allowed_tool_keys=tuple(data["allowed_tool_keys"]),
        denied_tool_keys=tuple(data["denied_tool_keys"]),
        approval_policy_key=data["approval_policy_key"],
        output_contract_key=data["output_contract_key"],
        output_contract_version=data["output_contract_version"],
        configuration_schema_version=data["configuration_schema_version"],
    )


def _version(row: object) -> AgentVersion:
    data = row._mapping  # type: ignore[attr-defined]
    version = AgentVersion(
        id=data["id"],
        definition_id=data["definition_id"],
        scope_kind=AgentScopeKind(data["scope_kind"]),
        tenant_id=data["tenant_id"],
        version_number=data["version_number"],
        configuration=_configuration(data),
        created_by_actor_kind=data["created_by_actor_kind"],
        created_by_actor_id=data["created_by_actor_id"],
        created_at=data["created_at"],
    )
    if version.configuration_digest != data["configuration_digest"]:
        raise ValueError("persisted agent configuration digest does not match its content")
    return version


def _activation(row: object) -> AgentActivation:
    data = row._mapping  # type: ignore[attr-defined]
    return AgentActivation(
        definition_id=data["definition_id"],
        active_version_id=data["active_version_id"],
        scope_kind=AgentScopeKind(data["scope_kind"]),
        tenant_id=data["tenant_id"],
        activated_by_actor_kind=data["activated_by_actor_kind"],
        activated_by_actor_id=data["activated_by_actor_id"],
        activated_at=data["activated_at"],
    )


class SqlAlchemyAgentDefinitionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, definition: AgentDefinition) -> None:
        try:
            await self._session.execute(
                insert(agent_definitions).values(
                    id=definition.id,
                    scope_kind=definition.scope_kind.value,
                    tenant_id=definition.tenant_id,
                    platform_template_id=definition.platform_template_id,
                    agent_key=definition.agent_key,
                    agent_type=definition.agent_type,
                    status=definition.status.value,
                    created_by_actor_kind=definition.created_by_actor_kind,
                    created_by_actor_id=definition.created_by_actor_id,
                    created_at=definition.created_at,
                    updated_at=definition.updated_at,
                )
            )
        except IntegrityError as error:
            raise AgentRegistryConflict("agent definition already exists or is invalid") from error

    async def get(self, definition_id: UUID, *, for_update: bool = False) -> AgentDefinition | None:
        query = select(agent_definitions).where(agent_definitions.c.id == definition_id)
        if for_update:
            query = query.with_for_update()
        row = (await self._session.execute(query)).first()
        return None if row is None else _definition(row)

    async def list_tenant(self) -> list[AgentDefinition]:
        result = await self._session.execute(
            select(agent_definitions)
            .where(agent_definitions.c.scope_kind == AgentScopeKind.TENANT.value)
            .order_by(agent_definitions.c.agent_key)
        )
        return [_definition(row) for row in result]

    async def set_status(
        self, definition_id: UUID, status: AgentDefinitionStatus, updated_at: datetime
    ) -> None:
        await self._session.execute(
            update(agent_definitions)
            .where(agent_definitions.c.id == definition_id)
            .values(status=status.value, updated_at=updated_at)
        )


class SqlAlchemyAgentVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, version: AgentVersion) -> None:
        configuration = version.configuration
        try:
            await self._session.execute(
                insert(agent_versions).values(
                    id=version.id,
                    definition_id=version.definition_id,
                    scope_kind=version.scope_kind.value,
                    tenant_id=version.tenant_id,
                    version_number=version.version_number,
                    **configuration.primitive(),
                    configuration_digest=version.configuration_digest,
                    created_by_actor_kind=version.created_by_actor_kind,
                    created_by_actor_id=version.created_by_actor_id,
                    created_at=version.created_at,
                )
            )
        except IntegrityError as error:
            raise AgentRegistryConflict("agent version already exists or is invalid") from error

    async def get(self, version_id: UUID) -> AgentVersion | None:
        row = (
            await self._session.execute(
                select(agent_versions).where(agent_versions.c.id == version_id)
            )
        ).first()
        return None if row is None else _version(row)

    async def list_for_definition(self, definition_id: UUID) -> list[AgentVersion]:
        result = await self._session.execute(
            select(agent_versions)
            .where(agent_versions.c.definition_id == definition_id)
            .order_by(agent_versions.c.version_number)
        )
        return [_version(row) for row in result]

    async def next_version_number(self, definition_id: UUID) -> int:
        value = await self._session.scalar(
            select(func.coalesce(func.max(agent_versions.c.version_number), 0) + 1).where(
                agent_versions.c.definition_id == definition_id
            )
        )
        if value is None:
            raise RuntimeError("database did not allocate an agent version number")
        return int(value)


class SqlAlchemyAgentActivationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, definition_id: UUID) -> AgentActivation | None:
        row = (
            await self._session.execute(
                select(agent_activations).where(agent_activations.c.definition_id == definition_id)
            )
        ).first()
        return None if row is None else _activation(row)

    async def set(self, activation: AgentActivation) -> None:
        statement = postgresql_insert(agent_activations).values(
            definition_id=activation.definition_id,
            active_version_id=activation.active_version_id,
            scope_kind=activation.scope_kind.value,
            tenant_id=activation.tenant_id,
            activated_by_actor_kind=activation.activated_by_actor_kind,
            activated_by_actor_id=activation.activated_by_actor_id,
            activated_at=activation.activated_at,
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[agent_activations.c.definition_id],
                set_={
                    "active_version_id": statement.excluded.active_version_id,
                    "activated_by_actor_kind": statement.excluded.activated_by_actor_kind,
                    "activated_by_actor_id": statement.excluded.activated_by_actor_id,
                    "activated_at": statement.excluded.activated_at,
                },
            )
        )
