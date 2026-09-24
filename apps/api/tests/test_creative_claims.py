from copy import deepcopy
from dataclasses import replace
from typing import cast

import pytest
from jsonschema import Draft202012Validator

from creative_marketer.audit.safety import JsonValue, safe_metadata
from creative_marketer.creative.application import (
    creative_output_schema_for_claims,
    load_creative_output_schema,
)
from creative_marketer.creative.domain import (
    CreativeClaimDiagnostic,
    CreativeClaimMismatch,
    InvalidCreativeClaimReference,
    ProductClaimRef,
    product_claim_refs,
)
from creative_marketer.infrastructure.model_providers.openai_schema import (
    normalize_openai_strict_output_schema,
)
from tests.test_creative_strategy import context, output, validate


def test_exact_claim_identity_is_preserved_without_normalization() -> None:
    value = context()
    raw = output(value)
    before = deepcopy(raw)
    result = validate(raw, value)
    assert raw == before
    assert result.concepts[0].payload["message_points"] == before["concepts"][0]["message_points"]


@pytest.mark.parametrize(
    "reference,category",
    [
        ("sha256:" + "f" * 64, "UNKNOWN_REFERENCE"),
        ("SHA256:" + "A" * 64, "REFERENCE_FORMAT_MISMATCH"),
        ("a" * 64, "UNKNOWN_REFERENCE"),
        (" Made from recycled steel ", "UNKNOWN_REFERENCE"),
        ("Made from recycled steel", "CLAIM_TEXT_INSTEAD_OF_ID"),
        (None, "MISSING_REFERENCE"),
        ("sk-do-not-leak-test-secret", "UNKNOWN_REFERENCE"),
    ],
)
def test_bad_claims_fail_closed_with_bounded_diagnostic(
    reference: str | None, category: str
) -> None:
    value = context()
    raw = output(value)
    # Valid earlier concepts must never cause a partial success.
    raw["concepts"][2]["message_points"][0]["product_claim_ref"] = reference
    with pytest.raises(InvalidCreativeClaimReference) as caught:
        validate(raw, value)
    diagnostic = caught.value.diagnostic
    assert diagnostic is not None
    assert diagnostic.allowed_refs == (value.product_claims[0].key,)
    assert len(diagnostic.mismatches) == 1
    assert diagnostic.mismatches[0].category == category
    assert diagnostic.mismatches[0].concept_ordinal == 3
    assert diagnostic.mismatches[0].message_ordinal == 1
    fields = diagnostic.safe_fields()
    assert "Made from recycled steel" not in str(fields)
    assert "sk-do-not-leak" not in str(fields)
    assert str(caught.value) == "Creative claim bindings do not match frozen Product authority"


def test_empty_authority_is_not_filled_from_brief_research_or_product_description() -> None:
    value = context()
    raw = output(value)
    product = {"profile": {"allowed_claims": []}, "brief": {"benefits": ["Unapproved fact"]}}
    value = replace(value, product_claims=(), product_context=product)
    assert product_claim_refs(value.product_snapshot_digest, product) == ()
    for concept in raw["concepts"]:
        concept["message_points"][0]["product_claim_ref"] = None
    with pytest.raises(InvalidCreativeClaimReference) as caught:
        validate(raw, value)
    assert caught.value.diagnostic is not None
    assert {x.category for x in caught.value.diagnostic.mismatches} == {"NO_ALLOWED_CLAIMS"}
    assert caught.value.diagnostic.allowed_refs == ()
    # Non-factual CTA is valid; there is no automatic relabeling in production code.
    for concept in raw["concepts"]:
        concept["message_points"][0].update(kind="CTA", text="Discover more")
    assert len(validate(raw, value).concepts) == 3


def test_non_fact_and_missing_disclaimer_are_distinct_diagnostics() -> None:
    value = context()
    raw = output(value)
    raw["concepts"][0]["message_points"][0]["kind"] = "CTA"
    raw["concepts"][1]["required_disclaimers"] = []
    with pytest.raises(InvalidCreativeClaimReference) as caught:
        validate(raw, value)
    assert caught.value.diagnostic is not None
    assert [x.category for x in caught.value.diagnostic.mismatches] == [
        "REFERENCE_ON_NON_FACT",
        "MISSING_REQUIRED_DISCLAIMERS",
    ]


def test_diagnostics_are_bounded_and_exclude_content() -> None:
    value = context()
    refs = tuple("sha256:" + f"{i:064x}" for i in range(100))
    mismatches = tuple(
        CreativeClaimMismatch(
            i // 10 + 1, i % 10 + 1, "REFERENCE_FORMAT_MISMATCH", "SHA256:" + "A" * 64
        )
        for i in range(50)
    )
    diagnostic = CreativeClaimDiagnostic(value.product_snapshot_id, refs, mismatches)
    fields = diagnostic.safe_fields()
    assert fields["mismatch_count"] == 50
    assert fields["allowed_reference_count"] == 100
    assert fields["mismatches_truncated"] is True
    assert fields["allowed_references_truncated"] is True
    assert len(safe_metadata(cast(dict[str, JsonValue], fields)).canonical_json.encode()) < 4096


@pytest.mark.parametrize("has_claims", [False, True])
def test_bound_generation_schema_is_stricter_without_mutating_canonical(has_claims: bool) -> None:
    value = context()
    canonical = deepcopy(load_creative_output_schema())
    keys = tuple(x.key for x in value.product_claims) if has_claims else ()
    schema = creative_output_schema_for_claims(keys)
    Draft202012Validator.check_schema(schema)
    normalized = normalize_openai_strict_output_schema(schema)
    validator = Draft202012Validator(normalized)
    raw = output(value)
    assert validator.is_valid(raw) is has_claims
    raw["concepts"][0]["message_points"][0]["product_claim_ref"] = "sha256:" + "f" * 64
    assert not validator.is_valid(raw)
    for concept in raw["concepts"]:
        concept["message_points"][0].update(
            kind="CTA", text="Discover more", product_claim_ref=None
        )
    assert validator.is_valid(raw)
    raw["concepts"][0]["message_points"][0]["product_claim_ref"] = value.product_claims[0].key
    assert not validator.is_valid(raw)
    assert load_creative_output_schema() == canonical


def test_claim_identity_stays_snapshot_bound_even_for_same_text() -> None:
    product = {"profile": {"allowed_claims": ["Made from recycled steel"]}}
    first = product_claim_refs("sha256:" + "a" * 64, product)
    other = product_claim_refs("sha256:" + "b" * 64, product)
    assert first[0].key != other[0].key
    value = replace(context(), product_claims=(ProductClaimRef(first[0].key, first[0].text),))
    raw = output(value)
    raw["concepts"][0]["message_points"][0]["product_claim_ref"] = other[0].key
    with pytest.raises(InvalidCreativeClaimReference):
        validate(raw, value)
