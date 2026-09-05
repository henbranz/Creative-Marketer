from collections.abc import Mapping, Sequence
from typing import cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from creative_marketer.tool_governance.domain import (
    JSON_SCHEMA_DIALECT,
    SECRET_VALUE,
    InvalidToolSchema,
    ToolContractSchema,
    canonical_json,
    sha256_digest,
)

MAX_SCHEMA_BYTES = 65_536
MAX_SCHEMA_DEPTH = 32
MAX_SCHEMA_NODES = 10_000
RAW_CREDENTIAL_FIELDS = frozenset(
    {
        "authorization",
        "password",
        "api_key",
        "access_token",
        "refresh_token",
        "client_secret",
    }
)


def _inspect(value: object, *, depth: int = 0) -> int:
    if depth > MAX_SCHEMA_DEPTH:
        raise InvalidToolSchema("tool schema exceeds maximum nesting depth")
    nodes = 1
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in RAW_CREDENTIAL_FIELDS:
                raise InvalidToolSchema("tool schema models raw credential material")
            if key == "$ref" and (not isinstance(child, str) or not child.startswith("#")):
                raise InvalidToolSchema(
                    "tool schemas may use only self-contained local $ref values"
                )
            nodes += _inspect(child, depth=depth + 1)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            nodes += _inspect(child, depth=depth + 1)
    elif isinstance(value, str) and SECRET_VALUE.search(value):
        raise InvalidToolSchema("tool schema contains credential-shaped value")
    if nodes > MAX_SCHEMA_NODES:
        raise InvalidToolSchema("tool schema exceeds maximum node count")
    return nodes


def validate_contract_schema(value: object) -> ToolContractSchema:
    if not isinstance(value, Mapping):
        raise InvalidToolSchema("tool schema document must be a JSON object")
    document = dict(value)
    if document.get("$schema") != JSON_SCHEMA_DIALECT:
        raise InvalidToolSchema("tool schema must declare JSON Schema 2020-12")
    if document.get("type") != "object":
        raise InvalidToolSchema("tool schema root type must be object")
    serialized = canonical_json(document)
    if len(serialized.encode()) > MAX_SCHEMA_BYTES:
        raise InvalidToolSchema("tool schema exceeds 65536 canonical bytes")
    _inspect(document)
    try:
        Draft202012Validator.check_schema(document)
    except SchemaError as error:
        raise InvalidToolSchema("tool schema is not valid JSON Schema 2020-12") from error
    return ToolContractSchema(serialized, sha256_digest(cast(object, document)))
