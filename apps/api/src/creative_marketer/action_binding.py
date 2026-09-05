import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast
from uuid import UUID, uuid4

from creative_marketer.governance_keys import canonical_scope_key, canonical_tool_key
from creative_marketer.permission_governance.domain import Decision, PermissionDecision
from creative_marketer.tool_governance.domain import RiskLevel

CANONICALIZATION_VERSION = 1
MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991
IDEMPOTENCY_KEY = re.compile(r"^op_[0-9a-f]{32}$")
SECRET_KEY_FRAGMENTS = (
    "authorization",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "client_secret",
    "credential",
    "cookie",
)
SECRET_VALUE = re.compile(
    r"(?i)(?:bearer\s+\S+|\bsk-[a-z0-9_-]{8,}|\bshpat_[a-z0-9]{8,}|"
    r"\bgh[pousr]_[a-z0-9]{12,}|(?:api[_ -]?key|password|client[_ -]?secret)\s*[:=])"
)
type CanonicalScalar = str | int | bool | None
type CanonicalValue = CanonicalScalar | Mapping[str, "CanonicalValue"] | Sequence["CanonicalValue"]


def _normalize(value: object, *, path: str = "$") -> object:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_JSON_INTEGER:
            raise ValueError(f"integer exceeds portable JSON safe range at {path}")
        return value
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError(f"string is not valid UTF-8 at {path}") from error
        if SECRET_VALUE.search(value):
            raise ValueError(f"credential-shaped value is forbidden at {path}")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError(f"canonical JSON object keys must be strings at {path}")
            if any(fragment in key.lower() for fragment in SECRET_KEY_FRAGMENTS):
                raise ValueError(f"credential-bearing field is forbidden at {path}.{key}")
            normalized[key] = _normalize(child, path=f"{path}.{key}")
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(child, path=f"{path}[{index}]") for index, child in enumerate(value)]
    raise ValueError(f"non-portable canonical JSON value at {path}")


def canonical_json_v1(value: object) -> str:
    """Strict portable subset: null/bool/integer/string/array/object, sorted UTF-8 keys."""
    return json.dumps(
        _normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_canonical_v1(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_v1(value).encode()).hexdigest()


def reject_sensitive_text(value: str, *, field_name: str) -> None:
    """Reject credential-shaped free text before it reaches durable control records."""
    if SECRET_VALUE.search(value):
        raise ValueError(f"credential-shaped value is forbidden in {field_name}")


@dataclass(frozen=True, slots=True)
class OperationIdempotencyKey:
    value: str

    def __post_init__(self) -> None:
        if not IDEMPOTENCY_KEY.fullmatch(self.value):
            raise ValueError("Agent operation idempotency key must be platform-generated")

    @classmethod
    def generate(cls) -> "OperationIdempotencyKey":
        return cls(f"op_{uuid4().hex}")


@dataclass(frozen=True, slots=True)
class NormalizedToolInput:
    canonical_json: str
    digest: str

    @classmethod
    def from_trusted_value(cls, value: object) -> "NormalizedToolInput":
        canonical = canonical_json_v1(value)
        parsed = json.loads(canonical)
        return cls(canonical, sha256_canonical_v1(parsed))

    def __post_init__(self) -> None:
        try:
            parsed = json.loads(self.canonical_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("normalized tool input is not canonical JSON") from error
        if canonical_json_v1(parsed) != self.canonical_json:
            raise ValueError("normalized tool input is not canonical JSON v1")
        if sha256_canonical_v1(parsed) != self.digest:
            raise ValueError("normalized tool input digest mismatch")

    def value(self) -> object:
        value = json.loads(self.canonical_json)
        return MappingProxyType(value) if isinstance(value, dict) else value


@dataclass(frozen=True, slots=True)
class ActionBindingV1:
    tenant_id: UUID
    requested_agent_definition_id: UUID
    resolved_agent_definition_id: UUID
    agent_version_id: UUID
    agent_configuration_digest: str
    tool_definition_id: UUID
    tool_version_id: UUID
    tool_configuration_digest: str
    tool_key: str
    risk_level: RiskLevel
    permission_id: UUID
    permission_version_id: UUID
    permission_configuration_digest: str
    permission_engine_version: int
    scope_request_digest: str
    resource_type: str | None
    resource_id: str | None
    environment: str
    normalized_input_digest: str
    idempotency_key: str
    canonicalization_version: int = CANONICALIZATION_VERSION

    @classmethod
    def from_permission_decision(
        cls,
        decision: PermissionDecision,
        normalized_input: NormalizedToolInput,
        idempotency_key: OperationIdempotencyKey,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
    ) -> "ActionBindingV1":
        required = (
            decision.resolved_agent_definition_id,
            decision.agent_version_id,
            decision.agent_configuration_digest,
            decision.tool_definition_id,
            decision.tool_version_id,
            decision.tool_configuration_digest,
            decision.risk_level,
            decision.permission_id,
            decision.permission_version_id,
            decision.permission_configuration_digest,
        )
        if decision.decision is Decision.DENY or any(value is None for value in required):
            raise ValueError("action binding requires a complete positive permission decision")
        return cls(
            tenant_id=decision.tenant_id,
            requested_agent_definition_id=decision.requested_agent_definition_id,
            resolved_agent_definition_id=cast(UUID, decision.resolved_agent_definition_id),
            agent_version_id=cast(UUID, decision.agent_version_id),
            agent_configuration_digest=cast(str, decision.agent_configuration_digest),
            tool_definition_id=cast(UUID, decision.tool_definition_id),
            tool_version_id=cast(UUID, decision.tool_version_id),
            tool_configuration_digest=cast(str, decision.tool_configuration_digest),
            tool_key=decision.tool_key,
            risk_level=cast(RiskLevel, decision.risk_level),
            permission_id=cast(UUID, decision.permission_id),
            permission_version_id=cast(UUID, decision.permission_version_id),
            permission_configuration_digest=cast(str, decision.permission_configuration_digest),
            permission_engine_version=decision.policy_engine_version,
            scope_request_digest=decision.requested_scope_digest,
            resource_type=resource_type,
            resource_id=resource_id,
            environment=decision.environment,
            normalized_input_digest=normalized_input.digest,
            idempotency_key=idempotency_key.value,
        )

    def __post_init__(self) -> None:
        canonical_tool_key(self.tool_key)
        if self.canonicalization_version != CANONICALIZATION_VERSION:
            raise ValueError("unsupported action canonicalization version")
        if not IDEMPOTENCY_KEY.fullmatch(self.idempotency_key):
            raise ValueError("invalid platform operation idempotency key")
        if (self.resource_type is None) != (self.resource_id is None):
            raise ValueError("resource type and id must be supplied together")
        if self.resource_type is not None:
            canonical_scope_key(self.resource_type, field_name="resource_type")
            if (
                not self.resource_id
                or self.resource_id != self.resource_id.strip()
                or len(self.resource_id) > 200
            ):
                raise ValueError("resource_id must be non-blank and at most 200 characters")
            reject_sensitive_text(self.resource_id, field_name="action resource_id")
        canonical_scope_key(self.environment, field_name="environment")
        if self.permission_engine_version < 1:
            raise ValueError("permission engine version must be positive")
        for digest in (
            self.agent_configuration_digest,
            self.tool_configuration_digest,
            self.permission_configuration_digest,
            self.scope_request_digest,
            self.normalized_input_digest,
        ):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise ValueError("action binding contains an invalid digest")

    def primitive(self) -> dict[str, object]:
        return {
            "tenant_id": str(self.tenant_id),
            "requested_agent_definition_id": str(self.requested_agent_definition_id),
            "resolved_agent_definition_id": str(self.resolved_agent_definition_id),
            "agent_version_id": str(self.agent_version_id),
            "agent_configuration_digest": self.agent_configuration_digest,
            "tool_definition_id": str(self.tool_definition_id),
            "tool_version_id": str(self.tool_version_id),
            "tool_configuration_digest": self.tool_configuration_digest,
            "tool_key": self.tool_key,
            "risk_level": self.risk_level.value,
            "permission_id": str(self.permission_id),
            "permission_version_id": str(self.permission_version_id),
            "permission_configuration_digest": self.permission_configuration_digest,
            "permission_engine_version": self.permission_engine_version,
            "scope_request_digest": self.scope_request_digest,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "environment": self.environment,
            "normalized_input_digest": self.normalized_input_digest,
            "idempotency_key": self.idempotency_key,
            "canonicalization_version": self.canonicalization_version,
        }

    @property
    def action_digest(self) -> str:
        return sha256_canonical_v1(self.primitive())
