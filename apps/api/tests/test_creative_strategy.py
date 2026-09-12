from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from creative_marketer.agent_runtime.domain import canonical_digest
from creative_marketer.creative.application import (
    CreativeService,
    build_creative_context,
    load_creative_output_schema,
    validate_creative_output,
)
from creative_marketer.creative.domain import (
    ApprovedCreativeConcept,
    ChannelIntent,
    CreativeConceptDecision,
    CreativeConceptSet,
    CreativeDecisionState,
    CreativeNotFound,
    CreativePermissionDenied,
    CreativeStrategyContext,
    CreativeStrategyRequest,
    InvalidCreativeAssetReference,
    InvalidCreativeClaimReference,
    InvalidCreativeOutput,
    InvalidCreativeResearchReference,
    ProductClaimRef,
    ProhibitedCreativeClaim,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus


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


@pytest.mark.parametrize(
    "mutation,error",
    [
        (lambda raw: raw["concepts"].pop(), InvalidCreativeOutput),
        (
            lambda raw: raw["concepts"][0]["message_points"][0].update(kind="VALUE_PROPOSITION"),
            InvalidCreativeClaimReference,
        ),
        (
            lambda raw: raw["concepts"][0].update(required_disclaimers=[]),
            InvalidCreativeClaimReference,
        ),
        (
            lambda raw: raw["concepts"][0].update(estimated_duration_seconds=14),
            InvalidCreativeOutput,
        ),
    ],
)
def test_contract_semantics_fail_closed(
    mutation: Callable[[dict[str, Any]], object], error: type[Exception]
) -> None:
    value, raw = context(), None
    raw = output(value)
    mutation(raw)
    with pytest.raises(error):
        validate(raw, value)


def test_existing_asset_rights_shape_fails_closed() -> None:
    value, raw = context(), None
    raw = output(value)
    asset_id = value.asset_manifest[0]["asset_id"]
    value.asset_manifest[0]["allowed_uses"] = "generation_input"  # type: ignore[index]
    raw["concepts"][0]["required_assets"] = [
        {"kind": "EXISTING_ASSET", "asset_id": asset_id, "intended_role": "hero"}
    ]
    with pytest.raises(InvalidCreativeAssetReference):
        validate(raw, value)


def test_schema_valid_wrong_concept_count_is_rejected() -> None:
    value, raw = context(), None
    raw = output(value)
    extra = deepcopy(raw["concepts"][-1])
    extra.update(concept_key="concept_3", title="Concept 3")
    extra["hook"].update(
        spoken_or_voiceover="Hook 3", on_screen_text="Look 3", visual_open="Open 3"
    )
    extra["scenes"][0]["visual_direction"] = "Distinct visual 3"
    raw["concepts"].append(extra)
    with pytest.raises(InvalidCreativeOutput, match="wrong concept count"):
        validate(raw, value)


def test_creative_value_objects_reject_invalid_identity_and_handoff() -> None:
    value = context()
    result = validate(output(value), value)
    concept = result.concepts[0]
    with pytest.raises(ValueError):
        CreativeStrategyRequest(2)
    with pytest.raises(InvalidCreativeOutput):
        replace(concept, concept_key="INVALID")
    with pytest.raises(InvalidCreativeOutput):
        replace(concept, semantic_digest="sha256:" + "0" * 64)
    with pytest.raises(InvalidCreativeOutput):
        replace(result, concepts=result.concepts[:2])
    with pytest.raises(InvalidCreativeOutput):
        replace(result, id=uuid4())
    with pytest.raises(InvalidCreativeOutput):
        replace(result, semantic_digest="sha256:" + "0" * 64)
    with pytest.raises(ValueError):
        CreativeConceptDecision(
            result.tenant_id,
            result.product_id,
            concept.id,
            CreativeDecisionState.REJECTED,
            uuid4(),
            reason_code="invalid",
        )
    with pytest.raises(ValueError):
        CreativeConceptDecision(
            result.tenant_id,
            result.product_id,
            concept.id,
            CreativeDecisionState.REJECTED,
            uuid4(),
            note="x" * 1001,
        )
    rejected = CreativeConceptDecision(
        result.tenant_id,
        result.product_id,
        concept.id,
        CreativeDecisionState.REJECTED,
        uuid4(),
    )
    with pytest.raises(ValueError):
        ApprovedCreativeConcept(concept, result, rejected)


@pytest.mark.asyncio
async def test_creative_service_fails_closed_for_missing_values_and_non_admins() -> None:
    class Repository:
        async def get_set(self, _set_id: object) -> None:
            return None

        async def get_concept(self, _concept_id: object, *, for_update: bool = False) -> None:
            return None

    class UnitOfWork:
        creative = Repository()

        async def __aenter__(self) -> "UnitOfWork":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    service = CreativeService(lambda _tenant_id: UnitOfWork())  # type: ignore[arg-type]
    member: Any = SimpleNamespace(
        tenant_id=uuid4(),
        membership_status=MembershipStatus.ACTIVE,
        membership_role=MembershipRole.MEMBER,
        user_id=uuid4(),
    )
    with pytest.raises(CreativeNotFound):
        await service.get_set(member, uuid4())
    with pytest.raises(CreativeNotFound):
        await service.get_concept(member, uuid4())
    with pytest.raises(CreativePermissionDenied):
        await service.decide(member, uuid4(), CreativeDecisionState.REJECTED)
    member.membership_role = MembershipRole.OWNER
    with pytest.raises(CreativeNotFound):
        await service.decide(member, uuid4(), CreativeDecisionState.REJECTED)


def test_creative_context_requires_product_snapshot_v2() -> None:
    with pytest.raises(InvalidCreativeOutput):
        build_creative_context(
            SimpleNamespace(schema_version=1),  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            CreativeStrategyRequest(),
        )
