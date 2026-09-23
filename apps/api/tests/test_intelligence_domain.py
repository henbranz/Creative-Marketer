from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from creative_marketer.agent_runtime.domain import canonical_digest
from creative_marketer.intelligence.application import (
    reject_unsupported_numeric_claims,
    validate_intelligence_output,
)
from creative_marketer.intelligence.domain import (
    CALCULATION_VERSION,
    COMPARABILITY_VERSION,
    CanonicalRef,
    ComparableSnapshot,
    Confidence,
    CreativeFeatureSnapshot,
    DataTrustLevel,
    ExperimentProposal,
    InsightCandidate,
    InsightDecision,
    InsightDecisionKind,
    InsightStatus,
    InsightTransitionDenied,
    IntelligenceContextManifest,
    IntelligenceReport,
    InvalidIntelligenceOutput,
    build_comparisons,
    current_insight_status,
    reject_unsafe_claims,
)


def comparable(
    *,
    tenant: UUID | None = None,
    product: UUID | None = None,
    platform: str = "instagram",
    window: str = "+24h",
    value: str = "1",
    trust: DataTrustLevel = DataTrustLevel.OBSERVED,
) -> ComparableSnapshot:
    return ComparableSnapshot(
        uuid4(),
        "sha256:" + "a" * 64,
        uuid4(),
        uuid4(),
        tenant or uuid4(),
        product or uuid4(),
        platform,
        "VIDEO",
        window,
        {"ctr": Decimal(value)},
        trust,
    )


def manifest(
    tenant: UUID,
    product: UUID,
    comparison_ids: tuple[UUID, ...] = (),
    trust: DataTrustLevel = DataTrustLevel.OBSERVED,
) -> IntelligenceContextManifest:
    product_ref = CanonicalRef("product_knowledge_snapshot", uuid4(), "sha256:" + "b" * 64)
    content = {
        "product_snapshot": product_ref.primitive(),
        "research_snapshot": None,
        "creative_concepts": [],
        "final_creatives": [],
        "publications": [],
        "performance_snapshots": [],
        "attribution_results": [],
        "comparison_ids": [str(item) for item in comparison_ids],
        "feature_snapshot_ids": [],
        "comparison_policy_version": COMPARABILITY_VERSION,
        "feature_extraction_version": "creative-features-v1",
        "data_trust_level": trust.value,
    }
    return IntelligenceContextManifest(
        tenant,
        product,
        product_ref,
        None,
        (),
        (),
        (),
        (),
        (),
        tuple(comparison_ids),
        (),
        trust,
        canonical_digest(content),
    )


def test_comparability_requires_same_product_platform_window_and_metric() -> None:
    tenant, product = uuid4(), uuid4()
    subject = comparable(tenant=tenant, product=product, value="5")
    valid = [comparable(tenant=tenant, product=product, value=value) for value in ("1", "2", "9")]
    assert len(build_comparisons(subject, valid)) == 1
    for excluded in (
        comparable(tenant=tenant, product=product, platform="tiktok"),
        comparable(tenant=tenant, product=product, window="+1h"),
        comparable(tenant=tenant, product=uuid4()),
    ):
        assert build_comparisons(subject, [*valid[:2], excluded]) == ()


def test_baseline_is_median_and_decimal_deltas_are_exact() -> None:
    tenant, product = uuid4(), uuid4()
    subject = comparable(tenant=tenant, product=product, value="4")
    result = build_comparisons(
        subject,
        [comparable(tenant=tenant, product=product, value=value) for value in ("1", "2", "9")],
    )[0]
    assert result.baseline_value == Decimal("2")
    assert result.absolute_delta == Decimal("2")
    assert result.relative_delta == Decimal("1.000000")
    assert result.sample_size == 3
    assert result.calculation_version == CALCULATION_VERSION


def test_too_few_or_missing_metrics_produces_no_baseline_and_zero_is_safe() -> None:
    tenant, product = uuid4(), uuid4()
    subject = comparable(tenant=tenant, product=product, value="4")
    assert build_comparisons(subject, [comparable(tenant=tenant, product=product)] * 2) == ()
    zeroes = [comparable(tenant=tenant, product=product, value="0") for _ in range(3)]
    assert build_comparisons(subject, zeroes)[0].relative_delta is None
    missing = replace(comparable(tenant=tenant, product=product), metrics={"views": Decimal("1")})
    assert build_comparisons(subject, [missing, missing, missing]) == ()


def test_synthetic_trust_propagates_from_any_baseline() -> None:
    tenant, product = uuid4(), uuid4()
    subject = comparable(tenant=tenant, product=product)
    population = [comparable(tenant=tenant, product=product) for _ in range(2)] + [
        comparable(tenant=tenant, product=product, trust=DataTrustLevel.SYNTHETIC)
    ]
    assert build_comparisons(subject, population)[0].data_trust_level is DataTrustLevel.SYNTHETIC


def test_feature_extraction_is_deterministic_and_does_not_infer_pixels() -> None:
    ids = (uuid4(), uuid4(), uuid4(), uuid4())
    first = CreativeFeatureSnapshot.extract(
        tenant_id=ids[0],
        product_id=ids[1],
        publication_id=ids[2],
        final_creative_id=ids[3],
        concept={"creative_angle": "demo", "scenes": [{}, {}], "cta": "Learn more"},
        production_plan={"product_visibility_strategy": "FIRST_SCENE"},
        final_creative={"duration_seconds": "12.5", "aspect_ratio": "9:16"},
        publication={"platform": "instagram", "caption": "Hello"},
    )
    second = CreativeFeatureSnapshot.extract(
        tenant_id=ids[0],
        product_id=ids[1],
        publication_id=ids[2],
        final_creative_id=ids[3],
        concept={"creative_angle": "demo", "scenes": [{}, {}], "cta": "Learn more"},
        production_plan={"product_visibility_strategy": "FIRST_SCENE"},
        final_creative={"duration_seconds": "12.5", "aspect_ratio": "9:16"},
        publication={"platform": "instagram", "caption": "Hello"},
    )
    assert first.semantic_digest == second.semantic_digest
    assert first.features["duration_seconds"] == "12.5"
    assert "person_present" not in first.features
    assert "lighting" not in first.features


@pytest.mark.parametrize(
    "claim",
    [
        "This caused the increase",
        "A proven winner",
        "Guaranteed conversion",
        "Increase ad budget",
        "Optimize ROAS",
    ],
)
def test_unsafe_causality_and_spend_claims_are_rejected(claim: str) -> None:
    with pytest.raises(InvalidIntelligenceOutput):
        reject_unsafe_claims(claim)


def test_numeric_claims_must_exist_in_deterministic_grounding() -> None:
    grounding = {"performance_comparisons": [{"observed_value": "4.8", "sample_size": 4}]}
    reject_unsupported_numeric_claims({"summary": "Observed 4.8 across 4 samples"}, grounding)
    with pytest.raises(InvalidIntelligenceOutput):
        reject_unsupported_numeric_claims({"summary": "Observed 9.9"}, grounding)


def test_structured_output_binds_exact_comparison_and_caps_synthetic_confidence() -> None:
    tenant, product = uuid4(), uuid4()
    subject = comparable(tenant=tenant, product=product, value="4", trust=DataTrustLevel.SYNTHETIC)
    comparison = build_comparisons(
        subject,
        [comparable(tenant=tenant, product=product, value=value) for value in ("1", "2", "3")],
    )[0]
    value = validate_intelligence_output(
        {
            "summary": "A possible pattern is worth testing.",
            "observations": [
                {
                    "statement": "Observed metric is available.",
                    "source_ref": "snapshot",
                    "window": "+24h",
                }
            ],
            "comparative_findings": [
                {
                    "comparison_id": str(comparison.id),
                    "interpretation": "This may indicate a testable pattern.",
                }
            ],
            "insight_candidates": [
                {
                    "statement": "A shorter opening may be worth testing.",
                    "comparison_ids": [str(comparison.id)],
                    "metric": "ctr",
                    "confidence": "HIGH",
                    "scope": {"dimensions": [{"key": "platform", "value": "instagram"}]},
                    "limitations": ["Observational only."],
                }
            ],
            "limitations": [
                "Synthetic source data; not real market evidence.",
                "Observational only.",
            ],
            "next_experiments": [
                {
                    "candidate_indexes": [0],
                    "hypothesis": "A shorter opening may improve engagement.",
                    "primary_variable": "opening duration",
                    "controlled_elements": ["CTA", "caption"],
                    "target_metric": "ctr",
                    "platform": "instagram",
                    "recommended_measurement_window": "+24h",
                    "creative_direction": "Create two otherwise equivalent openings.",
                    "rationale": "Isolate the opening duration.",
                    "expected_learning": "Whether opening duration is worth further testing.",
                }
            ],
        },
        tenant_id=tenant,
        product_id=product,
        agent_run_id=uuid4(),
        agent_version_id=uuid4(),
        manifest=manifest(tenant, product, (comparison.id,), DataTrustLevel.SYNTHETIC),
        comparisons=(comparison,),
    )
    assert value.candidates[0].confidence is Confidence.LOW
    assert value.candidates[0].status is InsightStatus.CANDIDATE
    assert value.proposals[0].source_intelligence_report_id == value.report.id


def test_single_unmatched_publication_can_report_without_candidate_or_experiment() -> None:
    tenant, product = uuid4(), uuid4()
    value = validate_intelligence_output(
        {
            "summary": "One publication is available.",
            "observations": [
                {
                    "statement": "A single snapshot was observed.",
                    "source_ref": "snapshot",
                    "window": "UNMATCHED",
                }
            ],
            "comparative_findings": [],
            "insight_candidates": [],
            "limitations": ["Insufficient comparable matched-window sample."],
            "next_experiments": [],
        },
        tenant_id=tenant,
        product_id=product,
        agent_run_id=uuid4(),
        agent_version_id=uuid4(),
        manifest=manifest(tenant, product),
        comparisons=(),
    )
    assert value.candidates == () and value.proposals == ()


def test_decision_only_moves_candidate_to_proposed_or_rejected() -> None:
    tenant, product, report = uuid4(), uuid4(), uuid4()
    evidence = CanonicalRef("performance_comparison", uuid4(), "sha256:" + "c" * 64)
    content = {
        "report_id": str(report),
        "statement": "Worth testing",
        "evidence_refs": [evidence.primitive()],
        "sample_size": 3,
        "metric": "ctr",
        "baseline": "1",
        "observed_delta": "1",
        "confidence": "LOW",
        "scope": {},
        "limitations": ["Observational"],
        "data_trust_level": "OBSERVED",
    }
    candidate = InsightCandidate(
        tenant,
        product,
        report,
        "Worth testing",
        (evidence,),
        3,
        "ctr",
        Decimal("1"),
        Decimal("1"),
        Confidence.LOW,
        {},
        ("Observational",),
        DataTrustLevel.OBSERVED,
        canonical_digest(content),
    )
    decision = InsightDecision(
        tenant,
        product,
        candidate.id,
        candidate.semantic_digest,
        InsightDecisionKind.PROPOSE_FOR_TESTING,
        uuid4(),
    )
    assert current_insight_status(candidate, (decision,)) is InsightStatus.PROPOSED
    with pytest.raises(InsightTransitionDenied):
        replace(candidate, status=InsightStatus.VALIDATED)


def test_immutable_intelligence_value_guards_fail_closed() -> None:
    with pytest.raises(ValueError, match="reference digest"):
        CanonicalRef("bad", uuid4(), "not-a-digest")

    ids = (uuid4(), uuid4(), uuid4(), uuid4())
    feature = CreativeFeatureSnapshot.extract(
        tenant_id=ids[0],
        product_id=ids[1],
        publication_id=ids[2],
        final_creative_id=ids[3],
        concept={"hook": {"type": "question"}, "cta": {"type": "discover"}},
    )
    assert feature.features["hook_type"] == "question"
    assert feature.features["cta_type"] == "discover"
    with pytest.raises(ValueError, match="not canonical"):
        replace(feature, extraction_version="invented-v2")

    tenant, product = uuid4(), uuid4()
    subject = comparable(tenant=tenant, product=product, value="4")
    with pytest.raises(ValueError, match="comparable snapshot"):
        replace(subject, window="+2h")
    comparison = build_comparisons(
        subject,
        [comparable(tenant=tenant, product=product, value=value) for value in ("1", "2", "3")],
    )[0]
    for values in (
        {"sample_size": 2},
        {"absolute_delta": Decimal("99")},
        {"relative_delta": Decimal("99")},
        {"semantic_digest": "sha256:" + "0" * 64},
    ):
        with pytest.raises(ValueError):
            replace(comparison, **values)

    exact_manifest = manifest(tenant, product)
    with pytest.raises(ValueError, match="manifest digest"):
        replace(exact_manifest, semantic_digest="sha256:" + "0" * 64)

    with pytest.raises(InvalidIntelligenceOutput, match="primary variable"):
        ExperimentProposal(
            tenant,
            product,
            uuid4(),
            "sha256:" + "a" * 64,
            (),
            "Worth testing",
            " ",
            (),
            "ctr",
            "instagram",
            "+24h",
            "Direction",
            "Rationale",
            "Learning",
            DataTrustLevel.OBSERVED,
            "sha256:" + "b" * 64,
        )


def test_candidate_status_without_decisions_and_stale_decision_binding() -> None:
    tenant, product, report = uuid4(), uuid4(), uuid4()
    evidence = CanonicalRef("performance_comparison", uuid4(), "sha256:" + "c" * 64)
    content = {
        "report_id": str(report),
        "statement": "Worth testing",
        "evidence_refs": [evidence.primitive()],
        "sample_size": 3,
        "metric": "ctr",
        "baseline": "1",
        "observed_delta": "1",
        "confidence": "LOW",
        "scope": {},
        "limitations": ["Observational"],
        "data_trust_level": "OBSERVED",
    }
    candidate = InsightCandidate(
        tenant,
        product,
        report,
        "Worth testing",
        (evidence,),
        3,
        "ctr",
        Decimal("1"),
        Decimal("1"),
        Confidence.LOW,
        {},
        ("Observational",),
        DataTrustLevel.OBSERVED,
        canonical_digest(content),
    )
    assert current_insight_status(candidate, ()) is InsightStatus.CANDIDATE
    stale = InsightDecision(
        tenant,
        product,
        uuid4(),
        candidate.semantic_digest,
        InsightDecisionKind.REJECT,
        uuid4(),
    )
    with pytest.raises(InsightTransitionDenied, match="exact candidate"):
        current_insight_status(candidate, (stale,))


def test_report_and_candidate_quality_guards_are_enforced() -> None:
    tenant, product, run_id, version_id, manifest_id = (uuid4() for _ in range(5))
    manifest_digest = "sha256:" + "a" * 64
    with pytest.raises(InvalidIntelligenceOutput, match="requires limitations"):
        IntelligenceReport(
            tenant,
            product,
            run_id,
            version_id,
            manifest_id,
            manifest_digest,
            DataTrustLevel.OBSERVED,
            "Summary",
            (),
            (),
            (),
            "sha256:" + "b" * 64,
        )
    with pytest.raises(InvalidIntelligenceOutput, match="synthetic source"):
        IntelligenceReport(
            tenant,
            product,
            run_id,
            version_id,
            manifest_id,
            manifest_digest,
            DataTrustLevel.SYNTHETIC,
            "Summary",
            (),
            (),
            ("Observational only.",),
            "sha256:" + "b" * 64,
        )
    with pytest.raises(InvalidIntelligenceOutput, match="report digest"):
        IntelligenceReport(
            tenant,
            product,
            run_id,
            version_id,
            manifest_id,
            manifest_digest,
            DataTrustLevel.OBSERVED,
            "Summary",
            (),
            (),
            ("Observational only.",),
            "sha256:" + "b" * 64,
        )

    evidence = CanonicalRef("performance_comparison", uuid4(), "sha256:" + "c" * 64)
    base: Any = dict(
        tenant_id=tenant,
        product_id=product,
        report_id=uuid4(),
        statement="Worth testing",
        evidence_refs=(evidence,),
        sample_size=3,
        metric="ctr",
        baseline=Decimal("1"),
        observed_delta=Decimal("1"),
        confidence=Confidence.HIGH,
        scope={},
        limitations=("Observational",),
        data_trust_level=DataTrustLevel.SYNTHETIC,
        semantic_digest="sha256:" + "d" * 64,
    )
    with pytest.raises(InvalidIntelligenceOutput, match="caps confidence"):
        InsightCandidate(**base)
    with pytest.raises(InvalidIntelligenceOutput, match="small samples"):
        InsightCandidate(
            **{
                **base,
                "sample_size": 2,
                "data_trust_level": DataTrustLevel.OBSERVED,
            }
        )
    with pytest.raises(InvalidIntelligenceOutput, match="evidence and limitations"):
        InsightCandidate(
            **{
                **base,
                "evidence_refs": (),
                "confidence": Confidence.LOW,
                "data_trust_level": DataTrustLevel.OBSERVED,
            }
        )
    with pytest.raises(InvalidIntelligenceOutput, match="candidate digest"):
        InsightCandidate(
            **{
                **base,
                "confidence": Confidence.LOW,
                "data_trust_level": DataTrustLevel.OBSERVED,
            }
        )
