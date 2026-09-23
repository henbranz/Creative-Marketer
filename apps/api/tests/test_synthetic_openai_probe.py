# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from creative_marketer.agent_runtime.application import load_output_schema
from creative_marketer.creative.application import load_creative_output_schema
from scripts import synthetic_openai_probe as probe


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,version,effort", [("creative", 1, "high"), ("researcher", 2, "medium")]
)
async def test_preview_is_offline_and_uses_canonical_schema(monkeypatch, kind, version, effort):
    async def forbidden(*_args, **_kwargs):
        pytest.fail("preview cannot use network")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(probe, "Settings", lambda: pytest.fail("preview cannot load credentials"))
    result = await probe.preview(kind)
    assert result["request_bytes"] < 8192
    assert result["max_output_tokens"] == 32
    assert result["contract_version"] == version
    assert result["reasoning_effort"] == effort
    assert result["operator_approval_required"]
    assert Decimal("0.075008") == probe.PAIR_COST
    call = probe.invocation(kind)
    assert call.trusted_product_context == {} and call.untrusted_evidence == ()
    assert call.image_inputs == ()
    expected = load_creative_output_schema() if kind == "creative" else load_output_schema(2)
    assert call.output_schema == expected


class ForbiddenBody(httpx.AsyncByteStream):
    async def __aiter__(self):
        pytest.fail("accepted model output must not be read")
        yield b""  # pragma: no cover


@pytest.mark.asyncio
async def test_accepted_probe_stops_at_headers_without_reading_output():
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(200, stream=ForbiddenBody())

    result = await probe.probe_once(
        "creative", "offline-only", transport=httpx.MockTransport(reply)
    )
    assert result == {"outcome": "accepted_stopped", "http_status": 200}
    assert len(calls) == 1
    body = json.loads(calls[0].content)
    assert body["tools"] == [] and body["store"] is False
    assert body["model"] == "gpt-5.6-sol"
    assert body["max_output_tokens"] == 32


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 429, 500])
async def test_rejected_probe_records_only_safe_fields_without_retry(status):
    calls = []

    def reply(request):
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

    result = await probe.probe_once(
        "researcher", "offline-only", transport=httpx.MockTransport(reply)
    )
    assert len(calls) == 1
    assert result["outcome"] == "rejected" and result["error_code"] == "future_code"
    assert result["request_id"] == "req_12345678"
    assert "private-context" not in json.dumps(result)


@pytest.mark.asyncio
async def test_probe_does_not_retry_unknown_transport_outcome():
    calls = []

    def reply(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret must never escape")

    result = await probe.probe_once(
        "creative", "offline-only", transport=httpx.MockTransport(reply)
    )
    assert len(calls) == 1
    assert result == {"outcome": "unknown_transport_outcome_do_not_retry"}


def test_execution_requires_explicit_approval_before_loading_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "Settings", lambda: pytest.fail("must require approval first"))
    with pytest.raises(ValueError, match="approval required"):
        probe.execute_approved("creative", "", tmp_path / "diagnostic.jsonl")


def test_approved_execution_requires_private_exclusive_durable_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(
        probe, "Settings", lambda: SimpleNamespace(openai_api_key=SecretStr("offline-only"))
    )
    path = tmp_path / "diagnostic.jsonl"
    calls = []

    async def fake_probe(kind, key):
        assert "started_outcome_unknown" in path.read_text()
        assert path.stat().st_mode & 0o777 == 0o600
        calls.append((kind, key))
        return {"outcome": "rejected", "http_status": 400}

    monkeypatch.setattr(probe, "probe_once", fake_probe)
    probe.execute_approved("creative", probe.APPROVAL, path)
    assert len(calls) == 1
    assert "offline-only" not in path.read_text()
    assert json.loads(path.read_text().splitlines()[-1])["http_status"] == 400
    with pytest.raises(FileExistsError):
        probe.execute_approved("creative", probe.APPROVAL, path)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_guard_rejects_redirects_oversized_or_changed_envelope_and_second_request():
    async def check(body, url="https://api.openai.com/v1/responses"):
        await probe.OneRequestGuard().request(httpx.Request("POST", url, content=body))

    with pytest.raises(ValueError):
        await check(b"{}", "https://example.com")
    with pytest.raises(ValueError):
        await check(b"x" * 8193)
    body = {"model": "gpt-5.6-sol", "max_output_tokens": 32, "tools": [], "store": False}
    for key, value in [
        ("model", "other"),
        ("max_output_tokens", 33),
        ("tools", [{}]),
        ("store", True),
    ]:
        with pytest.raises(ValueError):
            await check(json.dumps({**body, key: value}).encode())
    guard = probe.OneRequestGuard()
    request = httpx.Request("POST", "https://api.openai.com/v1/responses", json=body)
    await guard.request(request)
    with pytest.raises(ValueError):
        await guard.request(request)
