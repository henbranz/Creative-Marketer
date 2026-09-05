from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from creative_marketer.catalog.domain import (
    Audience,
    Brand,
    BrandProfile,
    CatalogValidationError,
    Product,
    ProductBrief,
    ProductKnowledgeSnapshot,
    ProductProfile,
    evaluate_completeness,
)


def product_brain() -> tuple[Brand, BrandProfile, Product, ProductProfile, ProductBrief]:
    tenant_id, user_id = uuid4(), uuid4()
    brand = Brand(tenant_id=tenant_id, name="Northstar", slug="northstar", created_by=user_id)
    brand_profile = BrandProfile(tenant_id=tenant_id, brand_id=brand.id, brand_voice="Direct")
    product = Product(
        tenant_id=tenant_id,
        brand_id=brand.id,
        name="Atlas Bottle",
        slug="atlas-bottle",
        category="Drinkware",
        created_by=user_id,
    )
    audience = Audience(
        name="Urban commuters", pain_points=("Disposable waste",), objections=("Price",)
    )
    profile = ProductProfile(
        tenant_id=tenant_id,
        product_id=product.id,
        description="An insulated bottle",
        features=("Double-wall steel",),
        benefits=("Cold all day",),
        price=Decimal("29.90"),
        currency="usd",
        target_audiences=(audience,),
        differentiators=("Repairable lid",),
        prohibited_claims=("Cures dehydration",),
    )
    brief = ProductBrief(
        tenant_id=tenant_id,
        product_id=product.id,
        product_why="Make daily hydration durable",
        primary_audience=audience,
        positioning_statement="The repairable everyday bottle",
        desired_creative_style="Editorial utility",
    )
    return brand, brand_profile, product, profile, brief


def test_brand_product_and_urls_are_normalized_and_validated() -> None:
    tenant_id, user_id = uuid4(), uuid4()
    brand = Brand(
        tenant_id=tenant_id,
        name=" Northstar ",
        slug="Northstar",
        website_url="https://northstar.example",
        created_by=user_id,
    )
    assert brand.name == "Northstar" and brand.slug == "northstar"
    with pytest.raises(CatalogValidationError):
        Brand(tenant_id=tenant_id, name="", slug="bad slug", created_by=user_id)
    with pytest.raises(CatalogValidationError):
        Brand(
            tenant_id=tenant_id,
            name="Brand",
            slug="brand",
            website_url="ftp://example.test",
            created_by=user_id,
        )


def test_money_is_decimal_and_quantized_without_float_storage() -> None:
    *_, product, _, _ = product_brain()
    profile = ProductProfile(
        tenant_id=product.tenant_id,
        product_id=product.id,
        price=Decimal("10.999"),
        currency="eur",
        estimated_margin=Decimal("0.32555"),
    )
    assert profile.price == Decimal("11.00")
    assert profile.currency == "EUR"
    assert profile.estimated_margin == Decimal("0.3256")
    with pytest.raises(CatalogValidationError):
        ProductProfile(tenant_id=product.tenant_id, product_id=product.id, price=Decimal("1.00"))
    with pytest.raises(CatalogValidationError):
        ProductProfile(
            tenant_id=product.tenant_id, product_id=product.id, estimated_margin=Decimal("1.1")
        )
    with pytest.raises(CatalogValidationError):
        ProductProfile(
            tenant_id=product.tenant_id,
            product_id=product.id,
            price=Decimal("NaN"),
            currency="USD",
        )
    with pytest.raises(CatalogValidationError):
        ProductProfile(
            tenant_id=product.tenant_id,
            product_id=product.id,
            estimated_margin=Decimal("Infinity"),
        )


def test_claims_cannot_be_both_allowed_and_prohibited() -> None:
    *_, product, _, _ = product_brain()
    with pytest.raises(CatalogValidationError):
        ProductProfile(
            tenant_id=product.tenant_id,
            product_id=product.id,
            allowed_claims=("Safe",),
            prohibited_claims=("safe",),
        )


def test_audience_is_structured_and_bounded() -> None:
    audience = Audience(name="Operators", pain_points=("Manual work",), desires=("Control",))
    assert audience.semantic()["pain_points"] == ["Manual work"]
    with pytest.raises(CatalogValidationError):
        Audience(name="Operators", desires=("same", "Same"))


def test_completeness_is_deterministic_and_actionable() -> None:
    *_, profile, brief = product_brain()
    first = evaluate_completeness(profile, brief)
    second = evaluate_completeness(profile, brief)
    assert first == second
    assert first.score == 100
    incomplete = evaluate_completeness(
        replace(profile, benefits=()), replace(brief, positioning_statement="")
    )
    assert incomplete.score == 80
    assert incomplete.missing_fields == ("brief.positioning_statement", "profile.benefits")


def test_snapshot_digest_is_order_stable_and_excludes_timestamps() -> None:
    brand, brand_profile, product, profile, brief = product_brain()
    one = ProductKnowledgeSnapshot.create(
        brand=brand,
        brand_profile=brand_profile,
        product=product,
        profile=profile,
        brief=brief,
        created_by=product.created_by,
    )
    two = ProductKnowledgeSnapshot.create(
        brand=brand,
        brand_profile=brand_profile,
        product=product,
        profile=profile,
        brief=brief,
        created_by=product.created_by,
    )
    assert one.id != two.id
    assert one.created_at != two.created_at
    assert one.digest == two.digest


def test_snapshot_changes_when_semantic_brief_changes_and_old_snapshot_stays_immutable() -> None:
    brand, brand_profile, product, profile, brief = product_brain()
    one = ProductKnowledgeSnapshot.create(
        brand=brand,
        brand_profile=brand_profile,
        product=product,
        profile=profile,
        brief=brief,
        created_by=product.created_by,
    )
    changed = replace(brief, product_why="A materially different reason", revision=2)
    two = ProductKnowledgeSnapshot.create(
        brand=brand,
        brand_profile=brand_profile,
        product=product,
        profile=profile,
        brief=changed,
        created_by=product.created_by,
    )
    assert one.digest != two.digest
    assert one.source_revision == 1 and two.source_revision == 2
    assert one.content["brief"] != two.content["brief"]
    with pytest.raises(TypeError):
        one.content["brief"] = {}  # type: ignore[index]
    with pytest.raises(CatalogValidationError, match="digest does not match"):
        replace(one, digest="sha256:" + "0" * 64)


def test_provenance_is_explicit_in_agent_ready_semantics() -> None:
    *_, profile, brief = product_brain()
    assert profile.semantic()["provenance"] == "user_provided"
    assert brief.semantic()["provenance"] == "user_provided"
