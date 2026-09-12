# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import Request, Response
from openai import APIConnectionError, APITimeoutError, RateLimitError

from creative_marketer.agent_runtime.domain import (
    InvalidModelOutput,
    ModelImageInputRef,
    ModelInvocation,
    ModelProviderError,
    ModelRateLimited,
    ModelRefusal,
    ModelTimeout,
)
from creative_marketer.infrastructure.model_providers.asset_images import (
    DatabaseObjectStoreImageMaterializer,
)
from creative_marketer.infrastructure.model_providers.execution_process_only import (
    ExecutionProcessOnlyModelProvider,
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


@pytest.mark.asyncio
async def test_openai_adapter_rejects_incomplete_output_blocks_and_missing_text() -> None:
    incomplete = SimpleNamespace(
        status="incomplete", incomplete_details=SimpleNamespace(reason="max_output_tokens")
    )
    client, _ = client_with(incomplete)
    with pytest.raises(ModelProviderError):
        await OpenAIResponsesModelProvider(
            "unit-live-credential", client=client
        ).generate_structured(invocation())

    refusal = SimpleNamespace(
        status="completed",
        output=(SimpleNamespace(content=(SimpleNamespace(type="refusal"),)),),
    )
    client, _ = client_with(refusal)
    with pytest.raises(ModelRefusal):
        await OpenAIResponsesModelProvider(
            "unit-live-credential", client=client
        ).generate_structured(invocation())

    missing_text = SimpleNamespace(status="completed", output=(), output_text=None)
    client, _ = client_with(missing_text)
    with pytest.raises(InvalidModelOutput):
        await OpenAIResponsesModelProvider(
            "unit-live-credential", client=client
        ).generate_structured(invocation())


@pytest.mark.asyncio
async def test_api_process_model_provider_cannot_invoke_models() -> None:
    with pytest.raises(ModelProviderError, match="worker process"):
        await ExecutionProcessOnlyModelProvider().generate_structured(invocation())


class MaterializerSession:
    def __init__(self, row):
        self.row = row
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def begin(self):
        return self

    async def execute(self, _statement, _parameters=None):
        self.calls += 1
        return SimpleNamespace(first=lambda: self.row if self.calls == 2 else None)


class MaterializerStore:
    def __init__(self, content: bytes):
        self.content = content

    async def stream(self, *, key: str) -> AsyncIterator[bytes]:
        assert key == "private/object.png"
        yield self.content[:4]
        yield self.content[4:]


@pytest.mark.asyncio
async def test_database_image_materializer_revalidates_policy_digest_and_size() -> None:
    tenant_id, asset_id = uuid4(), uuid4()
    digest = "sha256:" + "a" * 64
    reference = ModelImageInputRef(tenant_id, asset_id, digest)
    valid = SimpleNamespace(
        _mapping={
            "status": "ready",
            "kind": "image",
            "rights_status": "confirmed",
            "allowed_uses": ["generation_input"],
            "digest": digest,
            "object_key": "private/object.png",
            "detected_mime_type": "image/png",
        }
    )
    session = MaterializerSession(valid)
    materializer = DatabaseObjectStoreImageMaterializer(
        lambda: session,
        MaterializerStore(b"image-bytes"),
    )
    assert await materializer.materialize(reference) == (b"image-bytes", "image/png")

    missing = MaterializerSession(None)
    with pytest.raises(ValueError, match="unavailable"):
        await DatabaseObjectStoreImageMaterializer(
            lambda: missing,
            MaterializerStore(b"x"),
        ).materialize(reference)
    invalid = SimpleNamespace(_mapping={**valid._mapping, "rights_status": "unknown"})
    with pytest.raises(ValueError, match="integrity policy"):
        await DatabaseObjectStoreImageMaterializer(
            lambda: MaterializerSession(invalid),
            MaterializerStore(b"x"),
        ).materialize(reference)
    with pytest.raises(ValueError, match="materialization bound"):
        await DatabaseObjectStoreImageMaterializer(
            lambda: MaterializerSession(valid),
            MaterializerStore(b"too-large"),
            maximum_bytes=2,
        ).materialize(reference)
