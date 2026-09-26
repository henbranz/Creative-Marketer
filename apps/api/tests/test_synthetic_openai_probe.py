# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from scripts import synthetic_openai_probe as probe


@pytest.mark.asyncio
async def test_preview_is_offline_and_counts_every_current_contract(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        pytest.fail("preview cannot use network")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(probe, "Settings", lambda: pytest.fail("preview cannot load credentials"))
    result = await probe.preview()
    assert result["endpoint"] == "/v1/responses/input_tokens"
    assert result["contract_count"] == 6
    assert result["generation_requests"] == 0
    assert {item["agent_type"] for item in result["contracts"]} == {
        "researcher",
        "creative_strategist",
        "producer",
        "intelligence",
        "commerce_operations",
        "supervisor",
    }
    assert all(item["outcome"] == "accepted" for item in result["contracts"])


@pytest.mark.asyncio
async def test_gate_uses_only_input_token_count_endpoint_and_synthetic_data():
    calls: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"input_tokens": 123})

    results = await probe.count_contracts("offline-only", transport=httpx.MockTransport(reply))
    assert len(calls) == len(results) == 6
    for request, result in zip(calls, results, strict=True):
        assert request.method == "POST"
        assert request.url.path == "/v1/responses/input_tokens"
        body = json.loads(request.content)
        assert set(body) == {"model", "instructions", "input", "text", "reasoning", "tools"}
        assert body["tools"] == []
        assert body["text"]["format"]["strict"] is True
        assert "tenant" not in body["input"].lower()
        assert result["input_tokens"] == 123
        assert result["provider_schema_digest"].startswith("sha256:")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 429, 500])
async def test_rejection_records_only_safe_fields_without_retry(status):
    calls: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            status,
            headers={"x-request-id": "req_12345678"},
            json={
                "error": {
                    "type": "future_error",
                    "code": "future_code",
                    "param": "text.format.schema",
                    "message": "private-context-never-persist",
                }
            },
        )

    results = await probe.count_contracts("offline-only", transport=httpx.MockTransport(reply))
    assert len(calls) == 6
    assert all(item["outcome"] == "rejected" for item in results)
    assert all(item["error_code"] == "future_code" for item in results)
    assert "private-context" not in json.dumps(results)


@pytest.mark.asyncio
async def test_unknown_transport_stops_without_retrying_remaining_contracts():
    calls: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("secret must never escape")

    results = await probe.count_contracts("offline-only", transport=httpx.MockTransport(reply))
    assert len(calls) == 1
    assert results[0]["outcome"] == "unknown_transport_outcome_do_not_retry"


def test_execution_requires_approval_before_loading_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "Settings", lambda: pytest.fail("approval must come first"))
    with pytest.raises(ValueError, match="approval required"):
        probe.execute_approved("", tmp_path / "diagnostic.jsonl")


def test_approved_execution_uses_private_exclusive_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(
        probe, "Settings", lambda: SimpleNamespace(openai_api_key=SecretStr("offline-only"))
    )
    path = tmp_path / "diagnostic.jsonl"
    calls: list[str] = []

    async def fake_count(key):
        assert "started_outcome_unknown" in path.read_text()
        assert path.stat().st_mode & 0o777 == 0o600
        calls.append(key)
        return [{"outcome": "accepted"}]

    monkeypatch.setattr(probe, "count_contracts", fake_count)
    result = probe.execute_approved(probe.APPROVAL, path)
    assert result["generation_requests"] == 0
    assert calls == ["offline-only"]
    assert "offline-only" not in path.read_text()
    with pytest.raises(FileExistsError):
        probe.execute_approved(probe.APPROVAL, path)
