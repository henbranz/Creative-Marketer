import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, cast
from uuid import UUID, uuid4

from creative_marketer.governance_keys import canonical_tool_key

SUPPORTED_CONFIGURATION_SCHEMA_VERSION = 1
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
CATEGORY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SECRET_VALUE = re.compile(
    r"(?i)(?:bearer\s+\S+|\bsk-[a-z0-9_-]{8,}|\bshpat_[a-z0-9]{8,}|"
    r"\bgh[pousr]_[a-z0-9]{12,}|(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|"
    r"client[_ -]?secret|password|authorization)\s*[:=]\s*\S+|"
    r"\b[a-z0-9_-]{16,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\b)"
)


class ToolDefinitionStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class RiskLevel(StrEnum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"
    R5 = "R5"
    R6 = "R6"
    R7 = "R7"


class SideEffectClass(StrEnum):
    READ_ONLY = "READ_ONLY"
    INTERNAL_MUTATION = "INTERNAL_MUTATION"
    EXTERNAL_MUTATION = "EXTERNAL_MUTATION"


class ExecutionClass(StrEnum):
    INTERNAL = "INTERNAL"
    CONNECTOR = "CONNECTOR"
    PROVIDER = "PROVIDER"


class CredentialBoundary(StrEnum):
    NONE = "NONE"
    CONNECTOR = "CONNECTOR"


class IdempotencyRequirement(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    SUPPORTED = "SUPPORTED"
    REQUIRED = "REQUIRED"


class ToolRegistryError(Exception):
    code = "tool_registry_error"


class ToolUnavailable(ToolRegistryError):
    code = "tool_unavailable"


class ToolDefinitionNotFound(ToolRegistryError):
    code = "tool_definition_not_found"


class ToolVersionNotFound(ToolRegistryError):
    code = "tool_version_not_found"


class ToolRegistryConflict(ToolRegistryError):
    code = "tool_registry_conflict"


class InvalidToolLifecycleTransition(ToolRegistryError):
    code = "invalid_tool_lifecycle_transition"


class InvalidToolSchema(ToolRegistryError, ValueError):
    code = "invalid_tool_schema"


def utc_now() -> datetime:
    return datetime.now(UTC)


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ToolContractSchema:
    """Immutable canonical schema snapshot produced by the registration validator."""

    canonical_document: str
    digest: str

    def __post_init__(self) -> None:
        parsed = json.loads(self.canonical_document)
        if not isinstance(parsed, dict) or canonical_json(parsed) != self.canonical_document:
            raise InvalidToolSchema("tool schema snapshot must be a canonical JSON object")
        if self.digest != sha256_digest(parsed):
            raise InvalidToolSchema("tool schema digest does not match its snapshot")

    def primitive(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.canonical_document))


@dataclass(frozen=True, slots=True)
class ToolVersionConfiguration:
    display_name: str
    description: str
    risk_level: RiskLevel
    side_effect_class: SideEffectClass
    execution_class: ExecutionClass
    credential_boundary: CredentialBoundary
    idempotency_requirement: IdempotencyRequirement
    input_schema: ToolContractSchema
    output_schema: ToolContractSchema
    capability_tags: tuple[str, ...] = ()
    configuration_schema_version: int = SUPPORTED_CONFIGURATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name, value, limit in (
            ("display_name", self.display_name, 200),
            ("description", self.description, 2000),
        ):
            if not value.strip() or len(value.encode()) > limit:
                raise ValueError(f"{name} must be non-blank and at most {limit} bytes")
            if SECRET_VALUE.search(value):
                raise ValueError(f"{name} contains credential-shaped content")
        enum_fields = (
            ("risk_level", self.risk_level, RiskLevel),
            ("side_effect_class", self.side_effect_class, SideEffectClass),
            ("execution_class", self.execution_class, ExecutionClass),
            ("credential_boundary", self.credential_boundary, CredentialBoundary),
            ("idempotency_requirement", self.idempotency_requirement, IdempotencyRequirement),
        )
        for name, value, expected_type in enum_fields:
            if not isinstance(value, expected_type):
                raise ValueError(f"{name} must use its canonical enum representation")
        tags = tuple(
            sorted(
                canonical_tool_key(tag, field_name="capability_tag") for tag in self.capability_tags
            )
        )
        if len(tags) != len(set(tags)):
            raise ValueError("capability_tags contains duplicates")
        object.__setattr__(self, "capability_tags", tags)
        if self.configuration_schema_version != SUPPORTED_CONFIGURATION_SCHEMA_VERSION:
            raise ValueError("unsupported tool configuration schema version")

    def digest_primitive(self) -> dict[str, object]:
        return {
            "display_name": self.display_name,
            "description": self.description,
            "risk_level": self.risk_level.value,
            "side_effect_class": self.side_effect_class.value,
            "execution_class": self.execution_class.value,
            "credential_boundary": self.credential_boundary.value,
            "idempotency_requirement": self.idempotency_requirement.value,
            "input_schema_digest": self.input_schema.digest,
            "output_schema_digest": self.output_schema.digest,
            "capability_tags": list(self.capability_tags),
            "configuration_schema_version": self.configuration_schema_version,
        }

    @property
    def configuration_digest(self) -> str:
        return sha256_digest(self.digest_primitive())


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    tool_key: str
    category: str
    created_by_actor_kind: str
    created_by_actor_id: UUID
    id: UUID = field(default_factory=uuid4)
    status: ToolDefinitionStatus = ToolDefinitionStatus.ACTIVE
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        canonical_tool_key(self.tool_key)
        if not CATEGORY.fullmatch(self.category):
            raise ValueError("category must be a lowercase canonical segment")
        if self.created_by_actor_kind not in {"workload", "system"}:
            raise ValueError("tool definitions require a platform system/workload actor")


@dataclass(frozen=True, slots=True)
class ToolVersion:
    definition_id: UUID
    version_number: int
    configuration: ToolVersionConfiguration
    created_by_actor_kind: str
    created_by_actor_id: UUID
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.version_number <= 0:
            raise ValueError("version_number must be positive")
        if self.created_by_actor_kind not in {"workload", "system"}:
            raise ValueError("tool versions require a platform system/workload actor")

    @property
    def configuration_digest(self) -> str:
        return self.configuration.configuration_digest


@dataclass(frozen=True, slots=True)
class ToolActivation:
    definition_id: UUID
    active_version_id: UUID
    activated_by_actor_kind: str
    activated_by_actor_id: UUID
    activated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.activated_by_actor_kind not in {"workload", "system"}:
            raise ValueError("tool activation requires a platform system/workload actor")


@dataclass(frozen=True, slots=True)
class ResolvedToolVersion:
    definition_id: UUID
    version_id: UUID
    version_number: int
    tool_key: str
    risk_level: RiskLevel
    side_effect_class: SideEffectClass
    execution_class: ExecutionClass
    credential_boundary: CredentialBoundary
    idempotency_requirement: IdempotencyRequirement
    input_schema: ToolContractSchema
    output_schema: ToolContractSchema
    capability_tags: tuple[str, ...]
    configuration_digest: str


class DeclarationState(StrEnum):
    KNOWN_ACTIVE = "known_active"
    KNOWN_UNAVAILABLE = "known_unavailable"
    UNKNOWN = "unknown"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class AgentToolDeclarationInspection:
    known_active: tuple[str, ...]
    known_unavailable: tuple[str, ...]
    unknown: tuple[str, ...]
    denied: tuple[str, ...]

    @property
    def states(self) -> MappingProxyType[str, DeclarationState]:
        values = {
            **{key: DeclarationState.KNOWN_ACTIVE for key in self.known_active},
            **{key: DeclarationState.KNOWN_UNAVAILABLE for key in self.known_unavailable},
            **{key: DeclarationState.UNKNOWN for key in self.unknown},
            **{key: DeclarationState.DENIED for key in self.denied},
        }
        return MappingProxyType(values)
