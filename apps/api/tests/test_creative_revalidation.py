# mypy: disable-error-code="no-untyped-def,arg-type,unused-ignore"

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from creative_marketer.agent_runtime.domain import (
    Citation,
    Confidence,
    Finding,
    FindingCategory,
    ResearchSnapshot,
    canonical_digest,
)
from creative_marketer.catalog.domain import ProductKnowledgeSnapshot
from creative_marketer.creative.application import (
    CreativeRevalidationPreparation,
    CreativeService,
)
from creative_marketer.creative.domain import (
    CreativeConcept,
    CreativeConceptDecision,
    CreativeConceptSet,
    CreativeDecisionConflict,
    CreativeDecisionState,
    CreativeNotFound,
    CreativePermissionDenied,
    CreativeRevalidationNotReady,
    CreativeRevalidationReason,
    CreativeRevalidationResult,
    product_claim_refs,
)
from creative_marketer.events.domain import event_sha256_v1
from creative_marketer.identity.domain import MembershipRole, MembershipStatus


def finding(**changes: object) -> Finding:
    values = {
        "key": "audience_language",
        "category": FindingCategory.AUDIENCE,
        "statement": "Buyers value durable low-waste products.",
        "confidence": Confidence.HIGH,
        "citations": (Citation(uuid4(), 0, "sha256:" + "1" * 64),),
        "scope": "OBSERVED: supplied sources",
        "implication": "Lead with durability.",
    }
    values.update(changes)
    return Finding(**values)  # type: ignore[arg-type]


def research(
    product: ProductKnowledgeSnapshot, item: Finding, *, expired: bool
) -> ResearchSnapshot:
    now = datetime.now(UTC)
    semantic = {
        "schema_version": 1,
        "product_snapshot_id": str(product.id),
        "product_snapshot_digest": product.digest,
        "research_context_digest": "sha256:" + "2" * 64,
        "findings": [item.semantic()],
        "research_gaps": [],
        "recommended_next_sources": [],
    }
    return ResearchSnapshot(
        tenant_id=product.tenant_id,
        product_id=product.product_id,
        agent_run_id=uuid4(),
        product_snapshot_id=product.id,
        product_snapshot_digest=product.digest,
        research_context_digest="sha256:" + "2" * 64,
        findings=(item,),
        research_gaps=(),
        recommended_next_sources=(),
        semantic_digest=canonical_digest(semantic),
        created_at=now - timedelta(days=8) if expired else now,
        valid_until=now - timedelta(days=1) if expired else now + timedelta(days=7),
    )


def product() -> ProductKnowledgeSnapshot:
    content = {
        "brand_profile": {
            "allowed_claims": ["Made from recycled steel"],
            "prohibited_claims": ["Cures disease"],
        },
        "profile": {"allowed_claims": [], "prohibited_claims": []},
        "brief": {
            "required_disclaimers": ["Results vary"],
            "mandatory_messaging": [],
            "prohibited_messaging": [],
        },
    }
    source_revision = 1
    digest = event_sha256_v1(
        {"schema_version": 2, "source_revision": source_revision, "content": content}
    )
    return ProductKnowledgeSnapshot(
        uuid4(), uuid4(), source_revision, content, digest, uuid4(), schema_version=2
    )


def preparation() -> CreativeRevalidationPreparation:
    product_value = product()
    original = research(product_value, finding(), expired=True)
    current = research(
        product_value,
        finding(citations=(Citation(uuid4(), 2, "sha256:" + "3" * 64),)),
        expired=False,
    )
    set_id = uuid4()
    claim = product_claim_refs(product_value.digest, product_value.content)[0].key
    payload = {
        "supporting_research_refs": [
            {"research_snapshot_id": str(original.id), "finding_key": "audience_language"}
        ],
        "message_points": [
            {
                "kind": "PRODUCT_FACT",
                "product_claim_ref": claim,
                "text": "Made from recycled steel",
            }
        ],
    }
    concepts = tuple(
        CreativeConcept(
            product_value.tenant_id,
            product_value.product_id,
            set_id,
            f"concept_{ordinal}",
            ordinal,
            payload,
            canonical_digest(payload),
        )
        for ordinal in range(1, 4)
    )
    set_content = {
        "schema_version": 1,
        "product_snapshot_id": str(product_value.id),
        "product_snapshot_digest": product_value.digest,
        "research_snapshot_id": str(original.id),
        "research_snapshot_digest": original.semantic_digest,
        "input_context_digest": "sha256:" + "4" * 64,
        "concepts": [dict(item.payload) for item in concepts],
    }
    concept_set = CreativeConceptSet(
        product_value.tenant_id,
        product_value.product_id,
        uuid4(),
        product_value.id,
        product_value.digest,
        original.id,
        original.semantic_digest,
        "sha256:" + "4" * 64,
        concepts,
        canonical_digest(set_content),
        id=set_id,
    )
    decision = CreativeConceptDecision(
        product_value.tenant_id,
        product_value.product_id,
        concepts[0].id,
        CreativeDecisionState.APPROVED_FOR_PRODUCTION,
        uuid4(),
    )
    return CreativeRevalidationPreparation(
        concepts[0], concept_set, decision, original, current, "current", product_value
    )


class Recorder:
    def __init__(self) -> None:
        self.values: list[object] = []

    async def append(self, value: object) -> None:
        self.values.append(value)


class Repository:
    def __init__(self, value: CreativeRevalidationPreparation | None) -> None:
        self.value = value
        self.saved = None

    async def prepare_revalidation(self, _concept_id):
        return self.value

    async def find_revalidation(self, *_values):
        return self.saved

    async def add_revalidation(self, value):
        self.saved = value
        return True

    async def get_concept(self, concept_id, *, for_update=False):
        if self.value is None:
            return None
        return self.value.concept if concept_id == self.value.concept.id else None

    async def list_revalidations(self, _concept_id):
        return (self.saved,) if self.saved else ()


class UnitOfWork:
    def __init__(self, value: CreativeRevalidationPreparation | None) -> None:
        self.creative = Repository(value)
        self.audit = Recorder()
        self.outbox = Recorder()
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def commit(self):
        self.commits += 1


def context(role: MembershipRole = MembershipRole.OWNER):
    return SimpleNamespace(
        tenant_id=uuid4(),
        membership_status=MembershipStatus.ACTIVE,
        membership_role=role,
        user_id=uuid4(),
        actor=SimpleNamespace(kind=SimpleNamespace(value="user"), id=uuid4()),
        correlation_id=uuid4(),
        environment="test",
    )


async def execute(value: CreativeRevalidationPreparation):
    uow = UnitOfWork(value)
    ctx = context()
    ctx.tenant_id = value.concept.tenant_id
    result = await CreativeService(lambda _tenant_id: uow).revalidate(ctx, value.concept.id)  # type: ignore[arg-type]
    return result, uow, ctx


@pytest.mark.asyncio
async def test_identical_assertions_with_refreshed_citations_revalidate_without_provider() -> None:
    result, uow, ctx = await execute(preparation())
    assert result.result is CreativeRevalidationResult.REVALIDATED_FOR_PRODUCTION
    assert result.reason_codes == ()
    assert result.referenced_finding_assertions[0].original_assertion_digest == (
        result.referenced_finding_assertions[0].current_assertion_digest
    )
    assert len(uow.audit.values) == len(uow.outbox.values) == 1
    assert "statement" not in str(uow.audit.values[0])
    replay = await CreativeService(lambda _tenant_id: uow).revalidate(  # type: ignore[arg-type]
        ctx, result.concept_id
    )
    assert replay.id == result.id
    assert uow.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"statement": "Different"}, CreativeRevalidationReason.RESEARCH_FINDING_STATEMENT_CHANGED),
        (
            {"category": FindingCategory.COMPETITOR},
            CreativeRevalidationReason.RESEARCH_FINDING_CATEGORY_CHANGED,
        ),
        (
            {"confidence": Confidence.LOW},
            CreativeRevalidationReason.RESEARCH_FINDING_CONFIDENCE_CHANGED,
        ),
        (
            {"scope": "INFERRED: different"},
            CreativeRevalidationReason.RESEARCH_FINDING_SCOPE_CHANGED,
        ),
        (
            {"implication": "Different"},
            CreativeRevalidationReason.RESEARCH_FINDING_IMPLICATION_CHANGED,
        ),
    ],
)
async def test_assertion_changes_require_restrategy(
    changes: dict[str, object], reason: CreativeRevalidationReason
) -> None:
    value = preparation()
    changed = research(value.current_product, finding(**changes), expired=False)
    result, _, _ = await execute(replace(value, current_research=changed))
    assert result.result is CreativeRevalidationResult.REQUIRES_RESTRATEGY
    assert reason in result.reason_codes


@pytest.mark.asyncio
async def test_missing_finding_requires_restrategy() -> None:
    value = preparation()
    changed = research(value.current_product, finding(key="different_key"), expired=False)
    result, _, _ = await execute(replace(value, current_research=changed))
    assert result.reason_codes == (CreativeRevalidationReason.RESEARCH_FINDING_MISSING,)


@pytest.mark.asyncio
async def test_product_change_and_invalid_claim_require_restrategy() -> None:
    value = preparation()
    changed_product = product()
    result, _, _ = await execute(replace(value, current_product=changed_product))
    assert CreativeRevalidationReason.PRODUCT_AUTHORITY_CHANGED in result.reason_codes

    content = {
        "brand_profile": {
            "allowed_claims": ["Different claim"],
            "prohibited_claims": ["Cures disease"],
        },
        "profile": {"allowed_claims": [], "prohibited_claims": []},
        "brief": {
            "required_disclaimers": ["Results vary"],
            "mandatory_messaging": [],
            "prohibited_messaging": [],
        },
    }
    digest = event_sha256_v1({"schema_version": 2, "source_revision": 2, "content": content})
    claim_changed_product = ProductKnowledgeSnapshot(
        value.current_product.tenant_id,
        value.current_product.product_id,
        2,
        content,
        digest,
        uuid4(),
        schema_version=2,
    )
    result, _, _ = await execute(replace(value, current_product=claim_changed_product))
    assert CreativeRevalidationReason.PRODUCT_CLAIM_REFERENCE_INVALID in result.reason_codes


@pytest.mark.asyncio
async def test_revalidation_fails_closed_for_decision_freshness_and_permissions() -> None:
    value = preparation()
    rejected = replace(value.decision, state=CreativeDecisionState.REJECTED)
    with pytest.raises(CreativeDecisionConflict):
        await execute(replace(value, decision=rejected))
    with pytest.raises(CreativeRevalidationNotReady):
        await execute(replace(value, current_research_freshness="stale"))

    uow = UnitOfWork(value)
    ctx = context(MembershipRole.MEMBER)
    ctx.tenant_id = value.concept.tenant_id
    with pytest.raises(CreativePermissionDenied):
        await CreativeService(lambda _tenant_id: uow).revalidate(ctx, value.concept.id)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_revalidation_authority_rejects_internally_inconsistent_records() -> None:
    successful, _, _ = await execute(preparation())
    reason = CreativeRevalidationReason.RESEARCH_FINDING_MISSING
    with pytest.raises(ValueError, match="reason codes must be unique"):
        replace(
            successful,
            result=CreativeRevalidationResult.REQUIRES_RESTRATEGY,
            reason_codes=(reason, reason),
        )
    with pytest.raises(ValueError, match="successful revalidation"):
        replace(successful, reason_codes=(reason,))
    with pytest.raises(ValueError, match="requires at least one reason"):
        replace(successful, result=CreativeRevalidationResult.REQUIRES_RESTRATEGY)
    with pytest.raises(ValueError, match="digest does not match"):
        replace(successful, semantic_digest="sha256:" + "0" * 64)


@pytest.mark.asyncio
async def test_revalidation_rejects_missing_or_malformed_frozen_authority() -> None:
    value = preparation()
    ctx = context()
    ctx.tenant_id = value.concept.tenant_id
    missing_uow = UnitOfWork(None)
    service = CreativeService(lambda _tenant_id: missing_uow)  # type: ignore[arg-type]
    with pytest.raises(CreativeNotFound):
        await service.revalidate(ctx, value.concept.id)
    with pytest.raises(CreativeNotFound):
        await service.list_revalidations(ctx, value.concept.id)

    for references in (
        "malformed",
        [{"research_snapshot_id": str(uuid4()), "finding_key": "audience_language"}],
    ):
        payload = {**value.concept.payload, "supporting_research_refs": references}
        malformed = replace(
            value.concept,
            payload=payload,
            semantic_digest=canonical_digest(payload),
        )
        with pytest.raises(CreativeRevalidationNotReady):
            await execute(replace(value, concept=malformed))

    missing_original = research(
        value.current_product,
        finding(key="different_historical_key"),
        expired=True,
    )
    with pytest.raises(CreativeRevalidationNotReady):
        await execute(replace(value, original_research=missing_original))
