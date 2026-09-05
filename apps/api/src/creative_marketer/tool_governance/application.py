from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from creative_marketer.agent_governance.domain import ResolvedAgentVersion
from creative_marketer.audit.application import AuditWriter
from creative_marketer.audit.builders import platform_audit
from creative_marketer.audit.domain import AuditActorKind, AuditOutcome
from creative_marketer.audit.safety import canonical_digest, safe_metadata
from creative_marketer.identity.application.authentication import ActorKind
from creative_marketer.tool_governance.domain import (
    AgentToolDeclarationInspection,
    InvalidToolLifecycleTransition,
    ResolvedToolVersion,
    ToolActivation,
    ToolDefinition,
    ToolDefinitionNotFound,
    ToolDefinitionStatus,
    ToolRegistryConflict,
    ToolUnavailable,
    ToolVersion,
    ToolVersionConfiguration,
    ToolVersionNotFound,
    utc_now,
)
from creative_marketer.tool_governance.schema_validation import validate_contract_schema


@dataclass(frozen=True, slots=True)
class PlatformControlContext:
    actor_kind: ActorKind
    actor_id: UUID
    environment: str
    correlation_id: UUID

    def __post_init__(self) -> None:
        if self.actor_kind not in {ActorKind.SYSTEM, ActorKind.WORKLOAD}:
            raise ToolRegistryConflict("tool registry mutations require trusted platform authority")
        if not self.environment:
            raise ValueError("environment must be non-blank")


class ToolDefinitionRepository(Protocol):
    async def add(self, definition: ToolDefinition) -> None: ...
    async def get_by_id(
        self, definition_id: UUID, *, for_update: bool = False
    ) -> ToolDefinition | None: ...
    async def get_by_key(self, tool_key: str) -> ToolDefinition | None: ...
    async def list(self) -> list[ToolDefinition]: ...
    async def set_status(
        self, definition_id: UUID, status: ToolDefinitionStatus, updated_at: datetime
    ) -> None: ...


class ToolVersionRepository(Protocol):
    async def add(self, version: ToolVersion) -> None: ...
    async def get(self, version_id: UUID) -> ToolVersion | None: ...
    async def list_for_definition(self, definition_id: UUID) -> list[ToolVersion]: ...
    async def next_version_number(self, definition_id: UUID) -> int: ...


class ToolActivationRepository(Protocol):
    async def get(self, definition_id: UUID) -> ToolActivation | None: ...
    async def set(self, activation: ToolActivation) -> None: ...


class ToolRegistryUnitOfWork(Protocol):
    definitions: ToolDefinitionRepository
    versions: ToolVersionRepository
    activations: ToolActivationRepository
    audit: AuditWriter

    async def __aenter__(self) -> "ToolRegistryUnitOfWork": ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class ToolRegistryUnitOfWorkFactory(Protocol):
    def __call__(self) -> ToolRegistryUnitOfWork: ...


def _audit_actor(context: PlatformControlContext) -> AuditActorKind:
    return AuditActorKind(context.actor_kind.value)


def _definition(value: ToolDefinition | None) -> ToolDefinition:
    if value is None:
        raise ToolDefinitionNotFound("tool definition was not found")
    return value


@dataclass(slots=True)
class CreateToolDefinition:
    uow_factory: ToolRegistryUnitOfWorkFactory

    async def __call__(
        self, context: PlatformControlContext, *, tool_key: str, category: str
    ) -> ToolDefinition:
        definition = ToolDefinition(
            tool_key=tool_key,
            category=category,
            created_by_actor_kind=context.actor_kind.value,
            created_by_actor_id=context.actor_id,
        )
        async with self.uow_factory() as uow:
            await uow.definitions.add(definition)
            await uow.audit.append(
                platform_audit(
                    actor_kind=_audit_actor(context),
                    actor_id=str(context.actor_id),
                    action="tool.definition.created",
                    outcome=AuditOutcome.SUCCESS,
                    correlation_id=context.correlation_id,
                    environment=context.environment,
                    resource_type="tool_definition",
                    resource_id=str(definition.id),
                    tool_name=definition.tool_key,
                    tool_definition_id=definition.id,
                    metadata=safe_metadata({"category": definition.category}),
                )
            )
            await uow.commit()
        return definition


@dataclass(slots=True)
class CreateToolVersion:
    uow_factory: ToolRegistryUnitOfWorkFactory

    async def __call__(
        self,
        context: PlatformControlContext,
        definition_id: UUID,
        configuration: ToolVersionConfiguration,
    ) -> ToolVersion:
        validated_input = validate_contract_schema(configuration.input_schema.primitive())
        validated_output = validate_contract_schema(configuration.output_schema.primitive())
        if (
            validated_input != configuration.input_schema
            or validated_output != configuration.output_schema
        ):
            raise ToolRegistryConflict("tool schema snapshots failed canonical validation")
        async with self.uow_factory() as uow:
            definition = _definition(
                await uow.definitions.get_by_id(definition_id, for_update=True)
            )
            if definition.status is ToolDefinitionStatus.ARCHIVED:
                raise InvalidToolLifecycleTransition("archived tools cannot receive versions")
            version = ToolVersion(
                definition_id=definition.id,
                version_number=await uow.versions.next_version_number(definition.id),
                configuration=configuration,
                created_by_actor_kind=context.actor_kind.value,
                created_by_actor_id=context.actor_id,
            )
            await uow.versions.add(version)
            await uow.audit.append(
                platform_audit(
                    actor_kind=_audit_actor(context),
                    actor_id=str(context.actor_id),
                    action="tool.version.created",
                    outcome=AuditOutcome.SUCCESS,
                    correlation_id=context.correlation_id,
                    environment=context.environment,
                    resource_type="tool_version",
                    resource_id=str(version.id),
                    tool_name=definition.tool_key,
                    tool_version=str(version.version_number),
                    tool_definition_id=definition.id,
                    tool_version_id=version.id,
                    after_digest=version.configuration_digest,
                    metadata=safe_metadata(
                        {
                            "risk_level": configuration.risk_level.value,
                            "input_schema_digest": configuration.input_schema.digest,
                            "output_schema_digest": configuration.output_schema.digest,
                        }
                    ),
                )
            )
            await uow.commit()
        return version


@dataclass(slots=True)
class ActivateToolVersion:
    uow_factory: ToolRegistryUnitOfWorkFactory

    async def __call__(
        self, context: PlatformControlContext, definition_id: UUID, version_id: UUID
    ) -> ToolActivation:
        async with self.uow_factory() as uow:
            definition = _definition(
                await uow.definitions.get_by_id(definition_id, for_update=True)
            )
            if definition.status is not ToolDefinitionStatus.ACTIVE:
                raise ToolUnavailable("only active tool definitions can be activated")
            version = await uow.versions.get(version_id)
            if version is None or version.definition_id != definition.id:
                raise ToolVersionNotFound("version does not belong to the tool definition")
            previous = await uow.activations.get(definition.id)
            activation = ToolActivation(
                definition_id=definition.id,
                active_version_id=version.id,
                activated_by_actor_kind=context.actor_kind.value,
                activated_by_actor_id=context.actor_id,
            )
            await uow.activations.set(activation)
            await uow.audit.append(
                platform_audit(
                    actor_kind=_audit_actor(context),
                    actor_id=str(context.actor_id),
                    action="tool.version.activated",
                    outcome=AuditOutcome.SUCCESS,
                    correlation_id=context.correlation_id,
                    environment=context.environment,
                    resource_type="tool_version",
                    resource_id=str(version.id),
                    tool_name=definition.tool_key,
                    tool_version=str(version.version_number),
                    tool_definition_id=definition.id,
                    tool_version_id=version.id,
                    before_digest=canonical_digest(
                        {
                            "active_version_id": None
                            if previous is None
                            else str(previous.active_version_id)
                        }
                    ),
                    after_digest=canonical_digest({"active_version_id": str(version.id)}),
                )
            )
            await uow.commit()
        return activation


@dataclass(slots=True)
class ChangeToolDefinitionLifecycle:
    uow_factory: ToolRegistryUnitOfWorkFactory

    async def __call__(
        self,
        context: PlatformControlContext,
        definition_id: UUID,
        target: ToolDefinitionStatus,
    ) -> ToolDefinition:
        if target is ToolDefinitionStatus.ACTIVE:
            raise InvalidToolLifecycleTransition("enable/resurrection is not implemented")
        async with self.uow_factory() as uow:
            definition = _definition(
                await uow.definitions.get_by_id(definition_id, for_update=True)
            )
            allowed = {
                ToolDefinitionStatus.ACTIVE: {
                    ToolDefinitionStatus.DISABLED,
                    ToolDefinitionStatus.ARCHIVED,
                },
                ToolDefinitionStatus.DISABLED: {ToolDefinitionStatus.ARCHIVED},
                ToolDefinitionStatus.ARCHIVED: set(),
            }
            if target not in allowed[definition.status]:
                raise InvalidToolLifecycleTransition("tool lifecycle transition is invalid")
            changed_at = utc_now()
            await uow.definitions.set_status(definition.id, target, changed_at)
            action = (
                "tool.definition.disabled"
                if target is ToolDefinitionStatus.DISABLED
                else "tool.definition.archived"
            )
            await uow.audit.append(
                platform_audit(
                    actor_kind=_audit_actor(context),
                    actor_id=str(context.actor_id),
                    action=action,
                    outcome=AuditOutcome.SUCCESS,
                    correlation_id=context.correlation_id,
                    environment=context.environment,
                    resource_type="tool_definition",
                    resource_id=str(definition.id),
                    tool_name=definition.tool_key,
                    tool_definition_id=definition.id,
                    before_digest=canonical_digest({"status": definition.status.value}),
                    after_digest=canonical_digest({"status": target.value}),
                )
            )
            await uow.commit()
            return ToolDefinition(
                id=definition.id,
                tool_key=definition.tool_key,
                category=definition.category,
                status=target,
                created_by_actor_kind=definition.created_by_actor_kind,
                created_by_actor_id=definition.created_by_actor_id,
                created_at=definition.created_at,
                updated_at=changed_at,
            )


@dataclass(slots=True)
class GetToolDefinition:
    uow_factory: ToolRegistryUnitOfWorkFactory

    async def __call__(self, tool_key: str) -> ToolDefinition:
        async with self.uow_factory() as uow:
            return _definition(await uow.definitions.get_by_key(tool_key))


@dataclass(slots=True)
class ListToolDefinitions:
    uow_factory: ToolRegistryUnitOfWorkFactory

    async def __call__(self) -> list[ToolDefinition]:
        async with self.uow_factory() as uow:
            return await uow.definitions.list()


@dataclass(slots=True)
class ListToolVersions:
    uow_factory: ToolRegistryUnitOfWorkFactory

    async def __call__(self, definition_id: UUID) -> list[ToolVersion]:
        async with self.uow_factory() as uow:
            _definition(await uow.definitions.get_by_id(definition_id))
            return await uow.versions.list_for_definition(definition_id)


async def _resolve(uow: ToolRegistryUnitOfWork, tool_key: str) -> ResolvedToolVersion:
    definition = await uow.definitions.get_by_key(tool_key)
    if definition is None or definition.status is not ToolDefinitionStatus.ACTIVE:
        raise ToolUnavailable("tool is unavailable")
    activation = await uow.activations.get(definition.id)
    if activation is None:
        raise ToolUnavailable("tool has no active version")
    version = await uow.versions.get(activation.active_version_id)
    if version is None or version.definition_id != definition.id:
        raise ToolUnavailable("active tool version is unavailable")
    config = version.configuration
    return ResolvedToolVersion(
        definition_id=definition.id,
        version_id=version.id,
        version_number=version.version_number,
        tool_key=definition.tool_key,
        risk_level=config.risk_level,
        side_effect_class=config.side_effect_class,
        execution_class=config.execution_class,
        credential_boundary=config.credential_boundary,
        idempotency_requirement=config.idempotency_requirement,
        input_schema=config.input_schema,
        output_schema=config.output_schema,
        capability_tags=config.capability_tags,
        configuration_digest=version.configuration_digest,
    )


@dataclass(slots=True)
class ResolveActiveTool:
    uow_factory: ToolRegistryUnitOfWorkFactory

    async def __call__(self, tool_key: str) -> ResolvedToolVersion:
        async with self.uow_factory() as uow:
            return await _resolve(uow, tool_key)


@dataclass(slots=True)
class InspectAgentToolDeclarations:
    uow_factory: ToolRegistryUnitOfWorkFactory

    async def __call__(self, agent: ResolvedAgentVersion) -> AgentToolDeclarationInspection:
        active: list[str] = []
        unavailable: list[str] = []
        unknown: list[str] = []
        async with self.uow_factory() as uow:
            for tool_key in agent.configuration.allowed_tool_keys:
                definition = await uow.definitions.get_by_key(tool_key)
                if definition is None:
                    unknown.append(tool_key)
                    continue
                try:
                    await _resolve(uow, tool_key)
                except ToolUnavailable:
                    unavailable.append(tool_key)
                else:
                    active.append(tool_key)
        return AgentToolDeclarationInspection(
            known_active=tuple(active),
            known_unavailable=tuple(unavailable),
            unknown=tuple(unknown),
            denied=agent.configuration.denied_tool_keys,
        )
