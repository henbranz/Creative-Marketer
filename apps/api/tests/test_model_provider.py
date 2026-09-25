# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import Request, Response
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

from creative_marketer.agent_runtime.application import (
    initial_creative_strategist_route,
    initial_researcher_route,
)
from creative_marketer.agent_runtime.domain import (
    InvalidModelOutput,
    ModelImageInputRef,
    ModelIncompleteResponse,
    ModelInvocation,
    ModelProviderAuthenticationFailed,
    ModelProviderBadRequest,
    ModelProviderCancelledResponse,
    ModelProviderConflict,
    ModelProviderConnectionFailed,
    ModelProviderError,
    ModelProviderFailedResponse,
    ModelProviderInvalidResponse,
    ModelProviderModelUnavailable,
    ModelProviderPermissionDenied,
    ModelProviderSchemaUnsupported,
    ModelProviderServerError,
    ModelRateLimited,
    ModelRefusal,
    ModelTimeout,
    ProviderFailureReason,
    ProviderResponseStatus,
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
from creative_marketer.production.application import initial_producer_route
from tests.test_agent_runtime_domain import block, output


def invocation() -> ModelInvocation:
    return ModelInvocation(
        route=initial_researcher_route(),
        system_instructions="Only analyze supplied evidence.",
        trusted_product_context={"name": "Product"},
        untrusted_evidence=(block(),),
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [],
            "properties": {},
        },
        output_contract_key="research.research_snapshot",
        output_contract_version=1,
        max_output_tokens=6000,
        reasoning_effort="medium",
    )


def client_with(response=None, error=None):
    create = AsyncMock(return_value=response, side_effect=error)
    return SimpleNamespace(responses=SimpleNamespace(create=create)), create


@pytest.mark.asyncio
async def test_openai_adapter_rejects_schema_before_network_call() -> None:
    client, create = client_with()
    provider = OpenAIResponsesModelProvider("unit-live-credential", client=client)
    invalid = replace(
        invocation(),
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["value"],
            "properties": {"value": {"oneOf": [{"type": "string"}]}},
        },
    )
    with pytest.raises(ModelProviderSchemaUnsupported) as caught:
        await provider.generate_structured(invalid)
    assert caught.value.code == "MODEL_PROVIDER_SCHEMA_UNSUPPORTED"
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_wire_schema_is_normalized_without_mutating_canonical_constraints() -> None:
    canonical = {
        "type": "object",
        "required": ["values"],
        "additionalProperties": False,
        "properties": {
            "values": {"type": "array", "uniqueItems": True, "items": {"const": "EXISTING_ASSET"}}
        },
    }
    before = deepcopy(canonical)
    call = replace(invocation(), output_schema=canonical)
    response = SimpleNamespace(
        status="completed",
        output_text='{"values": ["EXISTING_ASSET"]}',
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15),
        id="offline",
        model="gpt-5.6-sol",
    )
    client, create = client_with(response)
    provider = OpenAIResponsesModelProvider("offline-only", client=client)
    provider.validate_invocation(call)
    await provider.generate_structured(call)
    wire = create.await_args.kwargs["text"]["format"]["schema"]
    assert canonical == before
    assert "uniqueItems" not in wire["properties"]["values"]
    assert wire["properties"]["values"]["items"] == {"enum": ["EXISTING_ASSET"]}


@pytest.mark.asyncio
async def test_openai_adapter_separates_untrusted_evidence_and_disables_tools_and_storage() -> None:
    call = invocation()
    response = SimpleNamespace(
        status="completed",
        output_text=__import__("json").dumps(output(call.untrusted_evidence[0])),
        usage=SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150),
        id="resp_1",
        model="gpt-5.6-sol",
    )
    client, create = client_with(response)
    provider = OpenAIResponsesModelProvider("unit-live-credential", client=client)
    result = await provider.generate_structured(call)
    assert result.provider_response_id == "resp_1"
    assert result.usage.total_tokens == 150
    parameters = create.await_args.kwargs
    assert parameters["model"] == "gpt-5.6-sol"
    assert parameters["reasoning"] == {"effort": "medium"}
    assert parameters["tools"] == []
    assert parameters["store"] is False
    assert parameters["text"]["format"]["strict"] is True
    assert "untrusted data" in parameters["instructions"]
    assert "The advertised price" in parameters["input"][0]["content"]


@pytest.mark.asyncio
async def test_creative_strategist_maps_sol_high_reasoning_contract() -> None:
    call = replace(
        invocation(),
        route=initial_creative_strategist_route(),
        max_output_tokens=8_000,
        reasoning_effort="high",
        output_contract_key="creative.creative_concept_set",
    )
    response = SimpleNamespace(
        status="completed",
        output_text="{}",
        output=(),
        usage=SimpleNamespace(input_tokens=20, output_tokens=10, total_tokens=30),
        id="resp_creative_sol",
        model="gpt-5.6-sol",
    )
    client, create = client_with(response)
    await OpenAIResponsesModelProvider("unit-live-credential", client=client).generate_structured(
        call
    )
    parameters = create.await_args.kwargs
    assert parameters["model"] == "gpt-5.6-sol"
    assert parameters["reasoning"] == {"effort": "high"}
    assert parameters["max_output_tokens"] == 8_000
    assert parameters["tools"] == []


class StaticImageMaterializer:
    async def materialize(self, _reference):
        return b"image", "image/png"


@pytest.mark.asyncio
async def test_sol_producer_maps_bounded_image_contract_with_zero_tools() -> None:
    image_reference = ModelImageInputRef(
        uuid4(),
        uuid4(),
        "sha256:" + __import__("hashlib").sha256(b"image").hexdigest(),
    )
    call = replace(
        invocation(),
        route=initial_producer_route(),
        max_output_tokens=12_000,
        reasoning_effort="high",
        output_contract_key="production.production_plan",
        image_inputs=(image_reference,),
    )
    response = SimpleNamespace(
        status="completed",
        output_text="{}",
        output=(),
        usage=SimpleNamespace(input_tokens=20, output_tokens=10, total_tokens=30),
        id="resp_producer_sol",
        model="gpt-5.6-sol",
    )
    client, create = client_with(response)
    await OpenAIResponsesModelProvider(
        "unit-live-credential",
        client=client,
        image_materializer=StaticImageMaterializer(),
    ).generate_structured(call)
    parameters = create.await_args.kwargs
    assert parameters["model"] == "gpt-5.6-sol"
    assert parameters["reasoning"] == {"effort": "high"}
    assert parameters["text"]["format"]["strict"] is True
    assert parameters["max_output_tokens"] == 12_000
    assert parameters["tools"] == []
    content = parameters["input"][0]["content"]
    assert [part["type"] for part in content] == ["input_text", "input_image"]
    assert content[1]["image_url"].startswith("data:image/png;base64,")


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
        (
            APIConnectionError(request=Request("POST", "https://api.openai.com")),
            ModelProviderConnectionFailed,
        ),
    ],
)
async def test_openai_adapter_normalizes_retryable_failures(error, expected) -> None:
    client, _ = client_with(error=error)
    with pytest.raises(expected):
        await OpenAIResponsesModelProvider(
            "unit-live-credential", client=client
        ).generate_structured(invocation())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, ModelProviderBadRequest),
        (408, ModelTimeout),
        (401, ModelProviderAuthenticationFailed),
        (403, ModelProviderPermissionDenied),
        (404, ModelProviderModelUnavailable),
        (409, ModelProviderConflict),
        (422, ModelProviderBadRequest),
        (500, ModelProviderServerError),
        (503, ModelProviderServerError),
    ],
)
async def test_openai_adapter_classifies_http_failures_without_leaking_payload(
    status, expected
) -> None:
    secret = "provider-payload-secret-never-print"
    response = Response(
        status,
        request=Request("POST", "https://api.openai.com"),
        headers={"Authorization": secret},
        json={"error": {"message": secret}},
    )
    client, _ = client_with(error=APIStatusError(secret, response=response, body=response.json()))

    with pytest.raises(expected) as caught:
        await OpenAIResponsesModelProvider(
            "unit-live-credential", client=client
        ).generate_structured(invocation())
    assert caught.value.code != "MODEL_PROVIDER_UNAVAILABLE"
    assert secret not in str(caught.value)


@pytest.mark.asyncio
async def test_openai_adapter_rejects_refusal_and_invalid_json() -> None:
    refused = SimpleNamespace(
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="content_filter"),
        id="resp_safety",
        model="gpt-5.6-sol",
        usage=SimpleNamespace(input_tokens=30, output_tokens=4, total_tokens=34),
    )
    client, _ = client_with(refused)
    with pytest.raises(ModelRefusal) as refusal:
        await OpenAIResponsesModelProvider(
            "unit-live-credential", client=client
        ).generate_structured(invocation())
    assert refusal.value.returned_response is not None
    assert refusal.value.returned_response.failure_reason is ProviderFailureReason.SAFETY
    assert refusal.value.returned_response.provider_response_id == "resp_safety"
    invalid = SimpleNamespace(
        status="completed",
        output_text="not-json",
        usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
        id="resp_2",
        model="gpt-5.6-sol",
    )
    client, _ = client_with(invalid)
    with pytest.raises(InvalidModelOutput) as invalid_output:
        await OpenAIResponsesModelProvider(
            "unit-live-credential", client=client
        ).generate_structured(invocation())
    assert invalid_output.value.returned_response is not None
    assert (
        invalid_output.value.returned_response.failure_reason
        is ProviderFailureReason.INVALID_OUTPUT
    )


@pytest.mark.asyncio
async def test_openai_adapter_rejects_incomplete_output_blocks_and_missing_text() -> None:
    incomplete = SimpleNamespace(
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        id="resp_incomplete",
        model="gpt-5.6-sol",
        usage=SimpleNamespace(input_tokens=100, output_tokens=8_000, total_tokens=8_100),
    )
    client, _ = client_with(incomplete)
    with pytest.raises(ModelIncompleteResponse) as caught:
        await OpenAIResponsesModelProvider(
            "unit-live-credential", client=client
        ).generate_structured(invocation())
    returned = caught.value.returned_response
    assert returned is not None
    assert returned.provider_response_id == "resp_incomplete"
    assert returned.status is ProviderResponseStatus.INCOMPLETE
    assert returned.failure_reason is ProviderFailureReason.MAX_OUTPUT_TOKENS
    assert returned.usage is not None and returned.usage.total_tokens == 8_100

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
@pytest.mark.parametrize(
    ("status", "error_type", "reason"),
    [
        ("failed", ModelProviderFailedResponse, ProviderFailureReason.PROVIDER_FAILED),
        (
            "cancelled",
            ModelProviderCancelledResponse,
            ProviderFailureReason.PROVIDER_CANCELLED,
        ),
    ],
)
async def test_openai_adapter_preserves_returned_failed_and_cancelled_metadata(
    status, error_type, reason
) -> None:
    response = SimpleNamespace(
        status=status,
        incomplete_details=None,
        id=f"resp_{status}",
        model="gpt-5.6-sol",
        usage=None,
    )
    client, _ = client_with(response)
    with pytest.raises(error_type) as caught:
        await OpenAIResponsesModelProvider(
            "unit-live-credential", client=client
        ).generate_structured(invocation())
    returned = caught.value.returned_response
    assert returned is not None
    assert returned.provider_response_id == f"resp_{status}"
    assert returned.failure_reason is reason
    assert returned.usage is None


@pytest.mark.asyncio
async def test_openai_adapter_preserves_completed_response_with_unavailable_usage() -> None:
    response = SimpleNamespace(
        status="completed",
        output=(),
        output_text="{}",
        id="resp_completed_no_usage",
        model="gpt-5.6-sol",
        usage=None,
    )
    client, _ = client_with(response)
    with pytest.raises(ModelProviderInvalidResponse) as caught:
        await OpenAIResponsesModelProvider(
            "unit-live-credential", client=client
        ).generate_structured(invocation())
    returned = caught.value.returned_response
    assert returned is not None
    assert returned.status is ProviderResponseStatus.COMPLETED
    assert returned.failure_reason is ProviderFailureReason.USAGE_UNAVAILABLE
    assert returned.usage is None


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
