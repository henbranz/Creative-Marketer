# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

import json
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from jsonschema import Draft202012Validator
from openai import AsyncOpenAI

from creative_marketer.agent_runtime.application import (
    ModelProviderRegistry,
    build_creative_model_context,
)
from creative_marketer.agent_runtime.domain import (
    AgentRunStatus,
    ModelAttemptStatus,
    ModelInvocationResult,
    ModelUsage,
)
from creative_marketer.catalog.domain import ProductKnowledgeSnapshot
from creative_marketer.creative.domain import ChannelIntent, CreativeStrategyRequest
from creative_marketer.events.domain import event_sha256_v1
from creative_marketer.infrastructure.model_providers.fake import FakeModelProvider
from creative_marketer.infrastructure.model_providers.openai_responses import (
    OpenAIResponsesModelProvider,
)
from tests.test_agent_runtime_application import (
    context,
    creative_preparation,
    preparation,
    service,
)
from tests.test_agent_runtime_domain import output as research_output
from tests.test_creative_strategy import output as creative_output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "has_claims,invalid", [(True, False), (True, True), (False, False), (False, True)]
)
async def test_live_style_creative_claim_flow_without_network(has_claims, invalid):
    tenant_id, product_id = uuid4(), uuid4()
    prepared = preparation(tenant_id, product_id)
    fake = FakeModelProvider(
        lambda invocation: ModelInvocationResult(
            research_output(invocation.untrusted_evidence[0]),
            "fake-research",
            ModelUsage(100, 50, 150),
            "openai",
            "gpt-5.6-sol",
        )
    )
    runtime, repository, audit, outbox = service(prepared, fake)
    research_run = await runtime.request_researcher(
        context(tenant_id), product_id=product_id, idempotency_key="claim-test-research"
    )
    await runtime.execute(tenant_id, research_run.id)
    research = next(iter(repository.snapshots.values()))
    creative = creative_preparation(prepared, research)
    request = CreativeStrategyRequest(3, ChannelIntent.TIKTOK)
    original_context = build_creative_model_context(creative, request)[0]
    raw = creative_output(original_context)
    if not has_claims:
        content = {
            "profile": {"allowed_claims": []},
            "brand_profile": {"allowed_claims": []},
            "brief": {"required_disclaimers": []},
            "assets": [],
        }
        snapshot = ProductKnowledgeSnapshot(
            tenant_id=tenant_id,
            product_id=product_id,
            source_revision=2,
            schema_version=2,
            content=content,
            created_by=uuid4(),
            digest=event_sha256_v1({"schema_version": 2, "source_revision": 2, "content": content}),
        )
        creative = replace(creative, product_snapshot=snapshot)
        for concept in raw["concepts"]:
            concept["message_points"][0].update(
                kind="CTA", text="Discover more", product_claim_ref=None
            )
    for concept in raw["concepts"]:
        concept["supporting_research_refs"][0]["finding_key"] = original_context.research_findings[
            0
        ]["key"]
    if invalid:
        raw["concepts"][2]["message_points"][0].update(
            kind="PRODUCT_FACT", product_claim_ref="sha256:" + "f" * 64
        )
    repository.creative_prepared = creative
    requested = await runtime.request_creative_strategist(
        context(tenant_id),
        product_id=product_id,
        request=request,
        idempotency_key="claim-test-creative",
    )
    captured = []

    def respond(request):
        body = json.loads(request.content)
        captured.append(body)
        assert body["store"] is False
        assert body["tools"] == []
        assert "Copy the key verbatim" in body["input"][0]["content"]
        validator = Draft202012Validator(body["text"]["format"]["schema"])
        assert validator.is_valid(raw) is not invalid
        # A deliberately nonconforming mock response must ALSO fail in the domain.
        return httpx.Response(
            200,
            json={
                "id": "resp_unit_claim_validation",
                "object": "response",
                "created_at": 1,
                "model": "gpt-5.6-sol",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_unit",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": json.dumps(raw), "annotations": []}
                        ],
                    }
                ],
                "usage": {"input_tokens": 2866, "output_tokens": 7936, "total_tokens": 10802},
            },
        )

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http,
        AsyncOpenAI(api_key="unit-not-a-real-key", http_client=http, max_retries=0) as client,
    ):
        runtime.providers = ModelProviderRegistry(
            {"openai": OpenAIResponsesModelProvider("unit-not-a-real-key", client=client)}
        )
        result = await runtime.execute(tenant_id, requested.id)
    assert len(captured) == 1
    assert result.estimated_cost == Decimal("0.170184")
    assert result.total_tokens == 10802
    attempt = next(x for x in repository.attempts.values() if x.agent_run_id == requested.id)
    assert attempt.status is ModelAttemptStatus.SUCCEEDED
    assert attempt.unknown_cost == 0
    assert attempt.provider_response_id == "resp_unit_claim_validation"
    diagnostics = [x for x in audit.values if x.action == "creative.claim_validation.failed"]
    if invalid:
        assert result.status is AgentRunStatus.FAILED
        assert result.failure_code == "INVALID_CREATIVE_CLAIM_REFERENCE"
        assert len(repository.snapshots) == 1  # Research only; no partial Creative set.
        assert not any(x.event_type == "creative.concept_set.created.v1" for x in outbox.values)
        assert len(diagnostics) == 1
        diagnostic = diagnostics[0]
        assert diagnostic.tenant_id == tenant_id
        assert diagnostic.agent_run_id == requested.id
        assert diagnostic.attempt_id == attempt.id
        fields = json.loads(diagnostic.safe_metadata.canonical_json)
        assert fields["mismatch_count"] == 1
        assert fields["allowed_reference_count"] == int(has_claims)
        assert fields["mismatches"][0]["category"] == (
            "UNKNOWN_REFERENCE" if has_claims else "NO_ALLOWED_CLAIMS"
        )
        assert "Made from recycled steel" not in str(fields)
    else:
        assert result.status is AgentRunStatus.SUCCEEDED
        assert result.result_ref.startswith("creative-concept-set://")
        assert diagnostics == []
