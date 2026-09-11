from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest

from creative_marketer.agent_runtime.domain import canonical_digest
from creative_marketer.creative.application import (
    load_creative_output_schema,
    validate_creative_output,
)
from creative_marketer.creative.domain import (
    ChannelIntent,
    CreativeConceptSet,
    CreativeStrategyContext,
    CreativeStrategyRequest,
    InvalidCreativeAssetReference,
    InvalidCreativeClaimReference,
    InvalidCreativeOutput,
    InvalidCreativeResearchReference,
    ProductClaimRef,
    ProhibitedCreativeClaim,
)


def context() -> CreativeStrategyContext:
    research_id, research_run_id, asset_id = (uuid4() for _ in range(3))
    product = {
        "brief": {"required_disclaimers": ["Results vary"], "prohibited_messaging": ["magic cure"]},
        "profile": {"allowed_claims": ["Made from recycled steel"]},
    }
    claim = ProductClaimRef("sha256:" + "a" * 64, "Made from recycled steel")
    return CreativeStrategyContext(
        uuid4(),
        "sha256:" + "b" * 64,
        2,
        research_id,
        "sha256:" + "c" * 64,
        research_run_id,
        product,
        ({"key": "audience_language", "statement": "People want less waste"},),
        (),
        ({"asset_id": str(asset_id), "status": "ready", "allowed_uses": ["generation_input"]},),
        (claim,),
        CreativeStrategyRequest(3, ChannelIntent.TIKTOK),
        "sha256:" + "d" * 64,
    )


def output(value: CreativeStrategyContext) -> dict[str, Any]:
    concepts = []
    for index in range(3):
        scenes = [
            {
                "ordinal": scene + 1,
                "estimated_duration_seconds": 5,
                "purpose": f"Purpose {scene}",
                "visual_direction": f"Distinct visual {index}-{scene}",
                "on_screen_text": None,
                "voiceover": f"Voice {index}-{scene}",
                "message_point_refs": ["fact"],
                "asset_requirements": [],
            }
            for scene in range(3)
        ]
        concepts.append(
            {
                "concept_key": f"concept_{index}",
                "title": f"Concept {index}",
                "format": "SHORT_FORM_VIDEO",
                "channel_intent": "TIKTOK",
                "creative_angle": f"Angle {index}",
                "strategic_rationale": "Supported by the supplied audience finding.",
                "target_audience": "Waste-conscious shoppers",
                "hook": {
                    "spoken_or_voiceover": f"Hook {index}",
                    "on_screen_text": f"Look {index}",
                    "visual_open": f"Open {index}",
                },
                "estimated_duration_seconds": 15,
                "scenes": scenes,
                "cta": {"text": "Discover more", "intent": "DISCOVER"},
                "hypothesis": "Showing the problem first will improve hook retention.",
                "primary_success_metric": "HOOK_HOLD_RATE",
                "supporting_research_refs": [
                    {
                        "research_snapshot_id": str(value.research_snapshot_id),
                        "finding_key": "audience_language",
                    }
                ],
                "message_points": [
                    {
                        "key": "fact",
                        "kind": "PRODUCT_FACT",
                        "text": "Made from recycled steel",
                        "product_claim_ref": value.product_claims[0].key,
                    }
                ],
                "required_assets": [
                    {
                        "kind": "MISSING_ASSET",
                        "asset_kind": "video",
                        "description": "Hand interaction",
                        "shot_requirement": "Close-up vertical shot",
                        "priority": "HIGH",
                    }
                ],
                "required_disclaimers": ["Results vary"],
                "production_notes": "Shoot in natural light.",
            }
        )
    return {"concepts": concepts, "strategy_limitations": []}


def validate(raw: dict[str, object], value: CreativeStrategyContext) -> CreativeConceptSet:
    return validate_creative_output(
        raw, tenant_id=uuid4(), product_id=uuid4(), agent_run_id=uuid4(), context=value
    )


def test_strict_schema_and_happy_path_with_missing_assets() -> None:
    value = context()
    result = validate(output(value), value)
    assert len(result.concepts) == 3
    assert result.semantic_digest == canonical_digest(result.semantic_content())
    assert load_creative_output_schema()["additionalProperties"] is False


@pytest.mark.parametrize(
    "mutation,error",
    [
        (
            lambda raw: raw["concepts"][0]["supporting_research_refs"][0].update(
                finding_key="invented"
            ),
            InvalidCreativeResearchReference,
        ),
        (
            lambda raw: raw["concepts"][0]["message_points"][0].update(
                product_claim_ref="sha256:" + "f" * 64
            ),
            InvalidCreativeClaimReference,
        ),
        (
            lambda raw: raw["concepts"][0]["hook"].update(on_screen_text="A magic cure"),
            ProhibitedCreativeClaim,
        ),
        (lambda raw: raw["concepts"][1].update(concept_key="concept_0"), InvalidCreativeOutput),
    ],
)
def test_adversarial_output_is_rejected(
    mutation: Callable[[dict[str, Any]], object], error: type[Exception]
) -> None:
    value, raw = context(), None
    raw = output(value)
    mutation(raw)
    with pytest.raises(error):
        validate(raw, value)


def test_unknown_existing_asset_is_rejected() -> None:
    value, raw = context(), None
    raw = output(value)
    raw["concepts"][0]["required_assets"] = [
        {"kind": "EXISTING_ASSET", "asset_id": str(uuid4()), "intended_role": "hero"}
    ]
    with pytest.raises(InvalidCreativeAssetReference):
        validate(raw, value)


def test_extra_model_field_fails_closed() -> None:
    value, raw = context(), None
    raw = output(value)
    raw["tool_request"] = {"name": "publish"}
    with pytest.raises(InvalidCreativeOutput):
        validate(raw, value)
