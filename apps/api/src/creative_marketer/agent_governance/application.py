from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from creative_marketer.agent_governance.domain import (
    AgentActivation,
    AgentDefinition,
    AgentDefinitionNotFound,
    AgentDefinitionStatus,
    AgentRegistryConflict,
    AgentScopeKind,
    AgentUnavailable,
    AgentVersion,
    AgentVersionConfiguration,
    AgentVersionNotFound,
    InvalidLifecycleTransition,
    ResolutionProvenance,
    ResolvedAgentVersion,
    utc_now,
)
from creative_marketer.audit.application import AuditWriter
from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditOutcome
from creative_marketer.audit.safety import canonical_digest, safe_metadata
from creative_marketer.identity.application.authentication import ActorKind, ExecutionContext
from creative_marketer.identity.application.context import TenantContext


class AgentDefinitionRepository(Protocol):
    async def add(self, definition: AgentDefinition) -> None: ...
    async def get(
        self, definition_id: UUID, *, for_update: bool = False
    ) -> AgentDefinition | None: ...
    async def list_tenant(self) -> list[AgentDefinition]: ...
    async def set_status(
        self, definition_id: UUID, status: AgentDefinitionStatus, updated_at: datetime
    ) -> None: ...


class AgentVersionRepository(Protocol):
    async def add(self, version: AgentVersion) -> None: ...
    async def get(self, version_id: UUID) -> AgentVersion | None: ...
    async def list_for_definition(self, definition_id: UUID) -> list[AgentVersion]: ...
    async def next_version_number(self, definition_id: UUID) -> int: ...


class AgentActivationRepository(Protocol):
    async def get(self, definition_id: UUID) -> AgentActivation | None: ...
    async def set(self, activation: AgentActivation) -> None: ...


class AgentRegistryUnitOfWork(Protocol):
    definitions: AgentDefinitionRepository
    versions: AgentVersionRepository
    activations: AgentActivationRepository
    audit: AuditWriter

    async def __aenter__(self) -> "AgentRegistryUnitOfWork": ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class AgentRegistryUnitOfWorkFactory(Protocol):
    def __call__(self, context: TenantContext) -> AgentRegistryUnitOfWork: ...


def _require_user_context(context: ExecutionContext) -> None:
    if context.actor.kind is not ActorKind.USER or context.actor.id != context.user_id:
        raise AgentRegistryConflict("tenant registry mutations require the trusted user actor")


def _owned_definition(
    definition: AgentDefinition | None, context: ExecutionContext
) -> AgentDefinition:
    if (
        definition is None
        or definition.scope_kind is not AgentScopeKind.TENANT
        or definition.tenant_id != context.tenant_id
    ):
        raise AgentDefinitionNotFound("tenant agent definition was not found")
    return definition


@dataclass(slots=True)
class CreateTenantAgentDefinition:
    uow_factory: AgentRegistryUnitOfWorkFactory

    async def __call__(
        self,
        context: ExecutionContext,
        *,
        agent_key: str,
        agent_type: str,
        platform_template_id: UUID | None = None,
    ) -> AgentDefinition:
        _require_user_context(context)
        definition = AgentDefinition(
            scope_kind=AgentScopeKind.TENANT,
            tenant_id=context.tenant_id,
            platform_template_id=platform_template_id,
            agent_key=agent_key,
            agent_type=agent_type,
            created_by_actor_kind=context.actor.kind.value,
            created_by_actor_id=context.actor.id,
        )
        async with self.uow_factory(context.tenant_context()) as uow:
            await uow.definitions.add(definition)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="agent.definition.created",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="agent_definition",
                    resource_id=str(definition.id),
                    agent_definition_id=definition.id,
                    metadata=safe_metadata(
                        {"agent_key": definition.agent_key, "agent_type": definition.agent_type}
                    ),
                )
            )
            if platform_template_id is not None:
                await uow.audit.append(
                    tenant_audit(
                        context,
                        action="agent.template.linked",
                        outcome=AuditOutcome.SUCCESS,
                        resource_type="agent_definition",
                        resource_id=str(definition.id),
                        agent_definition_id=definition.id,
                        metadata=safe_metadata({"platform_template_id": str(platform_template_id)}),
                    )
                )
            await uow.commit()
        return definition


@dataclass(slots=True)
class CreateAgentVersion:
    uow_factory: AgentRegistryUnitOfWorkFactory

    async def __call__(
        self,
        context: ExecutionContext,
        definition_id: UUID,
        configuration: AgentVersionConfiguration,
    ) -> AgentVersion:
        _require_user_context(context)
        async with self.uow_factory(context.tenant_context()) as uow:
            definition = _owned_definition(
                await uow.definitions.get(definition_id, for_update=True), context
            )
            if definition.status is AgentDefinitionStatus.ARCHIVED:
                raise InvalidLifecycleTransition("archived definitions cannot receive versions")
            version = AgentVersion(
                definition_id=definition.id,
                scope_kind=definition.scope_kind,
                tenant_id=definition.tenant_id,
                version_number=await uow.versions.next_version_number(definition.id),
                configuration=configuration,
                created_by_actor_kind=context.actor.kind.value,
                created_by_actor_id=context.actor.id,
            )
            await uow.versions.add(version)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="agent.version.created",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="agent_version",
                    resource_id=str(version.id),
                    agent_definition_id=definition.id,
                    agent_version_id=version.id,
                    after_digest=version.configuration_digest,
                    metadata=safe_metadata({"version_number": version.version_number}),
                )
            )
            await uow.commit()
            return version


@dataclass(slots=True)
class ActivateAgentVersion:
    uow_factory: AgentRegistryUnitOfWorkFactory

    async def __call__(
        self, context: ExecutionContext, definition_id: UUID, version_id: UUID
    ) -> AgentActivation:
        _require_user_context(context)
        async with self.uow_factory(context.tenant_context()) as uow:
            definition = _owned_definition(
                await uow.definitions.get(definition_id, for_update=True), context
            )
            if definition.status is not AgentDefinitionStatus.ACTIVE:
                raise AgentUnavailable("only active definitions can be activated")
            version = await uow.versions.get(version_id)
            if version is None or version.definition_id != definition.id:
                raise AgentVersionNotFound("version does not belong to the definition")
            previous = await uow.activations.get(definition.id)
            activation = AgentActivation(
                definition_id=definition.id,
                active_version_id=version.id,
                scope_kind=definition.scope_kind,
                tenant_id=definition.tenant_id,
                activated_by_actor_kind=context.actor.kind.value,
                activated_by_actor_id=context.actor.id,
            )
            await uow.activations.set(activation)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="agent.version.activated",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="agent_version",
                    resource_id=str(version.id),
                    agent_definition_id=definition.id,
                    agent_version_id=version.id,
                    before_digest=canonical_digest(
                        {
                            "active_version_id": (
                                None if previous is None else str(previous.active_version_id)
                            )
                        }
                    ),
                    after_digest=canonical_digest({"active_version_id": str(version.id)}),
                    metadata=safe_metadata({"version_number": version.version_number}),
                )
            )
            await uow.commit()
            return activation


@dataclass(slots=True)
class ChangeAgentDefinitionLifecycle:
    uow_factory: AgentRegistryUnitOfWorkFactory

    async def __call__(
        self,
        context: ExecutionContext,
        definition_id: UUID,
        target: AgentDefinitionStatus,
    ) -> AgentDefinition:
        _require_user_context(context)
        if target is AgentDefinitionStatus.ACTIVE:
            raise InvalidLifecycleTransition("enable/resurrection is not implemented")
        async with self.uow_factory(context.tenant_context()) as uow:
            definition = _owned_definition(
                await uow.definitions.get(definition_id, for_update=True), context
            )
            allowed = {
                AgentDefinitionStatus.ACTIVE: {
                    AgentDefinitionStatus.DISABLED,
                    AgentDefinitionStatus.ARCHIVED,
                },
                AgentDefinitionStatus.DISABLED: {AgentDefinitionStatus.ARCHIVED},
                AgentDefinitionStatus.ARCHIVED: set(),
            }
            if target not in allowed[definition.status]:
                raise InvalidLifecycleTransition("agent definition lifecycle transition is invalid")
            changed_at = utc_now()
            await uow.definitions.set_status(definition.id, target, changed_at)
            action = (
                "agent.definition.disabled"
                if target is AgentDefinitionStatus.DISABLED
                else "agent.definition.archived"
            )
            await uow.audit.append(
                tenant_audit(
                    context,
                    action=action,
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="agent_definition",
                    resource_id=str(definition.id),
                    agent_definition_id=definition.id,
                    before_digest=canonical_digest({"status": definition.status.value}),
                    after_digest=canonical_digest({"status": target.value}),
                )
            )
            await uow.commit()
            return AgentDefinition(
                id=definition.id,
                scope_kind=definition.scope_kind,
                tenant_id=definition.tenant_id,
                platform_template_id=definition.platform_template_id,
                agent_key=definition.agent_key,
                agent_type=definition.agent_type,
                status=target,
                created_by_actor_kind=definition.created_by_actor_kind,
                created_by_actor_id=definition.created_by_actor_id,
                created_at=definition.created_at,
                updated_at=changed_at,
            )


@dataclass(slots=True)
class GetAgentDefinition:
    uow_factory: AgentRegistryUnitOfWorkFactory

    async def __call__(self, context: ExecutionContext, definition_id: UUID) -> AgentDefinition:
        async with self.uow_factory(context.tenant_context()) as uow:
            return _owned_definition(await uow.definitions.get(definition_id), context)


@dataclass(slots=True)
class ListTenantAgentDefinitions:
    uow_factory: AgentRegistryUnitOfWorkFactory

    async def __call__(self, context: ExecutionContext) -> list[AgentDefinition]:
        async with self.uow_factory(context.tenant_context()) as uow:
            return await uow.definitions.list_tenant()


@dataclass(slots=True)
class ListAgentVersions:
    uow_factory: AgentRegistryUnitOfWorkFactory

    async def __call__(self, context: ExecutionContext, definition_id: UUID) -> list[AgentVersion]:
        async with self.uow_factory(context.tenant_context()) as uow:
            _owned_definition(await uow.definitions.get(definition_id), context)
            return await uow.versions.list_for_definition(definition_id)


@dataclass(slots=True)
class ResolveActiveAgentVersion:
    uow_factory: AgentRegistryUnitOfWorkFactory

    async def __call__(
        self, context: ExecutionContext, definition_id: UUID
    ) -> ResolvedAgentVersion:
        async with self.uow_factory(context.tenant_context()) as uow:
            requested = _owned_definition(await uow.definitions.get(definition_id), context)
            if requested.status is not AgentDefinitionStatus.ACTIVE:
                raise AgentUnavailable("agent definition is unavailable")
            activation = await uow.activations.get(requested.id)
            resolved_definition = requested
            provenance = ResolutionProvenance.TENANT
            if activation is None:
                if requested.platform_template_id is None:
                    raise AgentUnavailable("agent has no active version or explicit template")
                template = await uow.definitions.get(requested.platform_template_id)
                if (
                    template is None
                    or template.scope_kind is not AgentScopeKind.PLATFORM
                    or template.status is not AgentDefinitionStatus.ACTIVE
                ):
                    raise AgentUnavailable("explicit platform template is unavailable")
                activation = await uow.activations.get(template.id)
                if activation is None:
                    raise AgentUnavailable("explicit platform template has no active version")
                resolved_definition = template
                provenance = ResolutionProvenance.PLATFORM_TEMPLATE
            version = await uow.versions.get(activation.active_version_id)
            if version is None or version.definition_id != resolved_definition.id:
                raise AgentUnavailable("active agent version is unavailable")
            return ResolvedAgentVersion(
                requested_tenant_definition_id=requested.id,
                resolved_definition_id=resolved_definition.id,
                version_id=version.id,
                version_number=version.version_number,
                tenant_id=context.tenant_id,
                agent_key=requested.agent_key,
                agent_type=requested.agent_type,
                provenance=provenance,
                configuration_digest=version.configuration_digest,
                configuration=version.configuration,
            )
