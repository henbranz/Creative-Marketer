# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from openai import APIStatusError, AsyncOpenAI
from pydantic import SecretStr

from creative_marketer.agent_runtime.application import initial_creative_strategist_route
from creative_marketer.agent_runtime.domain import (
    ModelProviderAuthenticationFailed,
    ModelProviderBadRequest,
)
from creative_marketer.agent_runtime.provider_diagnostics import ProviderRejection, diagnostic_token
from creative_marketer.creative.application import load_creative_output_schema
from creative_marketer.infrastructure.model_providers.openai_diagnostics import (
    safe_status_diagnostic,
)
from creative_marketer.infrastructure.model_providers.openai_responses import (
    OpenAIResponsesModelProvider,
)
from creative_marketer.observability.logging import SafeJsonFormatter
from scripts import agent_request_preflight as gate
from tests.test_model_provider import invocation


@pytest.mark.parametrize("nested", [False, True])
def test_allowlisted_error_fields_only(nested):
    body = {
        "type": "invalid_request_error",
        "code": "invalid_json_schema",
        "param": "text.format.schema",
        "message": "private-product-and-secret",
    }
    error = APIStatusError(
        "private-product-and-secret",
        body={"error": body} if nested else body,
        response=httpx.Response(
            400,
            headers={"x-request-id": "req_12345678", "secret": "private-product-and-secret"},
            request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
        ),
    )
    diagnostic = safe_status_diagnostic(error)
    assert diagnostic == {
        "http_status": 400,
        "error_type": "invalid_request_error",
        "error_code": "invalid_json_schema",
        "param": "text.format.schema",
        "request_id": "req_12345678",
        "exception_class": "APIStatusError",
    }
    assert "private-product-and-secret" not in json.dumps(diagnostic)


@pytest.mark.parametrize(
    "body",
    [
        None,
        "private-secret",
        {"code": {"private": "secret"}, "type": ["secret"], "param": "sk-private-secret"},
    ],
)
def test_malformed_or_non_allowlisted_diagnostics_are_redacted(body):
    error = APIStatusError(
        "secret",
        body=body,
        response=httpx.Response(
            400,
            headers={"x-request-id": "sk-private-secret"},
            request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
        ),
    )
    result = safe_status_diagnostic(error)
    assert "secret" not in json.dumps(result)
    assert result["request_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected,exception_class",
    [
        (400, ModelProviderBadRequest, "BadRequestError"),
        (401, ModelProviderAuthenticationFailed, "AuthenticationError"),
    ],
)
async def test_real_sdk_status_mapping_and_safe_logs(status, expected, exception_class, caplog):
    def reply(request):
        return httpx.Response(
            status,
            json={
                "error": {
                    "type": "invalid_request_error",
                    "code": "invalid_json_schema",
                    "param": "text.format.schema",
                    "message": "never-log-product-or-key",
                }
            },
            headers={"x-request-id": "req_12345678"},
        )

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http,
        AsyncOpenAI(api_key="never-log-product-or-key", http_client=http, max_retries=0) as client,
    ):
        with pytest.raises(expected) as caught:
            await OpenAIResponsesModelProvider("offline-only", client=client).generate_structured(
                invocation()
            )
    assert str(caught.value) == "OpenAI request failed"
    assert caught.value.rejection.request_id == "req_12345678"
    assert caught.value.rejection.exception_class == exception_class
    assert type(caught.value.__cause__).__name__ == exception_class
    assert exception_class in caplog.text
    assert "req_12345678" in caplog.text
    assert "never-log-product-or-key" not in caplog.text
    record = next(item for item in caplog.records if hasattr(item, "safe_fields"))
    structured = json.loads(SafeJsonFormatter("test", "test").format(record))
    assert structured["provider.error_code"] == "invalid_json_schema"
    assert structured["provider.param"] == "text.format.schema"
    assert structured["provider.request_id"] == "req_12345678"
    assert "never-log-product-or-key" not in json.dumps(structured)


def creative_invocation():
    return replace(
        invocation(),
        route=initial_creative_strategist_route(),
        reasoning_effort="high",
        max_output_tokens=8000,
        output_contract_key="creative.creative_concept_set",
        output_schema=load_creative_output_schema(),
        capability_context={"product_brand_data": {"name": "never-print-product"}},
    )


@pytest.mark.asyncio
async def test_exact_creative_schema_and_real_sdk_serialize_offline(monkeypatch):
    async def forbid(*args, **kwargs):
        pytest.fail("network transport must not be used")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbid)
    result = await gate.inspect_invocation(creative_invocation(), 14647, 32000)
    assert result["schema_bytes"] == 4812
    assert result["schema_container_depth"] == 7
    assert result["object_schemas"] == 9
    assert result["refs"] == 7
    assert result["anyof_branches"] == 2
    assert result["contract_name"] == "creative_creative_concept_set_v1"
    assert result["max_output_tokens"] == 8000
    assert result["reasoning_effort"] == "high"
    assert result["tools"] == [] and result["store"] is False
    assert result["provider_acceptance"] == "UNVERIFIED"
    assert result["retry_authorized"] is False
    assert "never-print-product" not in json.dumps(result)


@pytest.mark.parametrize(
    "value", ["future_provider_code", "text.format.schema", "x:y-z.v2", "A" * 128]
)
def test_unknown_machine_tokens_survive(value):
    assert diagnostic_token(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "A" * 129,
        "free text",
        "line\nbreak",
        '"quoted"',
        "{private}",
        "[private]",
        "https://example.com",
        "https:example.com",
        "www.example.com",
        "mailto:private",
        "sk-private-secret",
        "Bearer-token",
        "a" * 16 + "." + "b" * 8 + "." + "c" * 8,
        {"message": "secret"},
        ["private"],
        42,
    ],
)
def test_untrusted_diagnostic_content_is_redacted(value):
    assert diagnostic_token(value) == "REDACTED"


def test_pure_diagnostic_value_enforces_bounds_and_does_not_retain_input():
    assert diagnostic_token(None) is None
    value = ProviderRejection(400, param="private product text", request_id="sk-private")
    assert value.param == "REDACTED"
    assert value.request_id is None
    assert "private" not in repr(value)
    for status in [200, 600, True, "400"]:
        with pytest.raises(ValueError, match="invalid provider rejection status"):
            ProviderRejection(status)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,bound,total",
    [
        ({"reasoning_effort": "unsupported"}, 1, 32000),
        ({"max_output_tokens": 128001}, 1, 200000),
        ({}, 25000, 32000),
        ({"output_contract_key": "bad name"}, 1, 32000),
    ],
)
async def test_gate_rejects_bad_request_settings(changes, bound, total):
    with pytest.raises(ValueError):
        await gate.inspect_invocation(replace(creative_invocation(), **changes), bound, total)


@pytest.mark.parametrize(
    "changes",
    [
        {"model_provider_backend": "fake"},
        {"agent_workload_id": "local-fake-agent-worker"},
        {"openai_api_key": None},
        {"openai_api_key": SecretStr("fake-placeholder")},
    ],
)
def test_gate_rejects_unready_configuration(changes):
    settings = SimpleNamespace(
        **{
            "model_provider_backend": "openai",
            "agent_workload_id": "local-live-agent-worker",
            "openai_api_key": SecretStr("local-configured"),
            **changes,
        }
    )
    with pytest.raises(ValueError):
        gate.check_settings(settings)


@pytest.mark.asyncio
async def test_gate_reads_in_read_only_tenant_transaction_without_claim(monkeypatch):
    route = initial_creative_strategist_route()
    tenant, run_id = uuid4(), uuid4()
    run = SimpleNamespace(
        tenant_id=tenant,
        agent_type="creative_strategist",
        resolved_model_route_version=route.route_version,
        resolved_model=route.model,
        resolved_provider=route.provider,
        pricing_version=route.pricing.version,
        reasoning_effort=route.reasoning_effort,
        max_output_tokens=8000,
        max_total_tokens=32000,
        input_context_kind="creative_strategy.v1",
    )
    session = AsyncMock()
    session.__aenter__.return_value = session
    repo = SimpleNamespace(
        get=AsyncMock(return_value=run), resolve_context=AsyncMock(return_value=object())
    )
    settings = SimpleNamespace(
        database_url="not-used",
        model_provider_backend="openai",
        agent_workload_id="local-live-agent-worker",
        openai_api_key=SecretStr("configured"),
    )
    monkeypatch.setattr(gate, "create_session_factory", lambda _: lambda: session)
    monkeypatch.setattr(gate, "SqlAlchemyAgentRunRepository", lambda _: repo)
    monkeypatch.setattr(
        gate,
        "default_capability_registry",
        lambda: SimpleNamespace(
            resolve=lambda _: SimpleNamespace(invocation=lambda *_: creative_invocation())
        ),
    )
    monkeypatch.setattr(gate, "conservative_creative_input_token_bound", lambda _: 14647)
    result = await gate.inspect_run(settings, tenant, run_id)
    assert str(session.execute.call_args_list[0].args[0]) == "SET TRANSACTION READ ONLY"
    assert session.execute.call_args_list[1].args[1] == {"tenant": str(tenant)}
    session.commit.assert_not_called()
    session.rollback.assert_awaited_once()
    assert result["retry_authorized"] is False
