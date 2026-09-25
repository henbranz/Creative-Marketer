from dataclasses import replace
from typing import cast
from unittest.mock import Mock
from uuid import uuid4

import pytest

from creative_marketer.agent_runtime.domain import parse_research_output
from creative_marketer.catalog.application import (
    CatalogPermissionDenied,
    CatalogService,
    CatalogUnitOfWorkFactory,
)
from creative_marketer.catalog.domain import ProductKnowledgeSnapshot
from creative_marketer.creative.application import build_creative_context
from creative_marketer.creative.domain import (
    ChannelIntent,
    CreativeStrategyRequest,
    product_claim_refs,
)
from creative_marketer.identity.application.authentication import Actor, ActorKind
from tests.integration.test_catalog import owner_context
from tests.test_agent_runtime_domain import block, output, run
from tests.test_catalog_domain import product_brain


def test_product_and_brand_claims_are_the_only_creative_authority() -> None:
    brand, brand_profile, product, profile, brief = product_brain()
    research_block = block()
    research = parse_research_output(
        output(research_block), run=run(), selected_blocks=(research_block,)
    )
    request = CreativeStrategyRequest(3, ChannelIntent.TIKTOK)
    for claims in (False, True):
        snapshot = ProductKnowledgeSnapshot.create_v2(
            brand=brand,
            brand_profile=replace(brand_profile, allowed_claims=("Brand fact",) if claims else ()),
            product=product,
            profile=replace(profile, allowed_claims=("Product fact",) if claims else ()),
            brief=brief,
            created_by=product.created_by,
            asset_manifest=(),
        )
        context = build_creative_context(snapshot, research, request)
        assert [ref.text for ref in context.product_claims] == (
            ["Brand fact", "Product fact"] if claims else []
        )
        assert context.research_findings  # Actual Research output exists, but adds no authority.
        assert brief.product_why and brief.offers and profile.features
        assert context.product_claims == product_claim_refs(snapshot.digest, snapshot.content)


def test_unchanged_claim_text_gets_new_ids_in_a_new_revision() -> None:
    brand, brand_profile, product, profile, brief = product_brain()
    snapshots = [
        ProductKnowledgeSnapshot.create_v2(
            brand=brand,
            brand_profile=brand_profile,
            product=product,
            profile=replace(profile, allowed_claims=("  Confirmed fact  ",)),
            brief=replace(brief, revision=revision),
            created_by=product.created_by,
            asset_manifest=(),
        )
        for revision in (1, 2)
    ]
    refs = [product_claim_refs(snapshot.digest, snapshot.content) for snapshot in snapshots]
    assert refs[0][0].text == refs[1][0].text == "Confirmed fact"
    assert refs[0][0].key != refs[1][0].key


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [ActorKind.AGENT, ActorKind.SYSTEM, ActorKind.WORKLOAD])
async def test_non_human_actor_cannot_save_claims_even_with_owner_membership(
    kind: ActorKind,
) -> None:
    factory = Mock()
    service = CatalogService(cast(CatalogUnitOfWorkFactory, factory))
    context = replace(owner_context(uuid4(), uuid4()), actor=Actor(kind, uuid4()))
    with pytest.raises(CatalogPermissionDenied):
        await service.save_claims(
            context, uuid4(), scope="product", allowed_claims=("Fact",), expected_claims=()
        )
    factory.assert_not_called()
