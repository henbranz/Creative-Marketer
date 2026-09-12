import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class KnowledgeProjectionError(ValueError):
    pass


class KnowledgeNodeType(StrEnum):
    BRAND = "brand"
    PRODUCT = "product"
    PRODUCT_KNOWLEDGE_SNAPSHOT = "product_knowledge_snapshot"
    AGENT_DEFINITION = "agent_definition"
    AGENT_VERSION = "agent_version"
    AGENT_RUN = "agent_run"
    RESEARCH_SOURCE = "research_source"
    EVIDENCE_SNAPSHOT = "evidence_snapshot"
    RESEARCH_SNAPSHOT = "research_snapshot"
    RESEARCH_FINDING = "research_finding"
    CREATIVE_CONCEPT_SET = "creative_concept_set"
    CREATIVE_CONCEPT = "creative_concept"
    CREATIVE_CONCEPT_DECISION = "creative_concept_decision"
    ASSET = "asset"


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SECRET = re.compile(
    r"(?i)(?:bearer\s+\S+|\bsk-[a-z0-9_-]{8,}|\bshpat_[a-z0-9]{8,}|"
    r"\bgh[pousr]_[a-z0-9]{12,}|\b(?:api[_ -]?key|client[_ -]?secret|password|"
    r"access[_ -]?token|refresh[_ -]?token|authorization)\s*[:=]\s*\S+|"
    r"\b[a-z0-9_-]{16,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})"
)
_FORBIDDEN_KEYS = re.compile(
    r"(?i)(?:authorization|cookie|password|secret|credential|access[_-]?token|"
    r"refresh[_-]?token|provider_response|raw_html|raw_provider|object_key|signed_url|"
    r"system_instructions|hidden_reasoning|chain_of_thought|prompt)"
)
_SIGNED_URL = re.compile(
    r"(?i)[?&](?:token|api[_-]?key|signature|x-amz-signature|x-goog-signature|"
    r"access[_-]?token)="
)


def safe_projection_value(value: object, *, _depth: int = 0) -> object:
    """Return JSON-safe, bounded projection data with credential-shaped material removed."""
    if _depth > 7:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, child in list(value.items())[:128]:
            key = str(raw_key)[:100]
            if _FORBIDDEN_KEYS.search(key):
                continue
            result[key] = safe_projection_value(child, _depth=_depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [safe_projection_value(child, _depth=_depth + 1) for child in value[:256]]
    if isinstance(value, str):
        if _SECRET.search(value) or _SIGNED_URL.search(value):
            return "[REDACTED]"
        return value[:20_000]
    if isinstance(value, bool | int | float) or value is None:
        return value
    return str(value)[:500]


def projection_digest(value: object) -> str:
    encoded = json.dumps(
        safe_projection_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True, order=True)
class KnowledgeNodeRef:
    node_type: KnowledgeNodeType
    canonical_id: str

    def __post_init__(self) -> None:
        if not self.canonical_id.strip() or len(self.canonical_id) > 256:
            raise KnowledgeProjectionError("canonical node identity is invalid")


@dataclass(frozen=True, slots=True)
class KnowledgeRelationship:
    target: KnowledgeNodeRef
    relationship_type: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.relationship_type):
            raise KnowledgeProjectionError("relationship type is invalid")


@dataclass(frozen=True, slots=True)
class KnowledgeNode:
    node_type: KnowledgeNodeType
    canonical_id: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    properties: Mapping[str, object] = field(default_factory=dict)
    relationships: tuple[KnowledgeRelationship, ...] = ()
    semantic_digest: str | None = None

    def __post_init__(self) -> None:
        KnowledgeNodeRef(self.node_type, self.canonical_id)
        if not self.title.strip() or len(self.title) > 500:
            raise KnowledgeProjectionError("node title is invalid")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise KnowledgeProjectionError("node timestamps must be timezone-aware")
        if self.semantic_digest is not None and not _DIGEST.fullmatch(self.semantic_digest):
            raise KnowledgeProjectionError("semantic digest is invalid")
        object.__setattr__(self, "properties", safe_projection_value(self.properties))
        object.__setattr__(
            self,
            "relationships",
            tuple(
                sorted(
                    set(self.relationships), key=lambda item: (item.relationship_type, item.target)
                )
            ),
        )

    @property
    def ref(self) -> KnowledgeNodeRef:
        return KnowledgeNodeRef(self.node_type, self.canonical_id)

    def primitive(self) -> dict[str, object]:
        return {
            "node_type": self.node_type.value,
            "canonical_id": self.canonical_id,
            "title": self.title,
            "status": self.status,
            "semantic_digest": self.semantic_digest,
            "created_at": self.created_at.astimezone(UTC).isoformat(),
            "updated_at": self.updated_at.astimezone(UTC).isoformat(),
            "properties": self.properties,
            "relationships": [
                {
                    "relationship_type": item.relationship_type,
                    "target_node_type": item.target.node_type.value,
                    "target_canonical_id": item.target.canonical_id,
                }
                for item in self.relationships
            ],
        }

    @property
    def projection_digest(self) -> str:
        return projection_digest(self.primitive())


@dataclass(frozen=True, slots=True)
class KnowledgeEdge:
    source_node: KnowledgeNodeRef
    target_node: KnowledgeNodeRef
    relationship_type: str


@dataclass(frozen=True, slots=True)
class KnowledgeProjectionRevision:
    revision: int
    projected_at: datetime


@dataclass(frozen=True, slots=True)
class KnowledgeGraph:
    nodes: tuple[KnowledgeNode, ...]

    @property
    def edges(self) -> tuple[KnowledgeEdge, ...]:
        return tuple(
            KnowledgeEdge(node.ref, relationship.target, relationship.relationship_type)
            for node in self.nodes
            for relationship in node.relationships
        )

    def __post_init__(self) -> None:
        refs = [node.ref for node in self.nodes]
        if len(refs) != len(set(refs)):
            raise KnowledgeProjectionError("graph contains duplicate node identities")


@dataclass(frozen=True, slots=True)
class KnowledgeChange:
    revision: int
    node: KnowledgeNode | None
    deleted_node: KnowledgeNodeRef | None

    def __post_init__(self) -> None:
        if self.revision < 1 or (self.node is None) == (self.deleted_node is None):
            raise KnowledgeProjectionError("projection change is invalid")
