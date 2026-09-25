# mypy: disable-error-code="no-untyped-def,no-untyped-call,index,arg-type"
"""Synthetic geometry of the rev-8/rev-11 incident; no Product or Evidence text.

Numbers below are serialized UTF-8 JSON field sizes, not text captured from the
tenant. ASCII filler preserves the measured admission boundary exactly.
"""

import json
import socket
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from creative_marketer.agent_runtime.application import (
    build_context,
    compact_context,
    conservative_input_token_bound,
    conservative_researcher_input_token_bound,
    fit_researcher_evidence,
)
from creative_marketer.agent_runtime.domain import (
    AgentContextBudgetExceeded,
    EvidenceBlockRef,
    ModelInvocationResult,
    ModelUsage,
    canonical_digest,
)
from creative_marketer.agent_runtime.researcher_context import project_researcher_product
from creative_marketer.catalog.domain import ProductKnowledgeSnapshot
from creative_marketer.events.domain import event_sha256_v1
from creative_marketer.infrastructure.model_providers.fake import FakeModelProvider
from scripts.bootstrap_researcher import researcher_configuration
from tests.test_agent_runtime_application import (
    context,
    preparation,
    preparation_with_blocks,
    service,
)


def s(json_bytes):
    return "x" * (json_bytes - 2)


def product_geometry(revision):
    return {
        "assets": [
            {
                "allowed_uses": [s(19), s(18)],
                "asset_id": s(38),
                "byte_size": 1000000,
                "digest": s(73),
                "kind": s(7),
                "mime_type": s(11),
                "rights_status": s(11),
                "role": s(11),
                "status": s(7),
            }
        ],
        "brand": {"id": s(38), "name": s(6), "slug": s(6), "status": s(8)},
        "brand_profile": {
            "industry": s(24),
            "primary_language": s(4),
            "provenance": s(15),
        },
        "product": {
            "brand_id": s(38),
            "category": s(10),
            "id": s(38),
            "name": s(15),
            "slug": s(15),
            "status": s(7),
        },
        "profile": {
            "provenance": s(15),
            "allowed_claims": [s(n) for n in (37, 93, 57, 50)] if revision == 11 else [],
        },
        "brief": {
            "competitive_alternatives": [s(80)],
            "conversion_goal": s(38),
            "cta_preferences": [s(n) for n in (9, 19, 21)],
            "desired_creative_style": s(756),
            "emotional_benefits": [s(n) for n in (288, 75, 129)],
            "geographical_restrictions": [s(29)],
            "mandatory_messaging": [s(29)],
            "offers": [s(82)],
            "positioning_statement": s(351),
            "primary_audience": {
                "description": s(227),
                "desires": [s(99)],
                "name": s(18),
                "objections": [s(n) for n in (162, 102, 127)],
                "pain_points": [s(397)],
            },
            "priority_channels": [s(76)],
            "product_why": s(426),
            "prohibited_messaging": [s(n) for n in (141, 180, 158, 120, 143)],
            "provenance": s(15),
            "tones_to_explore": [s(n) for n in (165, 206, 195, 298, 164)],
            "why_choose_us": [s(n) for n in (125, 147, 147, 153)],
        },
    }


def live_geometry(revision=11):
    prepared = preparation(uuid4(), uuid4())
    content = product_geometry(revision)
    snapshot = ProductKnowledgeSnapshot(
        tenant_id=prepared.product_snapshot.tenant_id,
        product_id=prepared.product_snapshot.product_id,
        created_by=prepared.product_snapshot.created_by,
        source_revision=revision,
        schema_version=2,
        content=content,
        digest=event_sha256_v1(
            {"schema_version": 2, "source_revision": revision, "content": content}
        ),
    )
    cfg = researcher_configuration()
    prepared = replace(
        prepared,
        product_snapshot=snapshot,
        researcher=replace(
            prepared.researcher,
            configuration=cfg,
            configuration_digest=cfg.configuration_digest,
        ),
    )
    kinds = ("list",) * 6 + ("heading", "paragraph", "heading", "paragraph")
    blocks = tuple(
        EvidenceBlockRef(
            UUID(int=1),
            UUID(int=2),
            s(12),
            i,
            kind,
            "sha256:" + "a" * 64,
            s(length),
            False,
        )
        for i, (kind, length) in enumerate(
            zip(kinds, (6, 15, 14, 12, 9, 14, 24, 114, 24, 113), strict=True)
        )
    )
    return prepared, blocks


@pytest.mark.parametrize("revision,old_fixed,old_first", [(8, 9615, 9924), (11, 9875, 10184)])
def test_incident_geometry_exact_legacy_boundary_and_complete_projected_fit(
    revision,
    old_fixed,
    old_first,
):
    prepared, blocks = live_geometry(revision)
    cfg = prepared.researcher.configuration
    original_digest = prepared.product_snapshot.digest
    original_content = compact_context(prepared.product_snapshot.content)
    assert (
        conservative_researcher_input_token_bound(cfg.system_instructions, original_content, ())
        == old_fixed
    )
    assert (
        conservative_input_token_bound(build_context(prepared, blocks[:1], projection_version=1))
        == old_first
    )
    fitted, model = fit_researcher_evidence(prepared, blocks, 10000)
    assert fitted == blocks  # Every complete block, including exact text and identity.
    assert conservative_input_token_bound(model) == 9647
    assert conservative_input_token_bound(build_context(prepared, blocks[:1])) == 6556
    assert (
        conservative_researcher_input_token_bound(
            cfg.system_instructions, model.product_context, ()
        )
        == 6247
    )
    assert prepared.product_snapshot.digest == original_digest
    assert compact_context(prepared.product_snapshot.content) == original_content
    assert build_context(prepared, blocks) == model
    legacy = build_context(prepared, blocks, projection_version=1)
    assert legacy.context_digest == canonical_digest(
        {
            "schema_version": 1,
            "agent_configuration_digest": prepared.researcher.configuration_digest,
            "product_snapshot_digest": original_digest,
            "research_context_digest": prepared.manifest.digest,
            "evidence_blocks": [b.identity() for b in blocks],
        }
    )
    assert legacy.product_context == original_content
    assert model.context_digest != legacy.context_digest


def test_large_claim_authority_and_assets_never_expand_researcher_view():
    current = product_geometry(11)
    expected = project_researcher_product(current)
    current["profile"]["allowed_claims"] = [str(i) + "q" * 497 for i in range(30)]
    current["profile"]["prohibited_claims"] = ["forbidden" * 60] * 30
    current["brand_profile"]["allowed_claims"] = ["brand claim" * 40] * 30
    current["assets"] *= 100
    current["future_private_section"] = {"internal": "never provided"}
    assert project_researcher_product(current) == expected


def test_projection_order_empty_values_and_nested_audiences_are_deterministic():
    value = {
        "brand": {"name": ""},
        "product": {"name": "Lamp", "category": None},
        "profile": {
            "description": "",
            "price": 0,
            "features": [],
            "target_audiences": [
                {
                    "name": "Readers",
                    "description": "Complete description",
                    "future_private": "omit",
                },
                {},
            ],
        },
        "brief": {"secondary_audiences": [], "offers": [None, "", "Free shipping"]},
    }
    expected = {
        "product": {"name": "Lamp"},
        "profile": {
            "price": 0,
            "target_audiences": [
                {"name": "Readers", "description": "Complete description"},
            ],
        },
        "brief": {"offers": ["Free shipping"]},
    }
    assert project_researcher_product(value) == expected
    assert project_researcher_product(dict(reversed(tuple(value.items())))) == expected
    assert project_researcher_product({"product": None}) == {}


def test_projected_digest_binds_full_snapshot_even_when_claims_are_not_sent():
    prepared, blocks = live_geometry()
    content = product_geometry(11)
    content["profile"]["allowed_claims"] = ["A different human-approved claim."]
    changed = replace(
        prepared,
        product_snapshot=replace(
            prepared.product_snapshot,
            content=content,
            digest=event_sha256_v1(
                {"schema_version": 2, "source_revision": 11, "content": content}
            ),
        ),
    )
    first, second = build_context(prepared, blocks), build_context(changed, blocks)
    assert first.product_context == second.product_context
    assert first.context_digest != second.context_digest
    with pytest.raises(ValueError, match="unsupported Researcher projection"):
        build_context(prepared, blocks, projection_version=99)


@pytest.mark.parametrize("reason", ["FIXED_CONTEXT_TOO_LARGE", "NO_EVIDENCE_BLOCK_FITS"])
@pytest.mark.asyncio
async def test_budget_denial_audits_numbers_only_without_run_reservation_or_network(
    reason, monkeypatch
):
    def forbidden(*args, **kwargs):
        raise AssertionError("network access forbidden")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    tenant_id, product_id = uuid4(), uuid4()
    private_text = "PRIVATE_PRODUCT_SENTINEL"
    prepared = preparation_with_blocks(
        preparation(tenant_id, product_id),
        ("PRIVATE_EVIDENCE_SENTINEL" * 320,),
        product_content={
            "profile": {
                "description": private_text * 300
                if reason == "FIXED_CONTEXT_TOO_LARGE"
                else private_text
            },
        },
    )
    provider = FakeModelProvider(
        ModelInvocationResult({}, None, ModelUsage(0, 0, 0), "openai", "gpt-5.6-sol")
    )
    runtime, repository, audit, outbox = service(prepared, provider)
    with pytest.raises(AgentContextBudgetExceeded) as raised:
        await runtime.request_researcher(
            context(tenant_id), product_id=product_id, idempotency_key="same-key"
        )
    diagnostic = raised.value.diagnostics
    assert diagnostic["reason"] == reason
    assert diagnostic["candidate_block_count"] == 1
    assert diagnostic["selected_block_count"] == 0
    assert diagnostic["projection_version"] == 2
    bound = diagnostic["first_rejected_block_bound"]
    assert isinstance(bound, int) and bound > 8000
    assert diagnostic["section_bytes"]["profile"] > 0
    assert repository.runs == {} and repository.reservations == [] and repository.attempts == {}
    assert provider.calls == [] and outbox.values == []
    assert len(audit.values) == 1
    assert audit.values[0].action == "agent.run.context_budget_denied"
    assert audit.values[0].agent_run_id is None
    serialized = audit.values[0].safe_metadata.canonical_json
    assert private_text not in serialized and "PRIVATE_EVIDENCE_SENTINEL" not in serialized
    assert json.loads(serialized) == diagnostic
    assert len(serialized.encode()) < 4096
    # A rejected admission doesn't bind the idempotency key or consume period budget.
    repository.prepared = preparation(tenant_id, product_id)
    accepted = await runtime.request_researcher(
        context(tenant_id), product_id=product_id, idempotency_key="same-key"
    )
    assert accepted.input_context_kind == "researcher.v2"
    assert accepted.input_context_schema_version == 2
    assert accepted.product_snapshot_digest == repository.prepared.product_snapshot.digest
    assert accepted.product_snapshot_id == repository.prepared.product_snapshot.id
    assert provider.calls == []


def test_empty_candidates_have_safe_diagnostics_and_no_partial_fields():
    prepared, _ = live_geometry()
    with pytest.raises(AgentContextBudgetExceeded) as raised:
        fit_researcher_evidence(prepared, (), 10000)
    assert raised.value.diagnostics["reason"] == "NO_EVIDENCE_BLOCK_FITS"
    assert raised.value.diagnostics["first_rejected_block_bound"] == 0
    full = "界" * 8000
    projected = project_researcher_product({"profile": {"description": full}})
    assert projected == {"profile": {"description": full}}
