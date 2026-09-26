# mypy: disable-error-code="no-untyped-def,no-untyped-call,index"

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from creative_marketer.agent_runtime.application import (
    default_capability_registry,
)
from creative_marketer.agent_runtime.domain import ModelProviderSchemaUnsupported
from creative_marketer.creative.application import load_creative_output_schema
from creative_marketer.infrastructure.model_providers.openai_schema import (
    OPENAI_SCHEMA_COMPILER_REVISION,
    audit_openai_schema,
    compile_openai_strict_output_schema,
    normalize_openai_strict_output_schema,
    validate_openai_strict_output_schema,
)
from scripts.openai_contract_audit import audit_contracts


def test_creative_schema_uses_supported_disjoint_any_of() -> None:
    schema = load_creative_output_schema()
    requirement = schema["$defs"]["asset_requirement"]
    assert "oneOf" not in requirement
    assert [branch["properties"]["kind"]["const"] for branch in requirement["anyOf"]] == [
        "EXISTING_ASSET",
        "MISSING_ASSET",
    ]
    validate_openai_strict_output_schema(normalize_openai_strict_output_schema(schema))


@pytest.mark.parametrize(
    "contract",
    default_capability_registry().output_contracts(),
    ids=lambda value: f"{value.agent_type}-v{value.version}",
)
def test_all_registered_openai_agent_schemas_compile_strictly(contract) -> None:
    before = deepcopy(contract.schema)
    compiled = compile_openai_strict_output_schema(
        contract.schema, contract_key=contract.key, contract_version=contract.version
    )
    validate_openai_strict_output_schema(compiled.schema)
    assert contract.schema == before
    assert compiled.compiler_revision == OPENAI_SCHEMA_COMPILER_REVISION
    assert compiled.digest.startswith("sha256:")
    again = compile_openai_strict_output_schema(
        compiled.schema, contract_key=contract.key, contract_version=contract.version
    )
    assert again.schema == compiled.schema
    assert again.digest == compiled.digest


def test_contract_registry_auto_enumerates_all_installed_versions() -> None:
    contracts = default_capability_registry().output_contracts()
    assert len(contracts) == 8
    assert sum(contract.current for contract in contracts) == 6
    assert {(value.key, value.version) for value in contracts} == {
        ("research.research_snapshot", 1),
        ("research.research_snapshot", 2),
        ("creative.creative_concept_set", 1),
        ("production.production_plan", 1),
        ("production.production_plan", 2),
        ("intelligence.intelligence_report", 1),
        ("commerce.operations_report", 1),
        ("orchestration.supervisor_report", 1),
    }


def test_offline_audit_reports_every_contract_without_provider_claim() -> None:
    rows = audit_contracts()
    assert len(rows) == 8
    assert all(row["local_result"] == "ACCEPTED" for row in rows)
    assert all(row["provider_count_result"] == "NOT_RUN" for row in rows)
    assert all(str(row["provider_schema_digest"]).startswith("sha256:") for row in rows)


def test_producer_v2_compiler_expands_exactly_six_partial_object_branches() -> None:
    contract = next(
        value
        for value in default_capability_registry().output_contracts()
        if value.key == "production.production_plan" and value.version == 2
    )
    canonical_audit = audit_openai_schema(contract.schema)
    assert canonical_audit.partial_object_branches == 6
    with pytest.raises(ModelProviderSchemaUnsupported):
        validate_openai_strict_output_schema(contract.schema)

    compiled = compile_openai_strict_output_schema(
        contract.schema, contract_key=contract.key, contract_version=contract.version
    )
    provider_audit = audit_openai_schema(compiled.schema)
    assert provider_audit.partial_object_branches == 0
    assert len(compiled.schema["$defs"]["shot"]["anyOf"]) == 4
    assert len(compiled.schema["$defs"]["segment"]["anyOf"]) == 2
    for definition in ("shot", "segment"):
        canonical = contract.schema["$defs"][definition]
        for branch in compiled.schema["$defs"][definition]["anyOf"]:
            assert branch["type"] == "object"
            assert branch["additionalProperties"] is False
            assert branch["required"] == canonical["required"]
            assert set(branch["properties"]) == set(canonical["properties"])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda schema: schema.pop("$defs"),
        lambda schema: schema["$defs"].pop("shot"),
        lambda schema: schema["$defs"]["shot"]["anyOf"].pop(),
        lambda schema: schema["$defs"]["shot"]["anyOf"][0].__setitem__("extra", True),
        lambda schema: schema["$defs"]["shot"]["anyOf"][0]["properties"].pop(
            "image_generation_spec"
        ),
        lambda schema: schema["$defs"]["shot"]["anyOf"][0]["properties"].__setitem__(
            "source_strategy", {"enum": ["USE_EXISTING_ASSET"]}
        ),
        lambda schema: schema["$defs"]["shot"]["anyOf"][0]["properties"][
            "source_strategy"
        ].__setitem__("const", "UNRECOGNIZED"),
    ],
    ids=[
        "missing-definitions",
        "missing-target-definition",
        "wrong-branch-count",
        "unexpected-branch-key",
        "incomplete-branch-fields",
        "missing-const-discriminator",
        "wrong-discriminator-value",
    ],
)
def test_producer_v2_compiler_fails_closed_on_noncanonical_shape(mutation) -> None:
    contract = next(
        value
        for value in default_capability_registry().output_contracts()
        if value.key == "production.production_plan" and value.version == 2
    )
    malformed = deepcopy(contract.schema)
    mutation(malformed)

    with pytest.raises(ModelProviderSchemaUnsupported):
        compile_openai_strict_output_schema(
            malformed,
            contract_key=contract.key,
            contract_version=contract.version,
        )


def test_compiler_rejects_schema_over_provider_property_limit() -> None:
    properties = {f"value_{index}": {"type": "string"} for index in range(5001)}
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }

    with pytest.raises(ModelProviderSchemaUnsupported):
        compile_openai_strict_output_schema(
            schema,
            contract_key="test.oversized",
            contract_version=1,
        )


@pytest.mark.parametrize("keyword", ["properties", "required", "additionalProperties"])
def test_object_structural_keyword_requires_explicit_object_type(keyword: str) -> None:
    branch: dict[str, object] = {keyword: {} if keyword == "properties" else []}
    if keyword == "additionalProperties":
        branch[keyword] = False
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["value"],
        "properties": {"value": {"anyOf": [branch]}},
    }
    with pytest.raises(ModelProviderSchemaUnsupported):
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
    validate_openai_strict_output_schema(normalize_openai_strict_output_schema(schema))


def test_previous_creative_one_of_shape_fails_locally() -> None:
    schema = deepcopy(load_creative_output_schema())
    requirement = schema["$defs"]["asset_requirement"]
    requirement["oneOf"] = requirement.pop("anyOf")
    with pytest.raises(ModelProviderSchemaUnsupported):
        validate_openai_strict_output_schema(schema)


def test_exact_creative_const_patterns_normalize_without_canonical_mutation():
    canonical = load_creative_output_schema()
    before = deepcopy(canonical)
    with pytest.raises(ModelProviderSchemaUnsupported):
        validate_openai_strict_output_schema(canonical)
    normalized = normalize_openai_strict_output_schema(canonical)
    assert canonical == before
    count = 0

    def compare(original, provider):
        nonlocal count
        if isinstance(original, dict):
            if "const" in original:
                count += 1
                assert "const" not in provider
                assert provider["enum"] == [original["const"]]
                values: tuple[Any, ...] = (
                    original["const"],
                    "wrong-discriminator",
                    None,
                    1,
                    False,
                    [],
                )
                for value in values:
                    assert Draft202012Validator(original).is_valid(value) == Draft202012Validator(
                        provider
                    ).is_valid(value)
            for key, value in original.items():
                if key != "const":
                    compare(value, provider[key])
        elif isinstance(original, list):
            for left, right in zip(original, provider, strict=True):
                compare(left, right)
        else:
            assert original == provider

    compare(canonical, normalized)
    assert count == 3


def test_normalizer_does_not_overwrite_existing_enum_or_rewrite_literal_payloads():
    base = {
        "type": "object",
        "additionalProperties": False,
        "required": ["value"],
        "properties": {"value": {"type": "string", "const": "private-marker", "enum": ["other"]}},
    }
    with pytest.raises(ModelProviderSchemaUnsupported) as caught:
        normalize_openai_strict_output_schema(base)
    assert "private-marker" not in str(caught.value)
    base["properties"]["value"] = {"enum": [{"const": "literal", "uniqueItems": True}]}
    assert normalize_openai_strict_output_schema(base) == base


def test_uniqueness_is_preserved_by_canonical_validation_not_provider_generation():
    canonical = {
        "type": "object",
        "required": ["values"],
        "additionalProperties": False,
        "properties": {
            "values": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 3,
                "uniqueItems": True,
            }
        },
    }
    with pytest.raises(ModelProviderSchemaUnsupported):
        validate_openai_strict_output_schema(canonical)
    provider = normalize_openai_strict_output_schema(canonical)
    assert canonical["properties"]["values"]["uniqueItems"] is True
    assert "uniqueItems" not in provider["properties"]["values"]
    duplicate = {"values": ["same", "same"]}
    assert Draft202012Validator(provider).is_valid(duplicate)
    assert not Draft202012Validator(canonical).is_valid(duplicate)
    invalid_values: tuple[Any, ...] = (
        {"values": []},
        {"values": [1]},
        {"values": ["a", "b", "c", "d"]},
    )
    for invalid in invalid_values:
        assert not Draft202012Validator(provider).is_valid(invalid)
