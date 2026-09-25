"""Versioned, explicit Product Brain view for evidence-grounded market research.

This is not Product truth or Creative claim authority. Included values are complete;
growth beyond the route envelope must fail admission, never truncate source text.
"""

from collections.abc import Mapping

RESEARCHER_PROJECTION_VERSION = 2

# Code-owned order and names: adding a field requires a new projection version.
RESEARCHER_FIELDS: Mapping[str, tuple[str, ...]] = {
    "brand": ("name",),
    "brand_profile": (
        "industry",
        "brand_positioning",
        "target_markets",
        "primary_language",
        "competitors",
    ),
    "product": ("name", "category", "short_description"),
    "profile": (
        "description",
        "features",
        "benefits",
        "materials",
        "price",
        "currency",
        "target_audiences",
        "problems_solved",
        "use_cases",
        "differentiators",
        "purchase_objections",
        "shipping_summary",
        "seasonality_notes",
        "competitor_product_refs",
    ),
    "brief": (
        "product_why",
        "emotional_benefits",
        "primary_audience",
        "secondary_audiences",
        "positioning_statement",
        "competitive_alternatives",
        "why_choose_us",
        "current_channels",
        "priority_channels",
        "conversion_goal",
        "offers",
        "legal_safety_constraints",
        "geographical_restrictions",
    ),
}
AUDIENCE_FIELDS = ("name", "description", "pain_points", "desires", "motivations", "objections")
AUDIENCE_KEYS = {"target_audiences", "primary_audience", "secondary_audiences"}


def _compact(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): child
            for key in sorted(value)
            if (child := _compact(value[key])) not in (None, "", [], {})
        }
    if isinstance(value, (tuple, list)):
        return [child for item in value if (child := _compact(item)) not in (None, "", [], {})]
    return value


def _audience(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: value[key] for key in AUDIENCE_FIELDS if key in value}
    if isinstance(value, (tuple, list)):
        return [_audience(item) for item in value]
    return value


def project_researcher_product(content: Mapping[str, object]) -> dict[str, object]:
    """Copy whole allowlisted semantic fields, excluding unrelated/future fields by default."""
    result: dict[str, object] = {}
    for section, fields in RESEARCHER_FIELDS.items():
        source = content.get(section)
        if not isinstance(source, Mapping):
            continue
        selected = _compact(
            {
                key: _audience(source[key]) if key in AUDIENCE_KEYS else source[key]
                for key in fields
                if key in source
            }
        )
        if selected:
            result[section] = selected
    return result
