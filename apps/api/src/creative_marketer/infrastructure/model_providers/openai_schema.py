from __future__ import annotations

from collections.abc import Mapping, Sequence

from creative_marketer.agent_runtime.domain import ModelProviderSchemaUnsupported

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
    }
)
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


def validate_openai_strict_output_schema(schema: Mapping[str, object]) -> None:
    """Fail closed on unsupported OpenAI strict Structured Output schemas.

    The exception is deliberately content-free so schema and tenant data cannot enter
    persistence or ordinary logs through an error message.
    """

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
        object_type = node_type == "object" or (
            isinstance(node_type, Sequence)
            and not isinstance(node_type, (str, bytes))
            and "object" in node_type
        )
        if object_type:
            properties = node.get("properties")
            required = node.get("required")
            if (
                node.get("additionalProperties") is not False
                or not isinstance(properties, Mapping)
                or not isinstance(required, list)
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


def _unsupported() -> None:
    raise ModelProviderSchemaUnsupported("OpenAI strict structured output schema is unsupported")
