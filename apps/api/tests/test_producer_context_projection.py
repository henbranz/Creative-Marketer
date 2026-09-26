# mypy: disable-error-code="no-untyped-def,no-untyped-call,index,arg-type"

import json
import socket
from dataclasses import replace
from uuid import uuid4

import pytest

from creative_marketer.agent_runtime.application import (
    ProducerPreparation,
    ResolvedResearcher,
    build_producer_model_context,
    compact_context,
    conservative_producer_input_token_bound,
)
from creative_marketer.agent_runtime.domain import (
    AgentContextBudgetExceeded,
    Citation,
    Confidence,
    Finding,
    FindingCategory,
    ModelInvocationResult,
    ModelUsage,
    ResearchSnapshot,
    canonical_digest,
)
from creative_marketer.agent_runtime.producer_context import (
    InvalidProducerProjection,
    build_producer_provider_projection,
    project_producer_product,
)
from creative_marketer.catalog.domain import ProductKnowledgeSnapshot
from creative_marketer.creative.domain import (
    ApprovedCreativeConcept,
    CreativeConcept,
    CreativeConceptDecision,
    CreativeConceptSet,
    CreativeDecisionState,
    product_claim_refs,
)
from creative_marketer.events.domain import event_sha256_v1
from creative_marketer.infrastructure.model_providers.fake import FakeModelProvider
from creative_marketer.production.domain import ProductionPlanningRequest
from scripts.bootstrap_producer import producer_configuration
from tests.test_agent_runtime_application import context, preparation, service

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _json_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def producer_preparation(
    *,
    product_content: dict[str, object] | None = None,
    extra_findings: int = 1,
    concept_changes: dict[str, object] | None = None,
    product_padding: int = 0,
    concept_padding: int = 0,
    research_padding: int = 0,
    gap_padding: int = 0,
    asset_role_padding: int = 0,
    system_padding: int = 0,
) -> ProducerPreparation:
    tenant_id, product_id, product_snapshot_id = uuid4(), uuid4(), uuid4()
    content = product_content or {
        "brand": {"name": "Atlas", "future_private": "exclude"},
        "brand_profile": {
            "allowed_claims": ["Designed in Europe"],
            "prohibited_claims": ["Guaranteed cure"],
            "future_private": "exclude",
        },
        "product": {
            "name": "Atlas Bottle",
            "category": "Drinkware",
            "short_description": "A repairable daily bottle.",
        },
        "profile": {
            "description": "A matte steel bottle with a replaceable cap.",
            "materials": ["Recycled steel", "Silicone"],
            "features": ["Unrelated future marketing expansion"],
            "allowed_claims": ["Made from recycled steel", "Keeps drinks cool"],
            "prohibited_claims": ["Never leaks"],
            "target_audiences": ["Unrelated audience"],
        },
        "brief": {
            "mandatory_messaging": ["Use responsibly"],
            "prohibited_messaging": ["Medical benefit"],
            "required_disclaimers": ["Results vary"],
            "legal_safety_constraints": ["Do not imply medical use"],
            "geographical_restrictions": ["EU only"],
            "desired_creative_style": "Already frozen in the approved concept",
        },
        "assets": [{"private": "excluded"}],
        "future_private_section": {"internal": "excluded"},
    }
    if product_padding:
        content["incident_unrelated_product_history"] = "x" * product_padding
    product_digest = event_sha256_v1(
        {"schema_version": 2, "source_revision": 7, "content": content}
    )
    product = ProductKnowledgeSnapshot(
        tenant_id=tenant_id,
        product_id=product_id,
        source_revision=7,
        content=content,
        digest=product_digest,
        created_by=uuid4(),
        schema_version=2,
        id=product_snapshot_id,
    )
    claims = product_claim_refs(product.digest, content)
    selected_claim = next(item for item in claims if item.text == "Made from recycled steel")

    research_id = uuid4()
    findings = tuple(
        Finding(
            f"finding_{index}",
            FindingCategory.AUDIENCE,
            f"Referenced production rationale {index}",
            Confidence.HIGH,
            (Citation(uuid4(), index, DIGEST_A),),
            "OBSERVED: synthetic fixture",
            f"Production implication {index}" + ("x" * research_padding if index == 0 else ""),
        )
        for index in range(extra_findings)
    )
    research_semantic = {
        "schema_version": 1,
        "product_snapshot_id": str(product.id),
        "product_snapshot_digest": product.digest,
        "research_context_digest": DIGEST_B,
        "findings": [item.semantic() for item in findings],
        "research_gaps": ["Unrelated gap must not reach Producer" + "x" * gap_padding],
        "recommended_next_sources": [],
    }
    research = ResearchSnapshot(
        tenant_id,
        product_id,
        uuid4(),
        product.id,
        product.digest,
        DIGEST_B,
        findings,
        ("Unrelated gap must not reach Producer" + "x" * gap_padding,),
        (),
        canonical_digest(research_semantic),
        id=research_id,
    )

    concept_payload: dict[str, object] = {
        "title": "The Quiet Frame",
        "creative_angle": "Preserve the approved calm strategy.",
        "strategic_rationale": "Use the exact referenced Research finding.",
        "target_audience": "Daily commuters",
        "hook": {"spoken_or_voiceover": "Pause.", "visual_open": "Still frame"},
        "scenes": [
            {
                "scene_key": "scene_one",
                "ordinal": 1,
                "estimated_duration_seconds": 10,
                "purpose": "Hook",
                "visual_direction": "Show the exact bottle silhouette.",
                "message_point_refs": ["fact"],
                "asset_requirements": [],
            }
        ],
        "cta": {"text": "Discover more", "intent": "DISCOVER"},
        "hypothesis": "A calm opening improves retention.",
        "supporting_research_refs": [
            {"research_snapshot_id": str(research.id), "finding_key": "finding_0"}
        ],
        "message_points": [
            {
                "key": "fact",
                "kind": "PRODUCT_FACT",
                "text": selected_claim.text,
                "product_claim_ref": selected_claim.key,
            }
        ],
        "required_assets": [],
        "required_disclaimers": ["Results vary"],
        "production_notes": "Keep all approved strategy and scenes." + "x" * concept_padding,
    }
    if concept_changes:
        concept_payload.update(concept_changes)
    concept_set_id = uuid4()
    concepts = tuple(
        CreativeConcept(
            tenant_id,
            product_id,
            concept_set_id,
            f"concept_{index}",
            index + 1,
            concept_payload,
            canonical_digest(concept_payload),
        )
        for index in range(3)
    )
    set_semantic = {
        "schema_version": 1,
        "product_snapshot_id": str(product.id),
        "product_snapshot_digest": product.digest,
        "research_snapshot_id": str(research.id),
        "research_snapshot_digest": research.semantic_digest,
        "input_context_digest": DIGEST_A,
        "concepts": [dict(item.payload) for item in concepts],
    }
    concept_set = CreativeConceptSet(
        tenant_id,
        product_id,
        uuid4(),
        product.id,
        product.digest,
        research.id,
        research.semantic_digest,
        DIGEST_A,
        concepts,
        canonical_digest(set_semantic),
        id=concept_set_id,
    )
    decision = CreativeConceptDecision(
        tenant_id,
        product_id,
        concepts[0].id,
        CreativeDecisionState.APPROVED_FOR_PRODUCTION,
        uuid4(),
    )
    cfg = producer_configuration()
    if system_padding:
        cfg = replace(
            cfg,
            system_instructions=cfg.system_instructions + "x" * system_padding,
        )
    return ProducerPreparation(
        ResolvedResearcher(uuid4(), uuid4(), uuid4(), 2, cfg.configuration_digest, cfg),
        ApprovedCreativeConcept(concepts[0], concept_set, decision),
        product,
        research,
        (
            {
                "asset_id": str(uuid4()),
                "kind": "image",
                "role": "product_hero",
                "status": "ready",
                "rights_status": "confirmed",
                "allowed_uses": [
                    "generation_input",
                    *(["x" * asset_role_padding] if asset_role_padding else []),
                ],
                "digest": DIGEST_B,
            },
        ),
    )


def test_product_projection_is_deterministic_bounded_and_preserves_exact_authority() -> None:
    prepared = producer_preparation()
    first, refs = project_producer_product(
        prepared.product_snapshot.content,
        snapshot_digest=prepared.product_snapshot.digest,
        approved_concept=prepared.approved.concept.payload,
    )
    second, second_refs = project_producer_product(
        dict(reversed(tuple(prepared.product_snapshot.content.items()))),
        snapshot_digest=prepared.product_snapshot.digest,
        approved_concept=prepared.approved.concept.payload,
    )
    assert first == second and refs == second_refs
    assert first["approved_claims"] == [{"key": refs[0], "text": "Made from recycled steel"}]
    assert first["safety"] == {
        "brand_prohibited_claims": ["Guaranteed cure"],
        "product_prohibited_claims": ["Never leaks"],
        "mandatory_messaging": ["Use responsibly"],
        "prohibited_messaging": ["Medical benefit"],
        "required_disclaimers": ["Results vary"],
        "legal_safety_constraints": ["Do not imply medical use"],
        "geographical_restrictions": ["EU only"],
    }
    serialized = json.dumps(first)
    assert "future_private" not in serialized
    assert "Unrelated audience" not in serialized
    assert "desired_creative_style" not in serialized
    assert "Keeps drinks cool" not in serialized


def test_projection_fails_closed_for_missing_claim_and_research_references() -> None:
    prepared = producer_preparation()
    bad_claim = dict(prepared.approved.concept.payload)
    bad_claim["message_points"] = [
        {
            "key": "fact",
            "kind": "PRODUCT_FACT",
            "text": "Invented",
            "product_claim_ref": "sha256:" + "f" * 64,
        }
    ]
    with pytest.raises(InvalidProducerProjection, match="claim authority"):
        project_producer_product(
            prepared.product_snapshot.content,
            snapshot_digest=prepared.product_snapshot.digest,
            approved_concept=bad_claim,
        )

    bad_research = dict(prepared.approved.concept.payload)
    bad_research["supporting_research_refs"] = [
        {
            "research_snapshot_id": str(prepared.research_snapshot.id),
            "finding_key": "invented",
        }
    ]
    with pytest.raises(InvalidProducerProjection, match="absent"):
        build_producer_provider_projection(
            product_content=prepared.product_snapshot.content,
            product_snapshot_digest=prepared.product_snapshot.digest,
            approved_concept=bad_research,
            research_snapshot_id=str(prepared.research_snapshot.id),
            research_findings=prepared.research_snapshot.findings,
        )


def test_research_projection_uses_only_approved_references_and_omits_gaps() -> None:
    prepared = producer_preparation(extra_findings=12)
    projection = build_producer_provider_projection(
        product_content=prepared.product_snapshot.content,
        product_snapshot_digest=prepared.product_snapshot.digest,
        approved_concept=prepared.approved.concept.payload,
        research_snapshot_id=str(prepared.research_snapshot.id),
        research_findings=prepared.research_snapshot.findings,
    )
    assert [item["key"] for item in projection.research_findings] == ["finding_0"]
    assert projection.research_finding_refs[0]["digest"] == canonical_digest(
        prepared.research_snapshot.findings[0].semantic()
    )
    assert "citations" not in projection.research_findings[0]
    assert "Unrelated gap" not in str(projection.research_findings)


def test_v2_preserves_full_concept_and_full_source_provenance_while_v1_is_unchanged() -> None:
    prepared = producer_preparation(extra_findings=10)
    planning_v1, legacy = build_producer_model_context(
        prepared, ProductionPlanningRequest(), context_version=1
    )
    planning_v2, projected = build_producer_model_context(
        prepared, ProductionPlanningRequest(), context_version=2
    )
    assert planning_v1 == planning_v2
    assert legacy.context_digest == planning_v1.context_digest
    assert projected.context_digest != legacy.context_digest
    assert legacy.capability_context is not None and projected.capability_context is not None
    assert legacy.capability_context["product_knowledge_snapshot"] == compact_context(
        prepared.product_snapshot.content
    )
    assert len(legacy.capability_context["research_findings"]) == 10
    assert "research_gaps" in legacy.capability_context
    assert "product_knowledge_snapshot" not in projected.capability_context
    assert "research_gaps" not in projected.capability_context
    assert projected.capability_context["approved_creative_concept"] == dict(
        prepared.approved.concept.payload
    )
    assert projected.capability_context["product_snapshot_id"] == str(prepared.product_snapshot.id)
    assert projected.capability_context["product_snapshot_digest"] == (
        prepared.product_snapshot.digest
    )
    assert projected.capability_context["research_snapshot_id"] == str(
        prepared.research_snapshot.id
    )
    assert projected.capability_context["research_snapshot_digest"] == (
        prepared.research_snapshot.semantic_digest
    )
    with pytest.raises(ValueError, match="unsupported Producer context"):
        build_producer_model_context(prepared, ProductionPlanningRequest(), context_version=99)


def test_exact_live_incident_geometry_fails_v1_and_fits_v2_without_losing_authority() -> None:
    prepared = producer_preparation(
        extra_findings=10,
        product_padding=6_356,
        concept_padding=5_444,
        research_padding=1_250,
        gap_padding=843,
        asset_role_padding=14,
        system_padding=28,
    )
    planning_v1, legacy = build_producer_model_context(
        prepared, ProductionPlanningRequest(), context_version=1
    )
    planning_v2, projected = build_producer_model_context(
        prepared, ProductionPlanningRequest(), context_version=2
    )
    assert legacy.capability_context is not None and projected.capability_context is not None
    legacy_sizes = {
        key: _json_bytes(legacy.capability_context[key])
        for key in (
            "product_knowledge_snapshot",
            "approved_creative_concept",
            "research_findings",
            "research_gaps",
            "selected_assets",
        )
    }
    assert legacy_sizes == {
        "product_knowledge_snapshot": 7_371,
        "approved_creative_concept": 6_385,
        "research_findings": 4_971,
        "research_gaps": 884,
        "selected_assets": 226,
    }
    assert conservative_producer_input_token_bound(legacy) == 26_500
    assert conservative_producer_input_token_bound(legacy) > 20_000
    assert conservative_producer_input_token_bound(projected) == 16_129
    assert conservative_producer_input_token_bound(projected) <= 20_000
    assert _json_bytes(projected.capability_context["producer_product"]) == 731
    assert _json_bytes(projected.capability_context["approved_creative_concept"]) == 6_430
    assert _json_bytes(projected.capability_context["referenced_research_findings"]) == 1_440
    assert _json_bytes(projected.capability_context["selected_assets"]) == 226
    assert planning_v1 == planning_v2
    assert projected.capability_context["approved_creative_concept"] == dict(
        prepared.approved.concept.payload
    )
    assert projected.capability_context["producer_product"]["approved_claims"]
    assert projected.capability_context["producer_product"]["safety"]["required_disclaimers"] == [
        "Results vary"
    ]
    assert [
        item["key"]
        for item in projected.capability_context["referenced_research_findings"]  # type: ignore[attr-defined]
    ] == ["finding_0"]
    assert projected.capability_context["product_snapshot_id"] == str(prepared.product_snapshot.id)
    assert projected.capability_context["product_snapshot_digest"] == (
        prepared.product_snapshot.digest
    )
    assert projected.capability_context["research_snapshot_id"] == str(
        prepared.research_snapshot.id
    )
    assert projected.capability_context["research_snapshot_digest"] == (
        prepared.research_snapshot.semantic_digest
    )


@pytest.mark.asyncio
async def test_oversized_v2_denial_is_content_free_and_has_no_run_reservation_event_or_call(
    monkeypatch,
) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("network access forbidden")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    private = "PRIVATE_PRODUCT_SENTINEL"
    base = producer_preparation()
    content = dict(base.product_snapshot.content)
    content["profile"] = {
        **dict(content["profile"]),  # type: ignore[call-overload]
        "description": private * 2_000,
    }
    oversized = producer_preparation(product_content=content)

    provider = FakeModelProvider(
        ModelInvocationResult({}, None, ModelUsage(0, 0, 0), "openai", "gpt-5.6-sol")
    )
    runtime, repository, audit, outbox = service(
        preparation(oversized.product_snapshot.tenant_id, oversized.product_snapshot.product_id),
        provider,
    )
    repository.producer_prepared = oversized
    with pytest.raises(AgentContextBudgetExceeded) as raised:
        await runtime.request_producer(
            context(oversized.product_snapshot.tenant_id),
            concept_id=oversized.approved.concept.id,
            request=ProductionPlanningRequest(),
            idempotency_key="reusable-producer-key",
        )
    diagnostic = raised.value.diagnostics
    assert diagnostic["input_allowance"] == 20_000
    assert diagnostic["fixed_input_bound"] > 20_000  # type: ignore[operator]
    assert diagnostic["projection_version"] == 2
    assert diagnostic["selected_asset_count"] == 1
    assert diagnostic["referenced_product_claim_count"] == 1
    assert diagnostic["referenced_research_finding_count"] == 1
    assert repository.runs == {} and repository.reservations == [] and repository.attempts == {}
    assert provider.calls == [] and outbox.values == []
    assert len(audit.values) == 1
    assert audit.values[0].action == "agent.run.context_budget_denied"
    assert private not in audit.values[0].safe_metadata.canonical_json
    assert _json_bytes(diagnostic) < 4096
