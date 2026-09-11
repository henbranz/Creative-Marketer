# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import Request, Response
from openai import APIConnectionError, APITimeoutError, RateLimitError

from creative_marketer.agent_runtime.domain import (
    InvalidModelOutput,
    ModelInvocation,
    ModelProviderError,
    ModelRateLimited,
    ModelRefusal,
    ModelTimeout,
)
from creative_marketer.infrastructure.model_providers.openai_responses import (
    OpenAIResponsesModelProvider,
)
from tests.test_agent_runtime_domain import block, output, route


def invocation() -> ModelInvocation:
    return ModelInvocation(
        route=route(),
        system_instructions="Only analyze supplied evidence.",
        trusted_product_context={"name": "Product"},
        untrusted_evidence=(block(),),
        output_schema={"type": "object"},
        output_contract_key="research.research_snapshot",
        output_contract_version=1,
        max_output_tokens=6000,
        reasoning_effort="medium",
    )


def client_with(response=None, error=None):
    create = AsyncMock(return_value=response, side_effect=error)
    return SimpleNamespace(responses=SimpleNamespace(create=create)), create


@pytest.mark.asyncio
async def test_openai_adapter_separates_untrusted_evidence_and_disables_tools_and_storage() -> None:
    call = invocation()
    response = SimpleNamespace(
        status="completed",
        output_text=__import__("json").dumps(output(call.untrusted_evidence[0])),
        usage=SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150),
        id="resp_1",
        model="gpt-5.6-terra",
    )
    client, create = client_with(response)
    provider = OpenAIResponsesModelProvider("unit-live-credential", client=client)
    result = await provider.generate_structured(call)
    assert result.provider_response_id == "resp_1"
    assert result.usage.total_tokens == 150
    parameters = create.await_args.kwargs
    assert parameters["tools"] == []
    assert parameters["store"] is False
    assert parameters["text"]["format"]["strict"] is True
    assert "untrusted data" in parameters["instructions"]
    assert "The advertised price" in parameters["input"][0]["content"]


@pytest.mark.parametrize("key", ["", "disabled-key", "test-key", "fake-key", "replace-key"])
def test_openai_adapter_rejects_missing_or_placeholder_credentials(key: str) -> None:
    with pytest.raises(ValueError):
        OpenAIResponsesModelProvider(key)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            RateLimitError(
                "limited",
                response=Response(429, request=Request("POST", "https://api.openai.com")),
                body=None,
            ),
            ModelRateLimited,
        ),
        (APITimeoutError(request=Request("POST", "https://api.openai.com")), ModelTimeout),
        (APIConnectionError(request=Request("POST", "https://api.openai.com")), ModelProviderError),
    ],
)
async def test_openai_adapter_normalizes_retryable_failures(error, expected) -> None:
    client, _ = client_with(error=error)
    with pytest.raises(expected):
        await OpenAIResponsesModelProvider(
            "unit-live-credential", client=client
        ).generate_structured(invocation())


@pytest.mark.asyncio
async def test_openai_adapter_rejects_refusal_and_invalid_json() -> None:
    refused = SimpleNamespace(
        status="incomplete", incomplete_details=SimpleNamespace(reason="content_filter")
    )
    client, _ = client_with(refused)
    with pytest.raises(ModelRefusal):
        await OpenAIResponsesModelProvider(
            "unit-live-credential", client=client
        ).generate_structured(invocation())
    invalid = SimpleNamespace(
        status="completed",
        output_text="not-json",
        usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
        id="resp_2",
        model="gpt-5.6-terra",
    )
    client, _ = client_with(invalid)
    with pytest.raises(InvalidModelOutput):
        await OpenAIResponsesModelProvider(
            "unit-live-credential", client=client
        ).generate_structured(invocation())
