from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from urllib.parse import urlparse
from uuid import UUID, uuid4

from creative_marketer.events.domain import event_sha256_v1


class CatalogValidationError(ValueError):
    pass


class BrandStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ProductStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class KnowledgeProvenance(StrEnum):
    USER_PROVIDED = "user_provided"
    IMPORTED = "imported"
    AI_INFERRED = "ai_inferred"
    VALIDATED = "validated"


def utc_now() -> datetime:
    return datetime.now(UTC)


def _required(value: str, label: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise CatalogValidationError(f"{label} must be between 1 and {maximum} characters")
    return normalized


def _optional(value: str | None, maximum: int) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip()
    if len(normalized) > maximum:
        raise CatalogValidationError(f"value exceeds {maximum} characters")
    return normalized


def _slug(value: str) -> str:
    normalized = value.strip().lower()
    if (
        not normalized
        or len(normalized) > 100
        or any(not (part.isalnum() and part.isascii()) for part in normalized.split("-"))
    ):
        raise CatalogValidationError(
            "slug must contain lowercase ASCII letters, numbers, or hyphens"
        )
    return normalized


def _url(value: str | None) -> str | None:
    normalized = _optional(value, 2048)
    if normalized is None:
        return None
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
        raise CatalogValidationError("URL must be an absolute HTTP(S) URL without user info")
    return normalized


def _items(
    values: tuple[str, ...], *, maximum: int = 30, item_maximum: int = 500
) -> tuple[str, ...]:
    if len(values) > maximum:
        raise CatalogValidationError(f"list exceeds {maximum} items")
    normalized = tuple(_required(value, "list item", item_maximum) for value in values)
    if len(set(item.casefold() for item in normalized)) != len(normalized):
        raise CatalogValidationError("list items must be unique")
    return normalized


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    return value


@dataclass(frozen=True, slots=True)
class Brand:
    tenant_id: UUID
    name: str
    slug: str
    created_by: UUID
    id: UUID = field(default_factory=uuid4)
    website_url: str | None = None
    status: BrandStatus = BrandStatus.ACTIVE
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required(self.name, "brand name", 200))
        object.__setattr__(self, "slug", _slug(self.slug))
        object.__setattr__(self, "website_url", _url(self.website_url))


@dataclass(frozen=True, slots=True)
class BrandProfile:
    tenant_id: UUID
    brand_id: UUID
    industry: str = ""
    description: str = ""
    brand_positioning: str = ""
    brand_voice: str = ""
    tone_attributes: tuple[str, ...] = ()
    visual_style_keywords: tuple[str, ...] = ()
    target_markets: tuple[str, ...] = ()
    primary_language: str = "en"
    allowed_claims: tuple[str, ...] = ()
    prohibited_claims: tuple[str, ...] = ()
    competitors: tuple[str, ...] = ()
    provenance: KnowledgeProvenance = KnowledgeProvenance.USER_PROVIDED
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name, maximum in (
            ("industry", 200),
            ("description", 4000),
            ("brand_positioning", 2000),
            ("brand_voice", 2000),
        ):
            object.__setattr__(self, name, _optional(getattr(self, name), maximum) or "")
        object.__setattr__(
            self,
            "primary_language",
            _required(self.primary_language, "primary language", 16).lower(),
        )
        for name in (
            "tone_attributes",
            "visual_style_keywords",
            "target_markets",
            "allowed_claims",
            "prohibited_claims",
            "competitors",
        ):
            object.__setattr__(self, name, _items(getattr(self, name)))
        overlap = {v.casefold() for v in self.allowed_claims} & {
            v.casefold() for v in self.prohibited_claims
        }
        if overlap:
            raise CatalogValidationError("a claim cannot be both allowed and prohibited")


@dataclass(frozen=True, slots=True)
class Product:
    tenant_id: UUID
    brand_id: UUID
    name: str
    slug: str
    category: str
    created_by: UUID
    id: UUID = field(default_factory=uuid4)
    sku: str | None = None
    short_description: str = ""
    status: ProductStatus = ProductStatus.DRAFT
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required(self.name, "product name", 200))
        object.__setattr__(self, "slug", _slug(self.slug))
        object.__setattr__(self, "category", _required(self.category, "category", 200))
        object.__setattr__(self, "sku", _optional(self.sku, 100))
        object.__setattr__(self, "short_description", _optional(self.short_description, 1000) or "")


@dataclass(frozen=True, slots=True)
class Audience:
    name: str
    description: str = ""
    pain_points: tuple[str, ...] = ()
    desires: tuple[str, ...] = ()
    motivations: tuple[str, ...] = ()
    objections: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required(self.name, "audience name", 120))
        object.__setattr__(self, "description", _optional(self.description, 1000) or "")
        for name in ("pain_points", "desires", "motivations", "objections"):
            object.__setattr__(self, name, _items(getattr(self, name), maximum=20))

    def semantic(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "pain_points": list(self.pain_points),
            "desires": list(self.desires),
            "motivations": list(self.motivations),
            "objections": list(self.objections),
        }


@dataclass(frozen=True, slots=True)
class ProductProfile:
    tenant_id: UUID
    product_id: UUID
    description: str = ""
    features: tuple[str, ...] = ()
    benefits: tuple[str, ...] = ()
    materials: tuple[str, ...] = ()
    variants: tuple[str, ...] = ()
    price: Decimal | None = None
    currency: str | None = None
    estimated_margin: Decimal | None = None
    target_audiences: tuple[Audience, ...] = ()
    problems_solved: tuple[str, ...] = ()
    use_cases: tuple[str, ...] = ()
    differentiators: tuple[str, ...] = ()
    purchase_objections: tuple[str, ...] = ()
    allowed_claims: tuple[str, ...] = ()
    prohibited_claims: tuple[str, ...] = ()
    shipping_summary: str | None = None
    seasonality_notes: str | None = None
    landing_page_url: str | None = None
    competitor_product_refs: tuple[str, ...] = ()
    provenance: KnowledgeProvenance = KnowledgeProvenance.USER_PROVIDED
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "description", _optional(self.description, 8000) or "")
        for name in (
            "features",
            "benefits",
            "materials",
            "variants",
            "problems_solved",
            "use_cases",
            "differentiators",
            "purchase_objections",
            "allowed_claims",
            "prohibited_claims",
            "competitor_product_refs",
        ):
            object.__setattr__(self, name, _items(getattr(self, name)))
        overlap = {v.casefold() for v in self.allowed_claims} & {
            v.casefold() for v in self.prohibited_claims
        }
        if overlap:
            raise CatalogValidationError("a claim cannot be both allowed and prohibited")
        if (self.price is None) != (self.currency is None):
            raise CatalogValidationError("price and currency must be provided together")
        if self.price is not None:
            try:
                price = Decimal(self.price).quantize(Decimal("0.01"))
            except InvalidOperation as error:
                raise CatalogValidationError("price is invalid") from error
            if not price.is_finite() or price < 0:
                raise CatalogValidationError("price cannot be negative")
            object.__setattr__(self, "price", price)
            currency = _required(self.currency or "", "currency", 3).upper()
            if len(currency) != 3 or not currency.isalpha() or not currency.isascii():
                raise CatalogValidationError("currency must be an ISO-style three-letter code")
            object.__setattr__(self, "currency", currency)
        if self.estimated_margin is not None:
            try:
                margin = Decimal(self.estimated_margin).quantize(Decimal("0.0001"))
            except InvalidOperation as error:
                raise CatalogValidationError("estimated margin is invalid") from error
            if not margin.is_finite() or not Decimal("0") <= margin <= Decimal("1"):
                raise CatalogValidationError("estimated margin must be between 0 and 1")
            object.__setattr__(self, "estimated_margin", margin)
        object.__setattr__(self, "shipping_summary", _optional(self.shipping_summary, 2000))
        object.__setattr__(self, "seasonality_notes", _optional(self.seasonality_notes, 2000))
        object.__setattr__(self, "landing_page_url", _url(self.landing_page_url))

    def semantic(self) -> dict[str, object]:
        return {
            "description": self.description,
            "features": list(self.features),
            "benefits": list(self.benefits),
            "materials": list(self.materials),
            "variants": list(self.variants),
            "price": None if self.price is None else format(self.price, ".2f"),
            "currency": self.currency,
            "estimated_margin": None
            if self.estimated_margin is None
            else format(self.estimated_margin, ".4f"),
            "target_audiences": [a.semantic() for a in self.target_audiences],
            "problems_solved": list(self.problems_solved),
            "use_cases": list(self.use_cases),
            "differentiators": list(self.differentiators),
            "purchase_objections": list(self.purchase_objections),
            "allowed_claims": list(self.allowed_claims),
            "prohibited_claims": list(self.prohibited_claims),
            "shipping_summary": self.shipping_summary,
            "seasonality_notes": self.seasonality_notes,
            "landing_page_url": self.landing_page_url,
            "competitor_product_refs": list(self.competitor_product_refs),
            "provenance": self.provenance.value,
        }


@dataclass(frozen=True, slots=True)
class ProductBrief:
    tenant_id: UUID
    product_id: UUID
    product_why: str = ""
    emotional_benefits: tuple[str, ...] = ()
    primary_audience: Audience | None = None
    secondary_audiences: tuple[Audience, ...] = ()
    positioning_statement: str = ""
    competitive_alternatives: tuple[str, ...] = ()
    why_choose_us: tuple[str, ...] = ()
    current_channels: tuple[str, ...] = ()
    priority_channels: tuple[str, ...] = ()
    conversion_goal: str = ""
    offers: tuple[str, ...] = ()
    cta_preferences: tuple[str, ...] = ()
    desired_creative_style: str = ""
    tones_to_explore: tuple[str, ...] = ()
    tones_to_avoid: tuple[str, ...] = ()
    creative_references: tuple[str, ...] = ()
    mandatory_messaging: tuple[str, ...] = ()
    prohibited_messaging: tuple[str, ...] = ()
    required_disclaimers: tuple[str, ...] = ()
    legal_safety_constraints: tuple[str, ...] = ()
    geographical_restrictions: tuple[str, ...] = ()
    provenance: KnowledgeProvenance = KnowledgeProvenance.USER_PROVIDED
    revision: int = 1
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name, maximum in (
            ("product_why", 3000),
            ("positioning_statement", 3000),
            ("conversion_goal", 500),
            ("desired_creative_style", 2000),
        ):
            object.__setattr__(self, name, _optional(getattr(self, name), maximum) or "")
        for name in (
            "emotional_benefits",
            "competitive_alternatives",
            "why_choose_us",
            "current_channels",
            "priority_channels",
            "offers",
            "cta_preferences",
            "tones_to_explore",
            "tones_to_avoid",
            "creative_references",
            "mandatory_messaging",
            "prohibited_messaging",
            "required_disclaimers",
            "legal_safety_constraints",
            "geographical_restrictions",
        ):
            object.__setattr__(self, name, _items(getattr(self, name)))
        if self.revision < 1:
            raise CatalogValidationError("brief revision must be positive")

    def semantic(self) -> dict[str, object]:
        return {
            "product_why": self.product_why,
            "emotional_benefits": list(self.emotional_benefits),
            "primary_audience": None
            if self.primary_audience is None
            else self.primary_audience.semantic(),
            "secondary_audiences": [a.semantic() for a in self.secondary_audiences],
            "positioning_statement": self.positioning_statement,
            "competitive_alternatives": list(self.competitive_alternatives),
            "why_choose_us": list(self.why_choose_us),
            "current_channels": list(self.current_channels),
            "priority_channels": list(self.priority_channels),
            "conversion_goal": self.conversion_goal,
            "offers": list(self.offers),
            "cta_preferences": list(self.cta_preferences),
            "desired_creative_style": self.desired_creative_style,
            "tones_to_explore": list(self.tones_to_explore),
            "tones_to_avoid": list(self.tones_to_avoid),
            "creative_references": list(self.creative_references),
            "mandatory_messaging": list(self.mandatory_messaging),
            "prohibited_messaging": list(self.prohibited_messaging),
            "required_disclaimers": list(self.required_disclaimers),
            "legal_safety_constraints": list(self.legal_safety_constraints),
            "geographical_restrictions": list(self.geographical_restrictions),
            "provenance": self.provenance.value,
        }


@dataclass(frozen=True, slots=True)
class BriefCompleteness:
    score: int
    missing_sections: tuple[str, ...]
    missing_fields: tuple[str, ...]


def evaluate_completeness(profile: ProductProfile, brief: ProductBrief) -> BriefCompleteness:
    groups: Mapping[str, tuple[tuple[str, bool], ...]] = {
        "Product basics": (
            ("profile.description", bool(profile.description)),
            ("brief.product_why", bool(brief.product_why)),
        ),
        "Audience": (
            ("profile.target_audiences", bool(profile.target_audiences)),
            (
                "brief.primary_audience.pain_points",
                bool(brief.primary_audience and brief.primary_audience.pain_points),
            ),
        ),
        "Positioning": (
            ("brief.positioning_statement", bool(brief.positioning_statement)),
            ("profile.differentiators", bool(profile.differentiators)),
        ),
        "Benefits and features": (
            ("profile.features", bool(profile.features)),
            ("profile.benefits", bool(profile.benefits)),
        ),
        "Creative and claims": (
            ("brief.desired_creative_style", bool(brief.desired_creative_style)),
            ("claims.prohibited", bool(profile.prohibited_claims or brief.prohibited_messaging)),
        ),
    }
    missing = tuple(field for fields in groups.values() for field, present in fields if not present)
    sections = tuple(
        section for section, fields in groups.items() if any(not present for _, present in fields)
    )
    return BriefCompleteness(
        score=100 - len(missing) * 10, missing_sections=sections, missing_fields=missing
    )


@dataclass(frozen=True, slots=True)
class ProductKnowledgeSnapshot:
    tenant_id: UUID
    product_id: UUID
    source_revision: int
    content: Mapping[str, object]
    digest: str
    created_by: UUID
    id: UUID = field(default_factory=uuid4)
    schema_version: int = 1
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", _freeze(dict(self.content)))
        expected = event_sha256_v1(
            {
                "schema_version": self.schema_version,
                "source_revision": self.source_revision,
                "content": self.content,
            }
        )
        if self.digest != expected:
            raise CatalogValidationError("snapshot digest does not match its canonical content")

    @classmethod
    def create(
        cls,
        *,
        brand: Brand,
        brand_profile: BrandProfile,
        product: Product,
        profile: ProductProfile,
        brief: ProductBrief,
        created_by: UUID,
    ) -> "ProductKnowledgeSnapshot":
        content: dict[str, object] = {
            "brand": {
                "id": str(brand.id),
                "name": brand.name,
                "slug": brand.slug,
                "status": brand.status.value,
            },
            "brand_profile": {
                "industry": brand_profile.industry,
                "description": brand_profile.description,
                "brand_positioning": brand_profile.brand_positioning,
                "brand_voice": brand_profile.brand_voice,
                "tone_attributes": list(brand_profile.tone_attributes),
                "visual_style_keywords": list(brand_profile.visual_style_keywords),
                "target_markets": list(brand_profile.target_markets),
                "primary_language": brand_profile.primary_language,
                "allowed_claims": list(brand_profile.allowed_claims),
                "prohibited_claims": list(brand_profile.prohibited_claims),
                "competitors": list(brand_profile.competitors),
                "provenance": brand_profile.provenance.value,
            },
            "product": {
                "id": str(product.id),
                "brand_id": str(product.brand_id),
                "name": product.name,
                "slug": product.slug,
                "sku": product.sku,
                "category": product.category,
                "short_description": product.short_description,
                "status": product.status.value,
            },
            "profile": profile.semantic(),
            "brief": brief.semantic(),
        }
        semantic = {"schema_version": 1, "source_revision": brief.revision, "content": content}
        return cls(
            tenant_id=product.tenant_id,
            product_id=product.id,
            source_revision=brief.revision,
            content=content,
            digest=event_sha256_v1(semantic),
            created_by=created_by,
        )
