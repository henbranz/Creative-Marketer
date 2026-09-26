from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import NoReturn

from creative_marketer.agent_runtime.domain import (
    ModelProviderSchemaUnsupported,
    canonical_digest,
)

OPENAI_SCHEMA_COMPILER_REVISION = "openai-strict-2026-09-26.1"
MAX_OPENAI_SCHEMA_PROPERTIES = 5_000
MAX_OPENAI_SCHEMA_CONTAINER_DEPTH = 10

_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "oneOf",
        "allOf",
        "not",
        "dependentRequired",
        "dependentSchemas",
        "if",
        "then",
        "else",
        "const",
        "uniqueItems",
    }
)
_OBJECT_STRUCTURAL_KEYWORDS = frozenset({"properties", "required", "additionalProperties"})
_SUPPORTED_FORMATS = frozenset(
    {
        "date-time",
        "time",
        "date",
        "duration",
        "email",
        "hostname",
        "ipv4",
        "ipv6",
        "uuid",
    }
)
_PRODUCER_CONTRACT_KEY = "production.production_plan"
_PRODUCER_CONTRACT_VERSION = 2
_PRODUCER_ALTERNATIVE_SHAPES = {
    "shot": (
        ("source_strategy", "existing_asset_id", "image_generation_spec"),
        ("USE_EXISTING_ASSET", "GENERATE_IMAGE", "GENERATE_VIDEO", "MANUAL_CAPTURE"),
    ),
    "segment": (
        ("media_kind", "duration_seconds"),
        ("IMAGE", "VIDEO"),
    ),
}


@dataclass(frozen=True, slots=True)
class CompiledOpenAISchema:
    contract_key: str
    contract_version: int
    schema: Mapping[str, object]
    digest: str
    compiler_revision: str = OPENAI_SCHEMA_COMPILER_REVISION


@dataclass(frozen=True, slots=True)
class OpenAISchemaAudit:
    root_type: object
    object_schemas: int
    any_of_branches: int
    partial_object_branches: int
    unsupported_keywords: tuple[str, ...]
    normalizations: tuple[str, ...]
    properties: int
    container_depth: int
    schema_bytes: int


def compile_openai_strict_output_schema(
    schema: Mapping[str, object], *, contract_key: str, contract_version: int
) -> CompiledOpenAISchema:
    """Compile a canonical contract into the exact OpenAI representation."""

    normalized = deepcopy(dict(schema))
    if contract_key == _PRODUCER_CONTRACT_KEY and contract_version == _PRODUCER_CONTRACT_VERSION:
        _expand_producer_v2_alternatives(normalized)
    _normalize_node(normalized)
    validate_openai_strict_output_schema(normalized)
    audit = audit_openai_schema(normalized)
    if (
        audit.properties > MAX_OPENAI_SCHEMA_PROPERTIES
        or audit.container_depth > MAX_OPENAI_SCHEMA_CONTAINER_DEPTH
    ):
        _unsupported()
    return CompiledOpenAISchema(
        contract_key=contract_key,
        contract_version=contract_version,
        schema=normalized,
        digest=canonical_digest(normalized),
    )


def normalize_openai_strict_output_schema(schema: Mapping[str, object]) -> dict[str, object]:
    """Compatibility wrapper for schemas requiring no identity-specific transform."""

    normalized = deepcopy(dict(schema))
    _normalize_node(normalized)
    validate_openai_strict_output_schema(normalized)
    audit = audit_openai_schema(normalized)
    if (
        audit.properties > MAX_OPENAI_SCHEMA_PROPERTIES
        or audit.container_depth > MAX_OPENAI_SCHEMA_CONTAINER_DEPTH
    ):
        _unsupported()
    return normalized


def _expand_producer_v2_alternatives(schema: dict[str, object]) -> None:
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        _unsupported()
    for definition_name, (
        expected_fields,
        expected_discriminators,
    ) in _PRODUCER_ALTERNATIVE_SHAPES.items():
        node = definitions.get(definition_name)
        if not isinstance(node, dict):
            _unsupported()
        alternatives = node.get("anyOf")
        # Compiling an already-compiled provider representation is idempotent.
        if (
            set(node) == {"anyOf"}
            and isinstance(alternatives, list)
            and alternatives
            and all(
                isinstance(branch, Mapping) and branch.get("type") == "object"
                for branch in alternatives
            )
        ):
            continue
        properties = node.get("properties")
        required = node.get("required")
        if (
            node.get("type") != "object"
            or node.get("additionalProperties") is not False
            or not isinstance(properties, dict)
            or not isinstance(required, list)
            or not isinstance(alternatives, list)
            or len(alternatives) != len(expected_discriminators)
        ):
            _unsupported()
        discriminator = expected_fields[0]
        observed: list[object] = []
        expanded: list[dict[str, object]] = []
        base = deepcopy(node)
        base.pop("anyOf")
        for branch in alternatives:
            if not isinstance(branch, Mapping) or set(branch) != {"properties"}:
                _unsupported()
            branch_properties = branch.get("properties")
            if (
                not isinstance(branch_properties, Mapping)
                or tuple(branch_properties) != expected_fields
                or not set(branch_properties).issubset(properties)
            ):
                _unsupported()
            discriminator_schema = branch_properties.get(discriminator)
            if not isinstance(discriminator_schema, Mapping) or set(discriminator_schema) != {
                "const"
            }:
                _unsupported()
            observed.append(discriminator_schema["const"])
            complete = deepcopy(base)
            complete_properties = complete.get("properties")
            if not isinstance(complete_properties, dict):
                _unsupported()
            complete_properties.update(deepcopy(dict(branch_properties)))
            expanded.append(complete)
        if tuple(observed) != expected_discriminators:
            _unsupported()
        definitions[definition_name] = {"anyOf": expanded}


def _normalize_node(node: dict[str, object]) -> None:
    node.pop("uniqueItems", None)
    if "const" in node:
        if "enum" in node:
            _unsupported()
        node["enum"] = [node.pop("const")]
    for collection_key in ("properties", "$defs", "definitions"):
        collection = node.get(collection_key)
        if isinstance(collection, dict):
            for child in collection.values():
                if isinstance(child, dict):
                    _normalize_node(child)
    items = node.get("items")
    if isinstance(items, dict):
        _normalize_node(items)
    alternatives = node.get("anyOf")
    if isinstance(alternatives, list):
        for child in alternatives:
            if isinstance(child, dict):
                _normalize_node(child)


def validate_openai_strict_output_schema(schema: Mapping[str, object]) -> None:
    """Fail closed without including schema or tenant content in the exception."""

    if schema.get("type") != "object" or "anyOf" in schema:
        _unsupported()
    _validate_node(schema)


def _validate_node(node: object) -> None:
    if isinstance(node, Mapping):
        if _UNSUPPORTED_KEYWORDS.intersection(node):
            _unsupported()
        schema_format = node.get("format")
        if schema_format is not None and (
            not isinstance(schema_format, str) or schema_format not in _SUPPORTED_FORMATS
        ):
            _unsupported()
        node_type = node.get("type")
        if _OBJECT_STRUCTURAL_KEYWORDS.intersection(node) and node_type != "object":
            _unsupported()
        if node_type == "object":
            properties = node.get("properties")
            required = node.get("required")
            if (
                node.get("additionalProperties") is not False
                or not isinstance(properties, Mapping)
                or not isinstance(required, list)
                or not all(isinstance(value, str) for value in required)
                or len(required) != len(set(required))
                or set(required) != set(properties)
            ):
                _unsupported()
        alternatives = node.get("anyOf")
        if alternatives is not None and (
            not isinstance(alternatives, list)
            or not alternatives
            or not all(isinstance(branch, Mapping) for branch in alternatives)
        ):
            _unsupported()
        for collection_key in ("properties", "$defs", "definitions"):
            collection = node.get(collection_key)
            if isinstance(collection, Mapping):
                for value in collection.values():
                    _validate_node(value)
        items = node.get("items")
        if isinstance(items, Mapping):
            _validate_node(items)
        if isinstance(alternatives, list):
            for value in alternatives:
                _validate_node(value)
    elif isinstance(node, list):
        for value in node:
            _validate_node(value)


def audit_openai_schema(schema: Mapping[str, object]) -> OpenAISchemaAudit:
    nodes: list[Mapping[str, object]] = []
    partial = 0
    unsupported: set[str] = set()
    normalizations: set[str] = set()

    def walk(node: Mapping[str, object]) -> None:
        nonlocal partial
        nodes.append(node)
        unsupported.update(_UNSUPPORTED_KEYWORDS.intersection(node))
        if _OBJECT_STRUCTURAL_KEYWORDS.intersection(node) and node.get("type") != "object":
            partial += 1
        if "const" in node:
            normalizations.add("const_to_singleton_enum")
        if "uniqueItems" in node:
            normalizations.add("omit_provider_uniqueItems")
        for collection_key in ("properties", "$defs", "definitions"):
            collection = node.get(collection_key)
            if isinstance(collection, Mapping):
                for child in collection.values():
                    if isinstance(child, Mapping):
                        walk(child)
        items = node.get("items")
        if isinstance(items, Mapping):
            walk(items)
        alternatives = node.get("anyOf")
        if isinstance(alternatives, list):
            for child in alternatives:
                if isinstance(child, Mapping):
                    walk(child)

    walk(schema)
    return OpenAISchemaAudit(
        root_type=schema.get("type"),
        object_schemas=sum(node.get("type") == "object" for node in nodes),
        any_of_branches=sum(
            len(value) for node in nodes if isinstance((value := node.get("anyOf")), list)
        ),
        partial_object_branches=partial,
        unsupported_keywords=tuple(sorted(unsupported)),
        normalizations=tuple(sorted(normalizations)),
        properties=sum(
            len(value) for node in nodes if isinstance((value := node.get("properties")), Mapping)
        ),
        container_depth=_schema_container_depth(schema, schema, ()),
        schema_bytes=len(json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode()),
    )


def _schema_container_depth(
    node: Mapping[str, object], root: Mapping[str, object], seen_refs: tuple[str, ...]
) -> int:
    reference = node.get("$ref")
    if reference is not None:
        if (
            not isinstance(reference, str)
            or not reference.startswith("#/")
            or reference in seen_refs
        ):
            _unsupported()
        target: object = root
        for part in reference[2:].split("/"):
            if not isinstance(target, Mapping):
                _unsupported()
            target = target.get(part.replace("~1", "/").replace("~0", "~"))
        if not isinstance(target, Mapping):
            _unsupported()
        return _schema_container_depth(target, root, (*seen_refs, reference))
    children: list[Mapping[str, object]] = []
    properties = node.get("properties")
    if isinstance(properties, Mapping):
        children.extend(value for value in properties.values() if isinstance(value, Mapping))
    alternatives = node.get("anyOf")
    if isinstance(alternatives, list):
        children.extend(value for value in alternatives if isinstance(value, Mapping))
    items = node.get("items")
    if isinstance(items, Mapping):
        children.append(items)
    node_type = node.get("type")
    own = int(node_type == "object" or node_type == "array")
    return own + max(
        (_schema_container_depth(child, root, seen_refs) for child in children), default=0
    )


def _unsupported() -> NoReturn:
    raise ModelProviderSchemaUnsupported("OpenAI strict structured output schema is unsupported")
