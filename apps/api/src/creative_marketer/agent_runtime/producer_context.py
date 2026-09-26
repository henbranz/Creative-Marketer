"""Versioned, deterministic provider views for approved production planning.

Product and Research snapshots remain the complete canonical authorities. This module
selects only the whole, production-relevant values needed by Producer; it never truncates,
summarizes, or grants new claim authority.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from creative_marketer.agent_runtime.domain import Finding, canonical_digest
from creative_marketer.creative.domain import product_claim_refs

PRODUCER_CONTEXT_VERSION = 2
PRODUCER_PRODUCT_PROJECTION_VERSION = 1
PRODUCER_RESEARCH_PROJECTION_VERSION = 1
PRODUCER_PROMPT_REVISION = "producer_v4_context_v2"


class InvalidProducerProjection(ValueError):
    """The approved concept cannot be resolved against its frozen authorities."""


@dataclass(frozen=True, slots=True)
class ProducerProviderProjection:
    product: Mapping[str, object]
    research_findings: tuple[Mapping[str, object], ...]
    product_digest: str
    research_digest: str
    product_claim_refs: tuple[str, ...]
    research_finding_refs: tuple[Mapping[str, str], ...]


def _compact(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): child
            for key, original in value.items()
            if (child := _compact(original)) not in (None, "", [], {})
        }
    if isinstance(value, (tuple, list)):
        return [child for item in value if (child := _compact(item)) not in (None, "", [], {})]
    return value


def _section(content: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = content.get(name)
    return value if isinstance(value, Mapping) else {}


def _whole_fields(source: Mapping[str, object], fields: tuple[str, ...]) -> dict[str, object]:
    value = _compact({key: source[key] for key in fields if key in source})
    return dict(value) if isinstance(value, Mapping) else {}


def referenced_product_claim_keys(concept: Mapping[str, object]) -> tuple[str, ...]:
    """Return exact PRODUCT_FACT claim identities in first-use order."""
    raw = concept.get("message_points", ())
    points = raw if isinstance(raw, (list, tuple)) else ()
    keys: list[str] = []
    for point in points:
        if not isinstance(point, Mapping) or str(point.get("kind")) != "PRODUCT_FACT":
            continue
        reference = point.get("product_claim_ref")
        if not isinstance(reference, str) or not reference:
            raise InvalidProducerProjection(
                "approved Product fact is missing its exact frozen claim reference"
            )
        if reference not in keys:
            keys.append(reference)
    return tuple(keys)


def project_producer_product(
    content: Mapping[str, object],
    *,
    snapshot_digest: str,
    approved_concept: Mapping[str, object],
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Build the explicit producer_product.v1 view from frozen Product authority."""
    brand = _section(content, "brand")
    product = _section(content, "product")
    profile = _section(content, "profile")
    brand_profile = _section(content, "brand_profile")
    brief = _section(content, "brief")

    claim_keys = referenced_product_claim_keys(approved_concept)
    claims_by_key = {item.key: item.text for item in product_claim_refs(snapshot_digest, content)}
    missing = tuple(key for key in claim_keys if key not in claims_by_key)
    if missing:
        raise InvalidProducerProjection(
            "approved concept references Product claim authority absent from its frozen snapshot"
        )

    projected = _compact(
        {
            "identity": {
                "brand_name": brand.get("name"),
                "product_name": product.get("name"),
                "product_category": product.get("category"),
                "product_short_description": product.get("short_description"),
            },
            "approved_claims": [{"key": key, "text": claims_by_key[key]} for key in claim_keys],
            "safety": {
                "brand_prohibited_claims": brand_profile.get("prohibited_claims"),
                "product_prohibited_claims": profile.get("prohibited_claims"),
                "mandatory_messaging": brief.get("mandatory_messaging"),
                "prohibited_messaging": brief.get("prohibited_messaging"),
                "required_disclaimers": brief.get("required_disclaimers"),
                "legal_safety_constraints": brief.get("legal_safety_constraints"),
                "geographical_restrictions": brief.get("geographical_restrictions"),
            },
            "visual_truth": _whole_fields(profile, ("description", "materials")),
        }
    )
    assert isinstance(projected, dict)
    return projected, claim_keys


def referenced_research_findings(
    approved_concept: Mapping[str, object],
    *,
    research_snapshot_id: str,
    findings: tuple[Finding, ...],
) -> tuple[tuple[Mapping[str, object], ...], tuple[Mapping[str, str], ...]]:
    """Resolve the concept allowlist to whole, bounded finding values and content digests."""
    raw = approved_concept.get("supporting_research_refs", ())
    references = raw if isinstance(raw, (list, tuple)) else ()
    keys: list[str] = []
    for reference in references:
        if not isinstance(reference, Mapping):
            raise InvalidProducerProjection("approved Research reference is malformed")
        if str(reference.get("research_snapshot_id")) != research_snapshot_id:
            raise InvalidProducerProjection(
                "approved concept references Research outside its frozen snapshot"
            )
        key = reference.get("finding_key")
        if not isinstance(key, str) or not key:
            raise InvalidProducerProjection("approved Research reference has no finding identity")
        if key not in keys:
            keys.append(key)

    findings_by_key = {item.key: item for item in findings}
    missing = tuple(key for key in keys if key not in findings_by_key)
    if missing:
        raise InvalidProducerProjection(
            "approved concept references a finding absent from its frozen Research snapshot"
        )

    provider_values: list[Mapping[str, object]] = []
    frozen_refs: list[Mapping[str, str]] = []
    for key in keys:
        semantic = findings_by_key[key].semantic()
        digest = canonical_digest(semantic)
        provider_values.append(
            {
                field: semantic[field]
                for field in ("key", "category", "statement", "confidence", "scope", "implication")
            }
        )
        frozen_refs.append({"key": key, "digest": digest})
    return tuple(provider_values), tuple(frozen_refs)


def build_producer_provider_projection(
    *,
    product_content: Mapping[str, object],
    product_snapshot_digest: str,
    approved_concept: Mapping[str, object],
    research_snapshot_id: str,
    research_findings: tuple[Finding, ...],
) -> ProducerProviderProjection:
    product, claim_refs = project_producer_product(
        product_content,
        snapshot_digest=product_snapshot_digest,
        approved_concept=approved_concept,
    )
    research, research_refs = referenced_research_findings(
        approved_concept,
        research_snapshot_id=research_snapshot_id,
        findings=research_findings,
    )
    return ProducerProviderProjection(
        product,
        research,
        canonical_digest(product),
        canonical_digest(list(research)),
        claim_refs,
        research_refs,
    )
