import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID, uuid4

from creative_marketer.identity.application.authentication import Actor, ActorKind, ExecutionContext

EVENT_CANONICALIZATION_VERSION = 1
MAX_EVENT_PAYLOAD_BYTES = 16_384
MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991
EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\.v([1-9][0-9]*)$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
SENSITIVE_KEY_FRAGMENTS = (
    "authorization",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "cookie",
    "email",
    "phone",
    "shipping_address",
)
SENSITIVE_VALUE = re.compile(
    r"(?i)(?:bearer\s+\S+|\bsk-[a-z0-9_-]{8,}|\bshpat_[a-z0-9]{8,}|"
    r"\bgh[pousr]_[a-z0-9]{12,}|(?:api[_ -]?key|password|client[_ -]?secret)\s*[:=])"
)


class EventScopeKind(StrEnum):
    TENANT = "tenant"
    PLATFORM = "platform"


class EventContractError(ValueError):
    code = "EVENT_CONTRACT_INVALID"


class EventIdConflict(EventContractError):
    code = "EVENT_ID_CONFLICT"


def _normalize(value: object, *, path: str = "$") -> object:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_JSON_INTEGER:
            raise EventContractError(f"integer exceeds portable range at {path}")
        return value
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise EventContractError(f"invalid UTF-8 at {path}") from error
        if SENSITIVE_VALUE.search(value):
            raise EventContractError(f"credential-shaped value is forbidden at {path}")
        return value
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise EventContractError(f"event object keys must be strings at {path}")
            normalized_key = key.lower()
            if any(part in normalized_key for part in SENSITIVE_KEY_FRAGMENTS):
                raise EventContractError(f"sensitive field is forbidden at {path}.{key}")
            result[key] = _normalize(child, path=f"{path}.{key}")
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise EventContractError(f"non-portable event value at {path}")


def canonical_event_json_v1(value: object) -> str:
    """Event Canonical JSON V1: strict JSON subset, sorted keys, compact UTF-8."""
    return json.dumps(_normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def event_sha256_v1(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_event_json_v1(value).encode()).hexdigest()


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    return value


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_type: str
    schema_version: int
    scope_kind: EventScopeKind
    tenant_id: UUID | None
    aggregate_type: str
    aggregate_id: UUID
    occurred_at: datetime
    actor_kind: ActorKind
    actor_id: UUID
    correlation_id: UUID
    payload: Mapping[str, object]
    payload_schema_digest: str
    causation_id: UUID | None = None
    agent_definition_id: UUID | None = None
    agent_version_id: UUID | None = None
    agent_run_id: UUID | None = None
    event_id: UUID = field(default_factory=uuid4)
    canonicalization_version: int = EVENT_CANONICALIZATION_VERSION

    def __post_init__(self) -> None:
        match = EVENT_TYPE_PATTERN.fullmatch(self.event_type)
        if match is None or int(match.group(1)) != self.schema_version:
            raise EventContractError("event type suffix and schema version must agree")
        if (self.scope_kind is EventScopeKind.TENANT) != (self.tenant_id is not None):
            raise EventContractError("tenant scope requires tenant_id; platform scope forbids it")
        if self.occurred_at.tzinfo is None:
            raise EventContractError("event timestamp must be timezone-aware")
        if not self.aggregate_type or len(self.aggregate_type) > 100:
            raise EventContractError("aggregate type must be present and bounded")
        if not DIGEST_PATTERN.fullmatch(self.payload_schema_digest):
            raise EventContractError("invalid payload schema digest")
        if self.canonicalization_version != EVENT_CANONICALIZATION_VERSION:
            raise EventContractError("unsupported event canonicalization version")
        canonical = canonical_event_json_v1(self.payload)
        if len(canonical.encode()) > MAX_EVENT_PAYLOAD_BYTES:
            raise EventContractError("event payload exceeds 16384 canonical bytes")
        object.__setattr__(self, "payload", _freeze(json.loads(canonical)))

    def semantic_envelope(self) -> dict[str, object]:
        return {
            "canonicalization_version": self.canonicalization_version,
            "event_id": str(self.event_id),
            "event_type": self.event_type,
            "schema_version": self.schema_version,
            "scope_kind": self.scope_kind.value,
            "tenant_id": None if self.tenant_id is None else str(self.tenant_id),
            "aggregate_type": self.aggregate_type,
            "aggregate_id": str(self.aggregate_id),
            "occurred_at": _utc_text(self.occurred_at),
            "actor_kind": self.actor_kind.value,
            "actor_id": str(self.actor_id),
            "agent_definition_id": (
                None if self.agent_definition_id is None else str(self.agent_definition_id)
            ),
            "agent_version_id": (
                None if self.agent_version_id is None else str(self.agent_version_id)
            ),
            "agent_run_id": None if self.agent_run_id is None else str(self.agent_run_id),
            "correlation_id": str(self.correlation_id),
            "causation_id": None if self.causation_id is None else str(self.causation_id),
            "payload_schema_digest": self.payload_schema_digest,
            "payload": json.loads(canonical_event_json_v1(self.payload)),
        }

    @property
    def event_digest(self) -> str:
        return event_sha256_v1(self.semantic_envelope())


def tenant_event(
    context: ExecutionContext,
    *,
    event_type: str,
    schema_version: int,
    aggregate_type: str,
    aggregate_id: UUID,
    payload: Mapping[str, object],
    payload_schema_digest: str,
    occurred_at: datetime,
    causation_id: UUID | None = None,
) -> DomainEvent:
    """Build a tenant event only from authoritative runtime context."""
    return DomainEvent(
        event_type=event_type,
        schema_version=schema_version,
        scope_kind=EventScopeKind.TENANT,
        tenant_id=context.tenant_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        occurred_at=occurred_at,
        actor_kind=context.actor.kind,
        actor_id=context.actor.id,
        correlation_id=context.correlation_id,
        causation_id=causation_id,
        payload=payload,
        payload_schema_digest=payload_schema_digest,
    )


def tenant_event_caused_by(
    source: DomainEvent,
    actor: Actor,
    *,
    event_type: str,
    schema_version: int,
    aggregate_type: str,
    aggregate_id: UUID,
    payload: Mapping[str, object],
    payload_schema_digest: str,
    occurred_at: datetime,
) -> DomainEvent:
    """Build a new tenant fact whose authority and trace lineage come from a trusted event."""
    if source.scope_kind is not EventScopeKind.TENANT or source.tenant_id is None:
        raise EventContractError("tenant consumer cannot derive authority from a platform event")
    return DomainEvent(
        event_type=event_type,
        schema_version=schema_version,
        scope_kind=EventScopeKind.TENANT,
        tenant_id=source.tenant_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        occurred_at=occurred_at,
        actor_kind=actor.kind,
        actor_id=actor.id,
        correlation_id=source.correlation_id,
        causation_id=source.event_id,
        payload=payload,
        payload_schema_digest=payload_schema_digest,
    )
