# mypy: disable-error-code="arg-type,var-annotated"

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from creative_marketer.agent_runtime.application import ModelRouter, select_evidence_blocks
from creative_marketer.agent_runtime.domain import (
    AgentRun,
    Citation,
    Confidence,
    EvidenceBlockRef,
    Finding,
    FindingCategory,
    InvalidModelOutput,
    InvalidResearchCitation,
    ModelAttempt,
    ModelCapabilityUnavailable,
    ModelContext,
    ModelPricing,
    ModelRoute,
    ModelRouteUnavailable,
    ModelUsage,
    RecommendedSource,
    canonical_digest,
    parse_research_output,
)
from creative_marketer.research.domain import (
    EvidenceBlock,
    EvidenceBlockKind,
    EvidenceSnapshot,
    ResearchCategory,
    research_sha256_v1,
)


def route() -> ModelRoute:
    return ModelRoute(
        "research_balanced",
        "route.v1",
        "openai",
        "gpt-5.6-sol",
        frozenset({"text", "reasoning", "structured_output"}),
        "medium",
        6000,
        ModelPricing("prices.v1", Decimal("4"), Decimal("20"), "USD"),
    )


def run() -> AgentRun:
    digest = "sha256:" + "a" * 64
    return AgentRun(
        tenant_id=uuid4(),
        requested_agent_definition_id=uuid4(),
        resolved_agent_definition_id=uuid4(),
        agent_version_id=uuid4(),
        agent_version_number=1,
        agent_configuration_digest=digest,
        prompt_revision="researcher.v1",
        product_id=uuid4(),
        product_snapshot_id=uuid4(),
        product_snapshot_digest=digest,
        product_snapshot_schema_version=1,
        research_context_digest=digest,
        context_digest=digest,
        selected_evidence=({"block_index": 0},),
        model_profile_key="research_balanced",
        output_contract_key="research.research_snapshot",
        output_contract_version=1,
        correlation_id=uuid4(),
        initiated_by_actor_kind="user",
        initiated_by_actor_id=uuid4(),
        period_start=datetime.now(UTC),
        reserved_cost=Decimal("0.15"),
        currency="USD",
        idempotency_key="request-1",
        max_total_tokens=12000,
    )


def block(*, digest: str | None = None) -> EvidenceBlockRef:
    return EvidenceBlockRef(
        uuid4(),
        uuid4(),
        "Competitor page",
        0,
        "paragraph",
        digest or canonical_digest({"evidence": "claim"}),
        "The advertised price is $20.",
    )


def output(reference: EvidenceBlockRef) -> dict[str, object]:
    return {
        "findings": [
            {
                "key": "competitor_price",
                "category": FindingCategory.PRICING.value,
                "statement": "The competitor advertises a $20 price.",
                "confidence": Confidence.HIGH.value,
                "citations": [
                    {
                        "evidence_snapshot_id": str(reference.evidence_snapshot_id),
                        "block_index": reference.block_index,
                        "block_digest": reference.block_digest,
                    }
                ],
                "scope": "Captured competitor page",
                "implication": "Compare offer framing.",
            }
        ],
        "research_gaps": ["Shipping terms are unknown."],
        "recommended_next_sources": [
            {
                "category": "pricing",
                "reason": "Confirm shipping",
                "suggested_query": "competitor shipping terms",
            }
        ],
    }


def evidence(category: ResearchCategory, captured_at: datetime, text: str) -> EvidenceSnapshot:
    item = EvidenceBlock(EvidenceBlockKind.PARAGRAPH, text, 0)
    arguments = {
        "schema_version": 1,
        "extractor_version": "html-v1",
        "final_url": "https://example.com/",
        "title": "Example",
        "blocks": (item,),
        "outbound_links": (),
        "structured_metadata": {},
        "instruction_like_content": False,
    }
    return EvidenceSnapshot(
        tenant_id=uuid4(),
        product_id=uuid4(),
        source_id=uuid4(),
        source_fetch_id=uuid4(),
        raw_digest="sha256:" + "b" * 64,
        semantic_digest=research_sha256_v1(
            {
                **arguments,
                "blocks": [item.semantic()],
                "outbound_links": [],
            }
        ),
        captured_at=captured_at,
        **arguments,
    )


def test_pricing_routing_and_usage_are_bounded() -> None:
    assert route().pricing.cost(1000, 500) == Decimal("0.014000")
    router = ModelRouter((route(),))
    assert router.resolve("research_balanced", ("text",)).model == "gpt-5.6-sol"
    with pytest.raises(ModelRouteUnavailable):
        router.resolve("missing", ("text",))
    with pytest.raises(ModelCapabilityUnavailable):
        router.resolve("research_balanced", ("vision",))
    with pytest.raises(ValueError):
        ModelRouter((route(), route()))
    with pytest.raises(ValueError):
        replace(route().pricing, currency="usd")
    with pytest.raises(ValueError):
        replace(route(), max_output_tokens=0)
    with pytest.raises(ValueError):
        ModelUsage(4, 3, 6)


def test_context_selection_is_deterministic_prioritized_stale_and_bounded() -> None:
    now = datetime.now(UTC)
    recent = evidence(ResearchCategory.PRICING, now, "new")
    stale = evidence(ResearchCategory.COMPETITOR, now - timedelta(days=31), "old")
    values = select_evidence_blocks(
        (
            (recent, ResearchCategory.PRICING, "Recent"),
            (stale, ResearchCategory.COMPETITOR, "Stale competitor"),
        ),
        now=now,
    )
    assert [item.text for item in values] == ["old", "new"]
    assert values[0].stale is True
    assert values[1].stale is False
    assert (
        len(
            select_evidence_blocks(
                tuple((recent, ResearchCategory.PRICING, str(i)) for i in range(30))
            )
        )
        == 20
    )


def test_research_output_requires_exact_frozen_citations_and_is_immutable() -> None:
    reference = block()
    snapshot = parse_research_output(output(reference), run=run(), selected_blocks=(reference,))
    assert snapshot.findings[0].confidence is Confidence.HIGH
    assert snapshot.semantic_digest == canonical_digest(snapshot.semantic_content())
    with pytest.raises(FrozenInstanceError):
        snapshot.findings[0].statement = "changed"  # type: ignore[misc]

    fabricated = output(reference)
    fabricated["findings"][0]["citations"][0]["block_digest"] = "sha256:" + "f" * 64  # type: ignore[index]
    with pytest.raises(InvalidResearchCitation):
        parse_research_output(fabricated, run=run(), selected_blocks=(reference,))

    for invalid in (
        {},
        {"findings": [], "research_gaps": [], "recommended_next_sources": []},
        {
            **output(reference),
            "findings": [output(reference)["findings"][0]] * 31,  # type: ignore[index]
        },
    ):
        with pytest.raises(InvalidModelOutput):
            parse_research_output(invalid, run=run(), selected_blocks=(reference,))


def test_runtime_entities_reject_tampering() -> None:
    digest = canonical_digest({"valid": True})
    assert block().identity()["block_index"] == 0
    with pytest.raises(ValueError):
        block(digest="not-a-digest")
    with pytest.raises(ValueError):
        ModelContext("system", {}, (), digest)
    with pytest.raises(ValueError):
        ModelUsage(-1, 0, 0)
    with pytest.raises(InvalidModelOutput):
        Citation(uuid4(), -1, digest)
    with pytest.raises(InvalidModelOutput):
        Finding(
            "invalid key",
            FindingCategory.PRICING,
            "Statement",
            Confidence.HIGH,
            (Citation(uuid4(), 0, digest),),
            "scope",
        )
    with pytest.raises(InvalidModelOutput):
        RecommendedSource("", "reason", "query")
    with pytest.raises(ValueError):
        replace(run(), context_digest="bad")
    with pytest.raises(ValueError):
        replace(run(), selected_evidence=())
    with pytest.raises(ValueError):
        replace(run(), max_total_tokens=-1)
    value = run()
    with pytest.raises(ValueError):
        replace(value, recovery_of_run_id=value.id)
    now = datetime.now(UTC)
    attempt = ModelAttempt(
        tenant_id=uuid4(),
        agent_run_id=uuid4(),
        attempt_number=1,
        workload_id="worker",
        model_route_version="route-v1",
        pricing_version="pricing-v1",
        provider="provider",
        model="model",
        claimed_at=now,
        lease_expires_at=now + timedelta(minutes=15),
    )
    with pytest.raises(ValueError):
        replace(attempt, lease_expires_at=now)
    with pytest.raises(ValueError):
        replace(attempt, input_tokens=-1)
    with pytest.raises(ValueError):
        replace(attempt, input_tokens=1, total_tokens=0)
    with pytest.raises(ValueError):
        replace(attempt, unknown_cost=Decimal("-0.01"))
    with pytest.raises(ValueError):
        replace(attempt, status="PROVIDER_STARTED")
    with pytest.raises(ValueError):
        replace(run(), is_stranded=True)


def test_research_snapshot_rejects_duplicate_unbounded_and_tampered_content() -> None:
    reference = block()
    snapshot = parse_research_output(output(reference), run=run(), selected_blocks=(reference,))

    with pytest.raises(InvalidModelOutput):
        replace(snapshot, findings=(snapshot.findings[0], snapshot.findings[0]))
    with pytest.raises(InvalidModelOutput):
        replace(snapshot, research_gaps=("",))
    with pytest.raises(InvalidModelOutput):
        replace(snapshot, semantic_digest=canonical_digest({"tampered": True}))


def test_research_output_rejects_non_collection_fields() -> None:
    reference = block()
    with pytest.raises(InvalidModelOutput):
        parse_research_output(
            {"findings": {}, "research_gaps": [], "recommended_next_sources": []},
            run=run(),
            selected_blocks=(reference,),
        )
