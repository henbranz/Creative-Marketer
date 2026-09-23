# mypy: disable-error-code="no-untyped-def,index"

from __future__ import annotations

from copy import deepcopy

import pytest

from creative_marketer.agent_runtime.application import (
    load_output_schema,
    load_supervisor_output_schema,
)
from creative_marketer.agent_runtime.domain import ModelProviderSchemaUnsupported
from creative_marketer.commerce.application import load_commerce_output_schema
from creative_marketer.creative.application import load_creative_output_schema
from creative_marketer.infrastructure.model_providers.openai_schema import (
    validate_openai_strict_output_schema,
)
from creative_marketer.intelligence.application import load_intelligence_output_schema
from creative_marketer.production.application import load_production_plan_schema


def test_creative_schema_uses_supported_disjoint_any_of() -> None:
    schema = load_creative_output_schema()
    requirement = schema["$defs"]["asset_requirement"]
    assert "oneOf" not in requirement
    assert [branch["properties"]["kind"]["const"] for branch in requirement["anyOf"]] == [
        "EXISTING_ASSET",
        "MISSING_ASSET",
    ]
    validate_openai_strict_output_schema(schema)


@pytest.mark.parametrize(
    "schema",
    [
        load_output_schema(1),
        load_output_schema(2),
        load_creative_output_schema(),
        load_production_plan_schema(),
        load_intelligence_output_schema(),
        load_commerce_output_schema(),
        load_supervisor_output_schema(),
    ],
    ids=[
        "researcher-v1",
        "researcher-v2",
        "creative-strategist",
        "producer",
        "intelligence",
        "commerce-operations",
        "supervisor",
    ],
)
def test_all_openai_agent_schemas_are_strict_compatible(schema) -> None:
    validate_openai_strict_output_schema(schema)


@pytest.mark.parametrize("keyword", ["oneOf", "allOf", "not", "if"])
def test_unsupported_composition_fails_without_schema_details(keyword: str) -> None:
    marker = "schema-secret-marker"
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["value"],
        "properties": {"value": {keyword: [{"const": marker}]}},
    }
    with pytest.raises(ModelProviderSchemaUnsupported) as caught:
        validate_openai_strict_output_schema(schema)
    assert caught.value.code == "MODEL_PROVIDER_SCHEMA_UNSUPPORTED"
    assert marker not in str(caught.value)


def test_root_any_of_is_rejected() -> None:
    with pytest.raises(ModelProviderSchemaUnsupported):
        validate_openai_strict_output_schema({"anyOf": [{"type": "object"}]})


@pytest.mark.parametrize(
    "mutation",
    [
        lambda schema: schema.pop("additionalProperties"),
        lambda schema: schema.__setitem__("required", []),
    ],
    ids=["missing-additional-properties-false", "optional-property"],
)
def test_strict_object_invariants_are_required(mutation) -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["value"],
        "properties": {"value": {"type": "string"}},
    }
    mutation(schema)
    with pytest.raises(ModelProviderSchemaUnsupported):
        validate_openai_strict_output_schema(schema)


def test_nested_disjoint_any_of_is_supported() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["item"],
        "properties": {
            "item": {
                "anyOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["kind", "value"],
                        "properties": {
                            "kind": {"const": "A"},
                            "value": {"type": "string"},
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["kind", "count"],
                        "properties": {
                            "kind": {"const": "B"},
                            "count": {"type": "integer", "minimum": 0},
                        },
                    },
                ]
            }
        },
    }
    validate_openai_strict_output_schema(schema)


def test_previous_creative_one_of_shape_fails_locally() -> None:
    schema = deepcopy(load_creative_output_schema())
    requirement = schema["$defs"]["asset_requirement"]
    requirement["oneOf"] = requirement.pop("anyOf")
    with pytest.raises(ModelProviderSchemaUnsupported):
        validate_openai_strict_output_schema(schema)
