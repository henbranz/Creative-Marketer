import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from creative_marketer.governance_keys import (
    canonical_scope_key,
    canonical_scope_keys,
    canonical_tool_keys,
)

AGENT_KEY = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
CURRENCY = re.compile(r"^[A-Z]{3}$")
SECRET_VALUE = re.compile(
    r"(?i)(?:bearer\s+\S+|\bsk-[a-z0-9_-]{8,}|\bshpat_[a-z0-9]{8,}|"
    r"\bgh[pousr]_[a-z0-9]{12,}|(?:api[_ -]?key|client[_ -]?secret|password)\s*[:=]\s*\S+)"
)
MAX_INSTRUCTIONS_BYTES = 20_000
SUPPORTED_CONFIGURATION_SCHEMA_VERSION = 1

CORE_AGENT_TYPES = frozenset(
    {
        "orchestrator",
        "researcher",
        "creative_strategist",
        "producer",
        "marketer",
        "commerce_operations",
        "intelligence",
    }
)


class AgentScopeKind(StrEnum):
    PLATFORM = "platform"
    TENANT = "tenant"


class AgentDefinitionStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class BudgetPeriod(StrEnum):
    DAILY = "daily"
    MONTHLY = "monthly"


class ResolutionProvenance(StrEnum):
    TENANT = "tenant"
    PLATFORM_TEMPLATE = "platform_template"


class AgentRegistryError(Exception):
    """Base registry error with a stable internal reason code."""

    code = "agent_registry_error"


class AgentUnavailable(AgentRegistryError):
    code = "agent_unavailable"


class AgentDefinitionNotFound(AgentRegistryError):
    code = "agent_definition_not_found"


class AgentVersionNotFound(AgentRegistryError):
    code = "agent_version_not_found"


class AgentRegistryConflict(AgentRegistryError):
    code = "agent_registry_conflict"


class InvalidLifecycleTransition(AgentRegistryError):
    code = "invalid_agent_lifecycle_transition"


def utc_now() -> datetime:
    return datetime.now(UTC)


def _canonical_key(value: str, *, field_name: str, max_length: int = 128) -> str:
    if len(value) > max_length:
        raise ValueError(f"{field_name} must be a canonical key without wildcards")
    return canonical_scope_key(value, field_name=field_name)


def _agent_key(value: str, *, field_name: str) -> str:
    if not AGENT_KEY.fullmatch(value):
        raise ValueError(f"{field_name} must match {AGENT_KEY.pattern}")
    return value


def _bounded_text(value: str, *, field_name: str, max_bytes: int) -> str:
    if not value.strip() or len(value.encode()) > max_bytes:
        raise ValueError(f"{field_name} must be non-blank and at most {max_bytes} bytes")
    if SECRET_VALUE.search(value):
        raise ValueError(f"{field_name} contains credential-shaped content")
    return value


def _canonical_set(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    return canonical_scope_keys(values, field_name=field_name)


def _money(value: Decimal, *, field_name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{field_name} must be a finite non-negative decimal")
    return value


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _actor_kind(value: str) -> None:
    if value not in {"user", "workload", "system"}:
        raise ValueError("registry provenance actor kind is invalid")


@dataclass(frozen=True, slots=True)
class ModelPolicy:
    profile_key: str
    required_capabilities: tuple[str, ...]
    max_turns: int
    structured_output_required: bool = True
    fallback_allowed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "profile_key", _canonical_key(self.profile_key, field_name="profile")
        )
        object.__setattr__(
            self,
            "required_capabilities",
            _canonical_set(self.required_capabilities, field_name="required capabilities"),
        )
        if self.max_turns <= 0:
            raise ValueError("max_turns must be positive")

    def primitive(self) -> dict[str, object]:
        return {
            "profile_key": self.profile_key,
            "required_capabilities": list(self.required_capabilities),
            "max_turns": self.max_turns,
            "structured_output_required": self.structured_output_required,
            "fallback_allowed": self.fallback_allowed,
        }


@dataclass(frozen=True, slots=True)
class RunBudgetPolicy:
    max_model_calls: int
    max_tool_calls: int
    max_total_tokens: int
    max_cost: Decimal
    currency: str

    def __post_init__(self) -> None:
        if min(self.max_model_calls, self.max_tool_calls, self.max_total_tokens) < 0:
            raise ValueError("run budget counters cannot be negative")
        object.__setattr__(self, "max_cost", _money(self.max_cost, field_name="max_cost"))
        if not CURRENCY.fullmatch(self.currency):
            raise ValueError("currency must be an uppercase ISO-style three-letter code")

    def primitive(self) -> dict[str, object]:
        return {
            "max_model_calls": self.max_model_calls,
            "max_tool_calls": self.max_tool_calls,
            "max_total_tokens": self.max_total_tokens,
            "max_cost": _decimal_text(self.max_cost),
            "currency": self.currency,
        }


@dataclass(frozen=True, slots=True)
class PeriodBudgetPolicy:
    period: BudgetPeriod
    max_runs: int | None
    max_cost: Decimal
    currency: str

    def __post_init__(self) -> None:
        if self.max_runs is not None and self.max_runs < 0:
            raise ValueError("period max_runs cannot be negative")
        object.__setattr__(self, "max_cost", _money(self.max_cost, field_name="max_cost"))
        if not CURRENCY.fullmatch(self.currency):
            raise ValueError("currency must be an uppercase ISO-style three-letter code")

    def primitive(self) -> dict[str, object]:
        return {
            "period": self.period.value,
            "max_runs": self.max_runs,
            "max_cost": _decimal_text(self.max_cost),
            "currency": self.currency,
        }


@dataclass(frozen=True, slots=True)
class AgentVersionConfiguration:
    display_name: str
    mission: str
    responsibilities: tuple[str, ...]
    system_instructions: str
    prompt_revision: str
    model_policy: ModelPolicy
    run_budget_policy: RunBudgetPolicy
    period_budget_policy: PeriodBudgetPolicy
    read_scopes: tuple[str, ...]
    write_scopes: tuple[str, ...]
    memory_scopes: tuple[str, ...]
    allowed_tool_keys: tuple[str, ...]
    denied_tool_keys: tuple[str, ...]
    approval_policy_key: str
    output_contract_key: str | None = None
    output_contract_version: int | None = None
    configuration_schema_version: int = SUPPORTED_CONFIGURATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _bounded_text(self.display_name, field_name="display_name", max_bytes=200)
        _bounded_text(self.mission, field_name="mission", max_bytes=2_000)
        if not self.responsibilities or len(self.responsibilities) != len(
            set(self.responsibilities)
        ):
            raise ValueError("responsibilities must be non-empty and unique")
        for responsibility in self.responsibilities:
            _bounded_text(responsibility, field_name="responsibility", max_bytes=500)
        object.__setattr__(self, "responsibilities", tuple(sorted(self.responsibilities)))
        _bounded_text(
            self.system_instructions,
            field_name="system_instructions",
            max_bytes=MAX_INSTRUCTIONS_BYTES,
        )
        object.__setattr__(
            self,
            "prompt_revision",
            _canonical_key(self.prompt_revision, field_name="prompt_revision", max_length=64),
        )
        for field_name in ("read_scopes", "write_scopes", "memory_scopes"):
            object.__setattr__(
                self,
                field_name,
                _canonical_set(getattr(self, field_name), field_name=field_name),
            )
        object.__setattr__(
            self,
            "allowed_tool_keys",
            canonical_tool_keys(self.allowed_tool_keys, field_name="allowed_tool_keys"),
        )
        object.__setattr__(
            self,
            "denied_tool_keys",
            canonical_tool_keys(self.denied_tool_keys, field_name="denied_tool_keys"),
        )
        if set(self.allowed_tool_keys) & set(self.denied_tool_keys):
            raise ValueError("a tool key cannot be both allowed and denied")
        object.__setattr__(
            self,
            "approval_policy_key",
            _canonical_key(self.approval_policy_key, field_name="approval_policy_key"),
        )
        if (self.output_contract_key is None) != (self.output_contract_version is None):
            raise ValueError("output contract key and version must be specified together")
        if self.output_contract_key is not None:
            object.__setattr__(
                self,
                "output_contract_key",
                _canonical_key(self.output_contract_key, field_name="output_contract_key"),
            )
            if self.output_contract_version is None or self.output_contract_version <= 0:
                raise ValueError("output_contract_version must be positive")
        if self.configuration_schema_version != SUPPORTED_CONFIGURATION_SCHEMA_VERSION:
            raise ValueError("unsupported agent configuration schema version")

    def primitive(self) -> dict[str, object]:
        return {
            "display_name": self.display_name,
            "mission": self.mission,
            "responsibilities": list(self.responsibilities),
            "system_instructions": self.system_instructions,
            "prompt_revision": self.prompt_revision,
            "model_policy": self.model_policy.primitive(),
            "run_budget_policy": self.run_budget_policy.primitive(),
            "period_budget_policy": self.period_budget_policy.primitive(),
            "read_scopes": list(self.read_scopes),
            "write_scopes": list(self.write_scopes),
            "memory_scopes": list(self.memory_scopes),
            "allowed_tool_keys": list(self.allowed_tool_keys),
            "denied_tool_keys": list(self.denied_tool_keys),
            "approval_policy_key": self.approval_policy_key,
            "output_contract_key": self.output_contract_key,
            "output_contract_version": self.output_contract_version,
            "configuration_schema_version": self.configuration_schema_version,
        }

    @property
    def configuration_digest(self) -> str:
        canonical = json.dumps(
            self.primitive(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    scope_kind: AgentScopeKind
    tenant_id: UUID | None
    platform_template_id: UUID | None
    agent_key: str
    agent_type: str
    created_by_actor_kind: str
    created_by_actor_id: UUID
    id: UUID = field(default_factory=uuid4)
    status: AgentDefinitionStatus = AgentDefinitionStatus.ACTIVE
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        valid_ownership = (
            self.scope_kind is AgentScopeKind.PLATFORM
            and self.tenant_id is None
            and self.platform_template_id is None
        ) or (self.scope_kind is AgentScopeKind.TENANT and self.tenant_id is not None)
        if not valid_ownership:
            raise ValueError("invalid agent definition ownership")
        _agent_key(self.agent_key, field_name="agent_key")
        _agent_key(self.agent_type, field_name="agent_type")
        _actor_kind(self.created_by_actor_kind)


@dataclass(frozen=True, slots=True)
class AgentVersion:
    definition_id: UUID
    scope_kind: AgentScopeKind
    tenant_id: UUID | None
    version_number: int
    configuration: AgentVersionConfiguration
    created_by_actor_kind: str
    created_by_actor_id: UUID
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        valid_ownership = (
            self.scope_kind is AgentScopeKind.PLATFORM and self.tenant_id is None
        ) or (self.scope_kind is AgentScopeKind.TENANT and self.tenant_id is not None)
        if not valid_ownership or self.version_number <= 0:
            raise ValueError("invalid agent version ownership or version number")
        _actor_kind(self.created_by_actor_kind)

    @property
    def configuration_digest(self) -> str:
        return self.configuration.configuration_digest


@dataclass(frozen=True, slots=True)
class AgentActivation:
    definition_id: UUID
    active_version_id: UUID
    scope_kind: AgentScopeKind
    tenant_id: UUID | None
    activated_by_actor_kind: str
    activated_by_actor_id: UUID
    activated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        valid_ownership = (
            self.scope_kind is AgentScopeKind.PLATFORM and self.tenant_id is None
        ) or (self.scope_kind is AgentScopeKind.TENANT and self.tenant_id is not None)
        if not valid_ownership:
            raise ValueError("invalid agent activation ownership")
        _actor_kind(self.activated_by_actor_kind)


@dataclass(frozen=True, slots=True)
class ResolvedAgentVersion:
    requested_tenant_definition_id: UUID
    resolved_definition_id: UUID
    version_id: UUID
    version_number: int
    tenant_id: UUID
    agent_key: str
    agent_type: str
    provenance: ResolutionProvenance
    configuration_digest: str
    configuration: AgentVersionConfiguration
