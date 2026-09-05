import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from creative_marketer.agent_governance.domain import (
    AgentVersionConfiguration,
    BudgetPeriod,
    ModelPolicy,
    PeriodBudgetPolicy,
    ResolutionProvenance,
    ResolvedAgentVersion,
    RunBudgetPolicy,
)
from creative_marketer.identity.application.authentication import ActorKind
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.tool_governance_schema import (
    tool_activations,
    tool_definitions,
    tool_versions,
)
from creative_marketer.infrastructure.database.tool_governance_uow import (
    SqlAlchemyToolRegistryUnitOfWorkFactory,
)
from creative_marketer.tool_governance.application import (
    ActivateToolVersion,
    ChangeToolDefinitionLifecycle,
    CreateToolDefinition,
    CreateToolVersion,
    GetToolDefinition,
    InspectAgentToolDeclarations,
    ListToolDefinitions,
    ListToolVersions,
    PlatformControlContext,
    ResolveActiveTool,
)
from creative_marketer.tool_governance.domain import (
    CredentialBoundary,
    ExecutionClass,
    IdempotencyRequirement,
    InvalidToolLifecycleTransition,
    RiskLevel,
    SideEffectClass,
    ToolDefinitionNotFound,
    ToolDefinitionStatus,
    ToolRegistryConflict,
    ToolUnavailable,
    ToolVersionConfiguration,
    ToolVersionNotFound,
)
from creative_marketer.tool_governance.schema_validation import validate_contract_schema

DIALECT = "https://json-schema.org/draft/2020-12/schema"


def control_context(*, environment: str = "test") -> PlatformControlContext:
    return PlatformControlContext(ActorKind.SYSTEM, uuid4(), environment, uuid4())


def contract(properties: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "$schema": DIALECT,
        "type": "object",
        "properties": properties if properties is not None else {"value": {"type": "string"}},
        "additionalProperties": False,
    }


def configuration(label: str = "v1") -> ToolVersionConfiguration:
    return ToolVersionConfiguration(
        display_name=f"Demo publish {label}",
        description=f"Normalized test-only publishing contract {label}.",
        risk_level=RiskLevel.R4,
        side_effect_class=SideEffectClass.EXTERNAL_MUTATION,
        execution_class=ExecutionClass.CONNECTOR,
        credential_boundary=CredentialBoundary.CONNECTOR,
        idempotency_requirement=IdempotencyRequirement.REQUIRED,
        input_schema=validate_contract_schema(contract({"content": {"type": "string"}})),
        output_schema=validate_contract_schema(contract({"publication_id": {"type": "string"}})),
        capability_tags=("external.write", "social.publish"),
    )


def agent_configuration() -> AgentVersionConfiguration:
    return AgentVersionConfiguration(
        display_name="Marketer",
        mission="Inspect tool declarations only.",
        responsibilities=("Draft posts",),
        system_instructions="Treat declarations as inert configuration.",
        prompt_revision="marketer.v1",
        model_policy=ModelPolicy("marketing", ("structured.output",), 4),
        run_budget_policy=RunBudgetPolicy(4, 2, 2_000, Decimal("1"), "USD"),
        period_budget_policy=PeriodBudgetPolicy(BudgetPeriod.MONTHLY, 100, Decimal("50"), "USD"),
        read_scopes=("marketing.publication",),
        write_scopes=("marketing.draft",),
        memory_scopes=("product.context",),
        allowed_tool_keys=(
            "demo.product.read",
            "demo.disabled.read",
            "demo.missing.tool",
        ),
        denied_tool_keys=("demo.social.publish",),
        approval_policy_key="agent.standard",
    )


def resolved_agent() -> ResolvedAgentVersion:
    tenant_id, definition_id, version_id = uuid4(), uuid4(), uuid4()
    config = agent_configuration()
    return ResolvedAgentVersion(
        requested_tenant_definition_id=definition_id,
        resolved_definition_id=definition_id,
        version_id=version_id,
        version_number=1,
        tenant_id=tenant_id,
        agent_key="marketer",
        agent_type="marketer",
        provenance=ResolutionProvenance.TENANT,
        configuration_digest=config.configuration_digest,
        configuration=config,
    )


async def create_active_tool(
    factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
    context: PlatformControlContext,
    tool_key: str,
    *,
    config: ToolVersionConfiguration | None = None,
) -> tuple[UUID, UUID]:
    definition = await CreateToolDefinition(factory)(
        context, tool_key=tool_key, category=tool_key.split(".")[0]
    )
    version = await CreateToolVersion(factory)(context, definition.id, config or configuration())
    await ActivateToolVersion(factory)(context, definition.id, version.id)
    return definition.id, version.id


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_version_activation_rollback_lifecycle_and_platform_audit_are_atomic(
    admin_engine: AsyncEngine,
    tool_control_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
    tool_runtime_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
) -> None:
    context = control_context()
    definition = await CreateToolDefinition(tool_control_factory)(
        context, tool_key="demo.social.publish", category="social"
    )
    assert (await GetToolDefinition(tool_runtime_factory)(definition.tool_key)).id == definition.id
    assert [item.id for item in await ListToolDefinitions(tool_runtime_factory)()] == [
        definition.id
    ]
    v1 = await CreateToolVersion(tool_control_factory)(context, definition.id, configuration())
    await ActivateToolVersion(tool_control_factory)(context, definition.id, v1.id)
    v2 = await CreateToolVersion(tool_control_factory)(context, definition.id, configuration("v2"))
    await ActivateToolVersion(tool_control_factory)(context, definition.id, v2.id)
    assert (await ResolveActiveTool(tool_runtime_factory)(definition.tool_key)).version_id == v2.id
    await ActivateToolVersion(tool_control_factory)(context, definition.id, v1.id)
    resolved = await ResolveActiveTool(tool_runtime_factory)(definition.tool_key)
    assert resolved.version_id == v1.id
    assert resolved.risk_level is RiskLevel.R4
    assert resolved.execution_class is ExecutionClass.CONNECTOR
    assert resolved.input_schema.digest == v1.configuration.input_schema.digest
    assert [
        v.version_number for v in await ListToolVersions(tool_runtime_factory)(definition.id)
    ] == [
        1,
        2,
    ]

    disabled = await ChangeToolDefinitionLifecycle(tool_control_factory)(
        context, definition.id, ToolDefinitionStatus.DISABLED
    )
    assert disabled.status is ToolDefinitionStatus.DISABLED
    with pytest.raises(ToolUnavailable):
        await ResolveActiveTool(tool_runtime_factory)(definition.tool_key)
    archived = await ChangeToolDefinitionLifecycle(tool_control_factory)(
        context, definition.id, ToolDefinitionStatus.ARCHIVED
    )
    assert archived.status is ToolDefinitionStatus.ARCHIVED
    with pytest.raises(InvalidToolLifecycleTransition):
        await ChangeToolDefinitionLifecycle(tool_control_factory)(
            context, definition.id, ToolDefinitionStatus.DISABLED
        )
    with pytest.raises(InvalidToolLifecycleTransition):
        await ChangeToolDefinitionLifecycle(tool_control_factory)(
            context, definition.id, ToolDefinitionStatus.ACTIVE
        )
    with pytest.raises(InvalidToolLifecycleTransition):
        await CreateToolVersion(tool_control_factory)(context, definition.id, configuration("v3"))

    async with admin_engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    text(
                        "SELECT action, scope_kind, tenant_id, actor_id, correlation_id, "
                        "tool_name, tool_version, tool_definition_id, tool_version_id, "
                        "after_digest, safe_metadata::text FROM audit.audit_records "
                        "WHERE action LIKE 'tool.%' ORDER BY occurred_at"
                    )
                )
            )
            .tuples()
            .all()
        )
    assert Counter(row[0] for row in rows) == Counter(
        {
            "tool.definition.created": 1,
            "tool.version.created": 2,
            "tool.version.activated": 3,
            "tool.definition.disabled": 1,
            "tool.definition.archived": 1,
        }
    )
    assert all(row[1] == "platform" and row[2] is None for row in rows)
    assert all(row[3] == str(context.actor_id) and row[4] == context.correlation_id for row in rows)
    assert all(row[5] == definition.tool_key and row[7] == definition.id for row in rows)
    version_row = next(row for row in rows if row[0] == "tool.version.created")
    assert version_row[8] is not None and version_row[9].startswith("sha256:")
    serialized = json.dumps(rows, default=str)
    assert "properties" not in serialized
    assert "Normalized test-only" not in serialized


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_resolution_fail_closed_and_activation_ownership(
    admin_engine: AsyncEngine,
    tool_control_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
    tool_runtime_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
) -> None:
    context = control_context()
    missing_activation = await CreateToolDefinition(tool_control_factory)(
        context, tool_key="demo.no_activation", category="demo"
    )
    with pytest.raises(ToolUnavailable):
        await ResolveActiveTool(tool_runtime_factory)(missing_activation.tool_key)
    with pytest.raises(ToolUnavailable):
        await ResolveActiveTool(tool_runtime_factory)("demo.unknown.tool")
    with pytest.raises(ToolDefinitionNotFound):
        await GetToolDefinition(tool_runtime_factory)("demo.unknown.tool")
    with pytest.raises(ToolDefinitionNotFound):
        await ListToolVersions(tool_runtime_factory)(uuid4())

    first = await CreateToolDefinition(tool_control_factory)(
        context, tool_key="demo.first.tool", category="demo"
    )
    second = await CreateToolDefinition(tool_control_factory)(
        context, tool_key="demo.second.tool", category="demo"
    )
    second_version = await CreateToolVersion(tool_control_factory)(
        context, second.id, configuration()
    )
    with pytest.raises(ToolVersionNotFound):
        await ActivateToolVersion(tool_control_factory)(context, first.id, second_version.id)
    await ChangeToolDefinitionLifecycle(tool_control_factory)(
        context, first.id, ToolDefinitionStatus.DISABLED
    )
    with pytest.raises(ToolUnavailable):
        await ActivateToolVersion(tool_control_factory)(context, first.id, second_version.id)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_concurrent_versions_are_monotonic_and_key_is_reserved(
    admin_engine: AsyncEngine,
    tool_control_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
) -> None:
    context = control_context()
    definition = await CreateToolDefinition(tool_control_factory)(
        context, tool_key="demo.concurrent.tool", category="demo"
    )
    first, second = await asyncio.gather(
        CreateToolVersion(tool_control_factory)(context, definition.id, configuration("a")),
        CreateToolVersion(tool_control_factory)(context, definition.id, configuration("b")),
    )
    assert {first.version_number, second.version_number} == {1, 2}
    with pytest.raises(ToolRegistryConflict):
        await CreateToolDefinition(tool_control_factory)(
            context, tool_key=definition.tool_key, category="demo"
        )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_agent_declarations_are_diagnostic_not_authorization(
    admin_engine: AsyncEngine,
    tool_control_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
    tool_runtime_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
) -> None:
    context = control_context()
    await create_active_tool(tool_control_factory, context, "demo.product.read")
    disabled_id, _ = await create_active_tool(tool_control_factory, context, "demo.disabled.read")
    await ChangeToolDefinitionLifecycle(tool_control_factory)(
        context, disabled_id, ToolDefinitionStatus.DISABLED
    )
    await create_active_tool(tool_control_factory, context, "demo.social.publish")
    inspection = await InspectAgentToolDeclarations(tool_runtime_factory)(resolved_agent())
    assert inspection.known_active == ("demo.product.read",)
    assert inspection.known_unavailable == ("demo.disabled.read",)
    assert inspection.unknown == ("demo.missing.tool",)
    assert inspection.denied == ("demo.social.publish",)
    assert set(inspection.states.values()) == {
        "known_active",
        "known_unavailable",
        "unknown",
        "denied",
    }
    assert not hasattr(inspection, "authorization_decision")
    assert not hasattr(inspection, "allow")


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_runtime_has_read_only_registry_privileges(
    admin_engine: AsyncEngine,
    runtime_database_url: str,
    tool_control_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
) -> None:
    context = control_context()
    definition_id, version_id = await create_active_tool(
        tool_control_factory, context, "demo.runtime.read"
    )
    sessions = create_session_factory(runtime_database_url)
    async with sessions.begin() as session:
        assert await session.scalar(select(tool_definitions.c.id)) == definition_id
        assert await session.scalar(select(tool_versions.c.id)) == version_id
        assert await session.scalar(select(tool_activations.c.active_version_id)) == version_id

    statements = (
        insert(tool_definitions).values(
            id=uuid4(),
            tool_key="demo.forged.tool",
            category="demo",
            status="active",
            created_by_actor_kind="system",
            created_by_actor_id=uuid4(),
        ),
        update(tool_definitions)
        .where(tool_definitions.c.id == definition_id)
        .values(status="disabled"),
        tool_definitions.delete().where(tool_definitions.c.id == definition_id),
        update(tool_versions).where(tool_versions.c.id == version_id).values(risk_level="R7"),
        tool_versions.delete().where(tool_versions.c.id == version_id),
        update(tool_activations)
        .where(tool_activations.c.definition_id == definition_id)
        .values(active_version_id=version_id),
        tool_activations.delete().where(tool_activations.c.definition_id == definition_id),
    )
    for statement in statements:
        with pytest.raises(ProgrammingError):
            async with sessions.begin() as session:
                await session.execute(statement)
    with pytest.raises(ProgrammingError):
        async with sessions.begin() as session:
            await session.execute(text("TRUNCATE tool_governance.tool_definitions CASCADE"))

    async with admin_engine.connect() as connection:
        assert await connection.scalar(select(tool_definitions.c.status)) == "active"


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_database_invariants_and_archived_identity_are_defense_in_depth(
    admin_engine: AsyncEngine,
    tool_control_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
) -> None:
    context = control_context()
    definition_id, version_id = await create_active_tool(
        tool_control_factory, context, "demo.database.guard"
    )
    for version_changes in (
        {"risk_level": "R8"},
        {"risk_level": "R-1"},
        {"risk_level": "HIGH"},
        {"input_schema": ["not", "an", "object"]},
        {"description": "Authorization: Bearer database-secret"},
        {"configuration_digest": "not-a-digest"},
    ):
        with pytest.raises(DBAPIError):
            async with admin_engine.begin() as connection:
                await connection.execute(
                    update(tool_versions)
                    .where(tool_versions.c.id == version_id)
                    .values(**version_changes)
                )
    with pytest.raises(DBAPIError):
        async with admin_engine.begin() as connection:
            await connection.execute(
                update(tool_activations)
                .where(tool_activations.c.definition_id == definition_id)
                .values(active_version_id=uuid4())
            )
    await ChangeToolDefinitionLifecycle(tool_control_factory)(
        context, definition_id, ToolDefinitionStatus.ARCHIVED
    )
    for definition_changes in (
        {"status": "active", "updated_at": datetime.now(UTC)},
        {"tool_key": "demo.repurposed.tool", "updated_at": datetime.now(UTC)},
    ):
        with pytest.raises(DBAPIError):
            async with admin_engine.begin() as connection:
                await connection.execute(
                    update(tool_definitions)
                    .where(tool_definitions.c.id == definition_id)
                    .values(**definition_changes)
                )
    async with admin_engine.connect() as connection:
        assert (
            await connection.scalar(
                select(tool_versions.c.risk_level).where(tool_versions.c.id == version_id)
            )
            == "R4"
        )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_audit_failure_rolls_back_tool_mutation(
    admin_engine: AsyncEngine,
    tool_control_factory: SqlAlchemyToolRegistryUnitOfWorkFactory,
) -> None:
    with pytest.raises(DBAPIError):
        await CreateToolDefinition(tool_control_factory)(
            control_context(environment="x" * 33),
            tool_key="demo.must.rollback",
            category="demo",
        )
    async with admin_engine.connect() as connection:
        assert (
            await connection.scalar(
                select(tool_definitions.c.id).where(
                    tool_definitions.c.tool_key == "demo.must.rollback"
                )
            )
            is None
        )
