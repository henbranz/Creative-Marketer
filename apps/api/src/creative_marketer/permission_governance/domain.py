import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from creative_marketer.agent_governance.domain import ResolvedAgentVersion
from creative_marketer.governance_keys import canonical_scope_key, canonical_scope_keys
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.tool_governance.domain import (
    CredentialBoundary,
    ExecutionClass,
    IdempotencyRequirement,
    ResolvedToolVersion,
    RiskLevel,
)

POLICY_ENGINE_VERSION = 1
POLICY_SCHEMA_VERSION = 1
ENVIRONMENTS = frozenset({"development", "test", "staging", "production"})


def _digest(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


class PermissionStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class PermissionEffect(StrEnum):
    GRANT = "GRANT"
    DENY = "DENY"


class ApprovalBehavior(StrEnum):
    RISK_DEFAULT = "RISK_DEFAULT"
    ALWAYS = "ALWAYS"


class ScopeAccess(StrEnum):
    READ = "READ"
    WRITE = "WRITE"


class Decision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"


class DecisionReason(StrEnum):
    ALLOWED = "allowed"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_FORCED_BY_POLICY = "approval_forced_by_policy"
    AGENT_UNAVAILABLE = "agent_unavailable"
    AGENT_TOOL_NOT_DECLARED = "agent_tool_not_declared"
    AGENT_TOOL_EXPLICITLY_DENIED = "agent_tool_explicitly_denied"
    AGENT_SCOPE_MISSING = "agent_scope_missing"
    TOOL_UNAVAILABLE = "tool_unavailable"
    TOOL_RISK_FORBIDDEN = "tool_risk_forbidden"
    PERMISSION_MISSING = "permission_missing"
    PERMISSION_DISABLED = "permission_disabled"
    PERMISSION_EXPLICITLY_DENIED = "permission_explicitly_denied"
    PERMISSION_SCOPE_MISSING = "permission_scope_missing"
    ENVIRONMENT_NOT_ALLOWED = "environment_not_allowed"
    TENANT_MISMATCH = "tenant_mismatch"
    IDENTITY_MISMATCH = "identity_mismatch"
    SECURITY_CONTEXT_INVALID = "security_context_invalid"
    AUDIT_FAILURE = "audit_failure"


class Obligation(StrEnum):
    VALIDATE_TOOL_INPUT = "VALIDATE_TOOL_INPUT"
    CHECK_BUDGET = "CHECK_BUDGET"
    CHECK_IDEMPOTENCY = "CHECK_IDEMPOTENCY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    RESOLVE_CONNECTOR_CREDENTIAL = "RESOLVE_CONNECTOR_CREDENTIAL"
    AUDIT_EXECUTION = "AUDIT_EXECUTION"


class PermissionGovernanceError(Exception):
    code = "permission_governance_error"


class PermissionGovernanceConflict(PermissionGovernanceError):
    code = "permission_governance_conflict"


class PermissionNotFound(PermissionGovernanceError):
    code = "permission_not_found"


class PermissionVersionNotFound(PermissionGovernanceError):
    code = "permission_version_not_found"


class InvalidPermissionLifecycleTransition(PermissionGovernanceError):
    code = "invalid_permission_lifecycle_transition"


@dataclass(frozen=True, slots=True)
class ScopeRequirement:
    scope_key: str
    access: ScopeAccess
    resource_type: str | None = None
    resource_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope_key", canonical_scope_key(self.scope_key))
        if not isinstance(self.access, ScopeAccess):
            raise ValueError("scope access must be READ or WRITE")
        if (self.resource_type is None) != (self.resource_id is None):
            raise ValueError("resource_type and resource_id must be supplied together")
        if self.resource_type is not None:
            object.__setattr__(
                self,
                "resource_type",
                canonical_scope_key(self.resource_type, field_name="resource_type"),
            )
            if not self.resource_id or len(self.resource_id) > 200:
                raise ValueError("resource_id must be non-blank and at most 200 characters")

    def primitive(self) -> dict[str, str | None]:
        return {
            "scope_key": self.scope_key,
            "access": self.access.value,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
        }


@dataclass(frozen=True, slots=True)
class TrustedScopeRequirements:
    requirements: tuple[ScopeRequirement, ...] = ()
    explicitly_unscoped: bool = False

    def __post_init__(self) -> None:
        if not self.requirements and not self.explicitly_unscoped:
            raise ValueError("empty scope requirements must be explicitly trusted as unscoped")
        if self.requirements and self.explicitly_unscoped:
            raise ValueError("scoped requirements cannot also be explicitly unscoped")
        keys = [
            (item.scope_key, item.access.value, item.resource_type or "", item.resource_id or "")
            for item in self.requirements
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("scope requirements contain duplicates")
        object.__setattr__(
            self,
            "requirements",
            tuple(x for _, x in sorted(zip(keys, self.requirements, strict=True))),
        )

    @property
    def digest(self) -> str:
        return _digest(
            {
                "explicitly_unscoped": self.explicitly_unscoped,
                "requirements": [item.primitive() for item in self.requirements],
            }
        )


@dataclass(frozen=True, slots=True)
class ToolPermission:
    tenant_id: UUID
    agent_definition_id: UUID
    tool_definition_id: UUID
    created_by_actor_kind: str
    created_by_actor_id: UUID
    id: UUID = field(default_factory=uuid4)
    status: PermissionStatus = PermissionStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not isinstance(self.status, PermissionStatus):
            raise ValueError("permission status must use its canonical enum representation")
        if self.created_by_actor_kind != "user":
            raise ValueError("tenant permission policy requires a trusted user actor")


@dataclass(frozen=True, slots=True)
class ToolPermissionVersionConfiguration:
    effect: PermissionEffect
    allowed_scopes: tuple[str, ...]
    allowed_environments: tuple[str, ...]
    approval_behavior: ApprovalBehavior = ApprovalBehavior.RISK_DEFAULT
    policy_schema_version: int = POLICY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.effect, PermissionEffect) or not isinstance(
            self.approval_behavior, ApprovalBehavior
        ):
            raise ValueError("permission policy enums must use canonical values")
        object.__setattr__(
            self,
            "allowed_scopes",
            canonical_scope_keys(self.allowed_scopes, field_name="allowed_scopes"),
        )
        environments = tuple(sorted(self.allowed_environments))
        if not environments or len(environments) != len(set(environments)):
            raise ValueError("allowed_environments must be non-empty and unique")
        if any(value not in ENVIRONMENTS for value in environments):
            raise ValueError("allowed_environments contains an unsupported environment")
        object.__setattr__(self, "allowed_environments", environments)
        if self.policy_schema_version != POLICY_SCHEMA_VERSION:
            raise ValueError("unsupported permission policy schema version")

    def primitive(self) -> dict[str, object]:
        return {
            "effect": self.effect.value,
            "allowed_scopes": list(self.allowed_scopes),
            "allowed_environments": list(self.allowed_environments),
            "approval_behavior": self.approval_behavior.value,
            "policy_schema_version": self.policy_schema_version,
        }

    @property
    def configuration_digest(self) -> str:
        return _digest(self.primitive())


@dataclass(frozen=True, slots=True)
class ToolPermissionVersion:
    permission_id: UUID
    tenant_id: UUID
    version_number: int
    configuration: ToolPermissionVersionConfiguration
    created_by_actor_kind: str
    created_by_actor_id: UUID
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.version_number <= 0 or self.created_by_actor_kind != "user":
            raise ValueError("invalid permission version provenance")

    @property
    def configuration_digest(self) -> str:
        return self.configuration.configuration_digest


@dataclass(frozen=True, slots=True)
class ToolPermissionActivation:
    permission_id: UUID
    tenant_id: UUID
    active_version_id: UUID
    activated_by_actor_kind: str
    activated_by_actor_id: UUID
    activated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.activated_by_actor_kind != "user":
            raise ValueError("permission activation requires a trusted user actor")


@dataclass(frozen=True, slots=True)
class ResolvedToolPermission:
    permission_id: UUID
    permission_version_id: UUID
    version_number: int
    tenant_id: UUID
    agent_definition_id: UUID
    tool_definition_id: UUID
    configuration_digest: str
    configuration: ToolPermissionVersionConfiguration


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    decision: Decision
    reason_code: DecisionReason
    tenant_id: UUID
    actor_kind: str
    actor_id: UUID
    requested_agent_definition_id: UUID
    resolved_agent_definition_id: UUID | None
    agent_version_id: UUID | None
    agent_configuration_digest: str | None
    tool_definition_id: UUID | None
    tool_version_id: UUID | None
    tool_configuration_digest: str | None
    tool_key: str
    risk_level: RiskLevel | None
    permission_id: UUID | None
    permission_version_id: UUID | None
    permission_configuration_digest: str | None
    requested_scope_digest: str
    obligations: tuple[Obligation, ...]
    correlation_id: UUID
    decision_id: UUID = field(default_factory=uuid4)
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    policy_engine_version: int = POLICY_ENGINE_VERSION


class PermissionEngine:
    """Pure, deterministic authorization logic; it performs no I/O or execution."""

    def evaluate(
        self,
        context: ExecutionContext,
        agent: ResolvedAgentVersion,
        tool: ResolvedToolVersion,
        permission: ResolvedToolPermission,
        scopes: TrustedScopeRequirements,
    ) -> PermissionDecision:
        reason: DecisionReason | None = None
        config = agent.configuration
        policy = permission.configuration
        if agent.tenant_id != context.tenant_id or permission.tenant_id != context.tenant_id:
            reason = DecisionReason.TENANT_MISMATCH
        elif context.actor.id != context.user_id and context.actor.kind.value == "user":
            reason = DecisionReason.IDENTITY_MISMATCH
        elif (
            permission.agent_definition_id != agent.requested_tenant_definition_id
            or permission.tool_definition_id != tool.definition_id
        ):
            reason = DecisionReason.SECURITY_CONTEXT_INVALID
        elif tool.risk_level is RiskLevel.R7:
            reason = DecisionReason.TOOL_RISK_FORBIDDEN
        elif tool.tool_key in config.denied_tool_keys:
            reason = DecisionReason.AGENT_TOOL_EXPLICITLY_DENIED
        elif tool.tool_key not in config.allowed_tool_keys:
            reason = DecisionReason.AGENT_TOOL_NOT_DECLARED
        elif policy.effect is PermissionEffect.DENY:
            reason = DecisionReason.PERMISSION_EXPLICITLY_DENIED
        elif context.environment not in policy.allowed_environments:
            reason = DecisionReason.ENVIRONMENT_NOT_ALLOWED
        else:
            for requirement in scopes.requirements:
                agent_scopes = (
                    config.read_scopes
                    if requirement.access is ScopeAccess.READ
                    else config.write_scopes
                )
                if requirement.scope_key not in agent_scopes:
                    reason = DecisionReason.AGENT_SCOPE_MISSING
                    break
                if requirement.scope_key not in policy.allowed_scopes:
                    reason = DecisionReason.PERMISSION_SCOPE_MISSING
                    break
        if reason is not None:
            return self._decision(
                context, agent, tool, permission, scopes, Decision.DENY, reason, ()
            )

        obligations = {Obligation.VALIDATE_TOOL_INPUT, Obligation.AUDIT_EXECUTION}
        if tool.execution_class is ExecutionClass.PROVIDER:
            obligations.add(Obligation.CHECK_BUDGET)
        if tool.idempotency_requirement is IdempotencyRequirement.REQUIRED:
            obligations.add(Obligation.CHECK_IDEMPOTENCY)
        if tool.credential_boundary is CredentialBoundary.CONNECTOR:
            obligations.add(Obligation.RESOLVE_CONNECTOR_CREDENTIAL)
        if policy.approval_behavior is ApprovalBehavior.ALWAYS:
            obligations.add(Obligation.REQUIRE_APPROVAL)
            decision, reason = Decision.REQUIRES_APPROVAL, DecisionReason.APPROVAL_FORCED_BY_POLICY
        elif tool.risk_level in {RiskLevel.R4, RiskLevel.R5, RiskLevel.R6}:
            obligations.add(Obligation.REQUIRE_APPROVAL)
            decision, reason = Decision.REQUIRES_APPROVAL, DecisionReason.APPROVAL_REQUIRED
        else:
            decision, reason = Decision.ALLOW, DecisionReason.ALLOWED
        return self._decision(
            context, agent, tool, permission, scopes, decision, reason, tuple(sorted(obligations))
        )

    @staticmethod
    def _decision(
        context: ExecutionContext,
        agent: ResolvedAgentVersion,
        tool: ResolvedToolVersion,
        permission: ResolvedToolPermission,
        scopes: TrustedScopeRequirements,
        decision: Decision,
        reason: DecisionReason,
        obligations: tuple[Obligation, ...],
    ) -> PermissionDecision:
        return PermissionDecision(
            decision=decision,
            reason_code=reason,
            tenant_id=context.tenant_id,
            actor_kind=context.actor.kind.value,
            actor_id=context.actor.id,
            requested_agent_definition_id=agent.requested_tenant_definition_id,
            resolved_agent_definition_id=agent.resolved_definition_id,
            agent_version_id=agent.version_id,
            agent_configuration_digest=agent.configuration_digest,
            tool_definition_id=tool.definition_id,
            tool_version_id=tool.version_id,
            tool_configuration_digest=tool.configuration_digest,
            tool_key=tool.tool_key,
            risk_level=tool.risk_level,
            permission_id=permission.permission_id,
            permission_version_id=permission.permission_version_id,
            permission_configuration_digest=permission.configuration_digest,
            requested_scope_digest=scopes.digest,
            obligations=obligations,
            correlation_id=context.correlation_id,
        )
