from dataclasses import dataclass, field, replace
from datetime import datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from creative_marketer.agent_governance.domain import (
    AgentRegistryError,
    ResolvedAgentVersion,
)
from creative_marketer.audit.application import AuditWriter
from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditOutcome, AuditRecord
from creative_marketer.audit.safety import canonical_digest, safe_metadata
from creative_marketer.identity.application.authentication import ActorKind, ExecutionContext
from creative_marketer.identity.application.context import TenantContext
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.permission_governance.domain import (
    Decision,
    DecisionReason,
    InvalidPermissionLifecycleTransition,
    PermissionDecision,
    PermissionEngine,
    PermissionGovernanceConflict,
    PermissionNotFound,
    PermissionStatus,
    PermissionVersionNotFound,
    ResolvedToolPermission,
    ToolPermission,
    ToolPermissionActivation,
    ToolPermissionVersion,
    ToolPermissionVersionConfiguration,
    TrustedScopeRequirements,
)
from creative_marketer.tool_governance.domain import (
    ResolvedToolVersion,
    ToolRegistryError,
)


class PermissionRepository(Protocol):
    async def add(self, permission: ToolPermission) -> None: ...
    async def get(
        self, permission_id: UUID, *, for_update: bool = False
    ) -> ToolPermission | None: ...
    async def get_for_subject(
        self, agent_definition_id: UUID, tool_definition_id: UUID
    ) -> ToolPermission | None: ...
    async def set_status(
        self, permission_id: UUID, status: PermissionStatus, updated_at: datetime
    ) -> None: ...


class PermissionVersionRepository(Protocol):
    async def add(self, version: ToolPermissionVersion) -> None: ...
    async def get(self, version_id: UUID) -> ToolPermissionVersion | None: ...
    async def next_version_number(self, permission_id: UUID) -> int: ...


class PermissionActivationRepository(Protocol):
    async def get(self, permission_id: UUID) -> ToolPermissionActivation | None: ...
    async def set(self, activation: ToolPermissionActivation) -> None: ...


class PermissionUnitOfWork(Protocol):
    permissions: PermissionRepository
    versions: PermissionVersionRepository
    activations: PermissionActivationRepository
    audit: AuditWriter

    async def __aenter__(self) -> "PermissionUnitOfWork": ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class PermissionUnitOfWorkFactory(Protocol):
    def __call__(self, context: TenantContext) -> PermissionUnitOfWork: ...


class AgentResolver(Protocol):
    async def __call__(
        self, context: ExecutionContext, definition_id: UUID
    ) -> ResolvedAgentVersion: ...


class ToolResolver(Protocol):
    async def __call__(self, tool_key: str) -> ResolvedToolVersion: ...


def _require_policy_admin(context: ExecutionContext) -> None:
    if (
        context.actor.kind is not ActorKind.USER
        or context.actor.id != context.user_id
        or context.membership_status is not MembershipStatus.ACTIVE
        or context.membership_role not in {MembershipRole.OWNER, MembershipRole.ADMIN}
    ):
        raise PermissionGovernanceConflict(
            "permission policy mutations require an active tenant owner or admin user"
        )


def _owned(permission: ToolPermission | None, context: ExecutionContext) -> ToolPermission:
    if permission is None or permission.tenant_id != context.tenant_id:
        raise PermissionNotFound("tenant tool permission was not found")
    return permission


@dataclass(slots=True)
class CreateToolPermission:
    uow_factory: PermissionUnitOfWorkFactory

    async def __call__(
        self, context: ExecutionContext, agent_definition_id: UUID, tool_definition_id: UUID
    ) -> ToolPermission:
        _require_policy_admin(context)
        permission = ToolPermission(
            tenant_id=context.tenant_id,
            agent_definition_id=agent_definition_id,
            tool_definition_id=tool_definition_id,
            created_by_actor_kind=context.actor.kind.value,
            created_by_actor_id=context.actor.id,
        )
        async with self.uow_factory(context.tenant_context()) as uow:
            await uow.permissions.add(permission)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="governance.permission.created",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="tool_permission",
                    resource_id=str(permission.id),
                    agent_definition_id=agent_definition_id,
                    tool_definition_id=tool_definition_id,
                    permission_id=permission.id,
                )
            )
            await uow.commit()
        return permission


@dataclass(slots=True)
class CreateToolPermissionVersion:
    uow_factory: PermissionUnitOfWorkFactory

    async def __call__(
        self,
        context: ExecutionContext,
        permission_id: UUID,
        configuration: ToolPermissionVersionConfiguration,
    ) -> ToolPermissionVersion:
        _require_policy_admin(context)
        async with self.uow_factory(context.tenant_context()) as uow:
            permission = _owned(await uow.permissions.get(permission_id, for_update=True), context)
            if permission.status is PermissionStatus.ARCHIVED:
                raise InvalidPermissionLifecycleTransition(
                    "archived permissions cannot receive versions"
                )
            version = ToolPermissionVersion(
                permission_id=permission.id,
                tenant_id=context.tenant_id,
                version_number=await uow.versions.next_version_number(permission.id),
                configuration=configuration,
                created_by_actor_kind=context.actor.kind.value,
                created_by_actor_id=context.actor.id,
            )
            await uow.versions.add(version)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="governance.permission.version.created",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="tool_permission_version",
                    resource_id=str(version.id),
                    agent_definition_id=permission.agent_definition_id,
                    tool_definition_id=permission.tool_definition_id,
                    permission_id=permission.id,
                    permission_version_id=version.id,
                    after_digest=version.configuration_digest,
                    metadata=safe_metadata({"version_number": version.version_number}),
                )
            )
            await uow.commit()
            return version


@dataclass(slots=True)
class ActivateToolPermissionVersion:
    uow_factory: PermissionUnitOfWorkFactory

    async def __call__(
        self, context: ExecutionContext, permission_id: UUID, version_id: UUID
    ) -> ToolPermissionActivation:
        _require_policy_admin(context)
        async with self.uow_factory(context.tenant_context()) as uow:
            permission = _owned(await uow.permissions.get(permission_id, for_update=True), context)
            if permission.status is not PermissionStatus.ACTIVE:
                raise InvalidPermissionLifecycleTransition("only active permissions can activate")
            version = await uow.versions.get(version_id)
            if version is None or version.permission_id != permission.id:
                raise PermissionVersionNotFound("version does not belong to permission")
            previous = await uow.activations.get(permission.id)
            activation = ToolPermissionActivation(
                permission_id=permission.id,
                tenant_id=context.tenant_id,
                active_version_id=version.id,
                activated_by_actor_kind=context.actor.kind.value,
                activated_by_actor_id=context.actor.id,
            )
            await uow.activations.set(activation)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="governance.permission.version.activated",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="tool_permission_version",
                    resource_id=str(version.id),
                    agent_definition_id=permission.agent_definition_id,
                    tool_definition_id=permission.tool_definition_id,
                    permission_id=permission.id,
                    permission_version_id=version.id,
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
class ChangeToolPermissionLifecycle:
    uow_factory: PermissionUnitOfWorkFactory

    async def __call__(
        self, context: ExecutionContext, permission_id: UUID, target: PermissionStatus
    ) -> ToolPermission:
        _require_policy_admin(context)
        if target is PermissionStatus.ACTIVE:
            raise InvalidPermissionLifecycleTransition("permission resurrection is not implemented")
        async with self.uow_factory(context.tenant_context()) as uow:
            permission = _owned(await uow.permissions.get(permission_id, for_update=True), context)
            allowed = {
                PermissionStatus.ACTIVE: {PermissionStatus.DISABLED, PermissionStatus.ARCHIVED},
                PermissionStatus.DISABLED: {PermissionStatus.ARCHIVED},
                PermissionStatus.ARCHIVED: set(),
            }
            if target not in allowed[permission.status]:
                raise InvalidPermissionLifecycleTransition(
                    "invalid permission lifecycle transition"
                )
            changed_at = datetime.now(permission.updated_at.tzinfo)
            await uow.permissions.set_status(permission.id, target, changed_at)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action=f"governance.permission.{target.value}",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="tool_permission",
                    resource_id=str(permission.id),
                    agent_definition_id=permission.agent_definition_id,
                    tool_definition_id=permission.tool_definition_id,
                    permission_id=permission.id,
                    before_digest=canonical_digest({"status": permission.status.value}),
                    after_digest=canonical_digest({"status": target.value}),
                )
            )
            await uow.commit()
            return replace(permission, status=target, updated_at=changed_at)


async def _resolve_policy(
    uow: PermissionUnitOfWork, agent_id: UUID, tool_id: UUID
) -> tuple[ResolvedToolPermission | None, DecisionReason | None]:
    permission = await uow.permissions.get_for_subject(agent_id, tool_id)
    if permission is None:
        return None, DecisionReason.PERMISSION_MISSING
    if permission.status is not PermissionStatus.ACTIVE:
        return None, DecisionReason.PERMISSION_DISABLED
    activation = await uow.activations.get(permission.id)
    if activation is None:
        return None, DecisionReason.PERMISSION_MISSING
    version = await uow.versions.get(activation.active_version_id)
    if version is None or version.permission_id != permission.id:
        return None, DecisionReason.SECURITY_CONTEXT_INVALID
    return (
        ResolvedToolPermission(
            permission_id=permission.id,
            permission_version_id=version.id,
            version_number=version.version_number,
            tenant_id=permission.tenant_id,
            agent_definition_id=permission.agent_definition_id,
            tool_definition_id=permission.tool_definition_id,
            configuration_digest=version.configuration_digest,
            configuration=version.configuration,
        ),
        None,
    )


@dataclass(slots=True)
class EvaluateToolPermission:
    uow_factory: PermissionUnitOfWorkFactory
    agent_resolver: AgentResolver
    tool_resolver: ToolResolver
    engine: PermissionEngine = field(default_factory=PermissionEngine)

    async def __call__(
        self,
        context: ExecutionContext,
        requested_agent_definition_id: UUID,
        tool_key: str,
        scopes: TrustedScopeRequirements,
    ) -> PermissionDecision:
        agent: ResolvedAgentVersion | None = None
        tool: ResolvedToolVersion | None = None
        policy: ResolvedToolPermission | None = None
        reason: DecisionReason | None = None
        try:
            agent = await self.agent_resolver(context, requested_agent_definition_id)
        except AgentRegistryError:
            reason = DecisionReason.AGENT_UNAVAILABLE
        if reason is None:
            try:
                tool = await self.tool_resolver(tool_key)
            except (ToolRegistryError, ValueError):
                reason = DecisionReason.TOOL_UNAVAILABLE
        if (
            reason is None
            and agent is not None
            and tool is not None
            and (
                agent.requested_tenant_definition_id != requested_agent_definition_id
                or tool.tool_key != tool_key
            )
        ):
            reason = DecisionReason.SECURITY_CONTEXT_INVALID
        async with self.uow_factory(context.tenant_context()) as uow:
            if reason is None and agent is not None and tool is not None:
                policy, reason = await _resolve_policy(
                    uow, requested_agent_definition_id, tool.definition_id
                )
            if reason is None and agent is not None and tool is not None and policy is not None:
                decision = self.engine.evaluate(context, agent, tool, policy, scopes)
            else:
                decision = PermissionDecision(
                    decision=Decision.DENY,
                    reason_code=reason or DecisionReason.SECURITY_CONTEXT_INVALID,
                    tenant_id=context.tenant_id,
                    actor_kind=context.actor.kind.value,
                    actor_id=context.actor.id,
                    requested_agent_definition_id=requested_agent_definition_id,
                    resolved_agent_definition_id=None
                    if agent is None
                    else agent.resolved_definition_id,
                    agent_version_id=None if agent is None else agent.version_id,
                    agent_configuration_digest=None
                    if agent is None
                    else agent.configuration_digest,
                    tool_definition_id=None if tool is None else tool.definition_id,
                    tool_version_id=None if tool is None else tool.version_id,
                    tool_configuration_digest=None if tool is None else tool.configuration_digest,
                    tool_key=tool_key,
                    risk_level=None if tool is None else tool.risk_level,
                    permission_id=None,
                    permission_version_id=None,
                    permission_configuration_digest=None,
                    requested_scope_digest=scopes.digest,
                    obligations=(),
                    correlation_id=context.correlation_id,
                )
            try:
                await uow.audit.append(_decision_audit(context, decision))
                await uow.commit()
            except Exception:
                if decision.decision is Decision.DENY:
                    return decision
                return replace(
                    decision,
                    decision=Decision.DENY,
                    reason_code=DecisionReason.AUDIT_FAILURE,
                    obligations=(),
                )
            return decision


def _decision_audit(context: ExecutionContext, decision: PermissionDecision) -> AuditRecord:
    action = {
        Decision.ALLOW: "governance.permission.allowed",
        Decision.DENY: "governance.permission.denied",
        Decision.REQUIRES_APPROVAL: "governance.permission.requires_approval",
    }[decision.decision]
    return tenant_audit(
        context,
        action=action,
        outcome=AuditOutcome.DENIED if decision.decision is Decision.DENY else AuditOutcome.SUCCESS,
        reason_code=decision.reason_code.value,
        resource_type="tool_permission_decision",
        resource_id=str(decision.decision_id),
        agent_definition_id=decision.requested_agent_definition_id,
        agent_version_id=decision.agent_version_id,
        tool_definition_id=decision.tool_definition_id,
        tool_version_id=decision.tool_version_id,
        permission_id=decision.permission_id,
        permission_version_id=decision.permission_version_id,
        metadata=safe_metadata(
            {
                "policy_engine_version": decision.policy_engine_version,
                "requested_scope_digest": decision.requested_scope_digest,
                "obligations": [item.value for item in decision.obligations],
            }
        ),
    )
