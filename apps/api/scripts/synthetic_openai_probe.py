"""Offline by default. One explicitly approved synthetic request; no runtime or DB use.

Never call the execution mode without separate operator approval. A successful HTTP
response is closed before its body is read; server-side generation may nevertheless
consume the entire tiny output allowance. No cancellation/refund guarantee is made.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from openai import AsyncOpenAI

from creative_marketer.agent_runtime.application import (
    initial_creative_strategist_route,
    initial_researcher_route,
    load_output_schema,
)
from creative_marketer.agent_runtime.domain import ModelInvocation, ModelProviderError
from creative_marketer.creative.application import load_creative_output_schema
from creative_marketer.infrastructure.model_providers.openai_responses import (
    OpenAIResponsesModelProvider,
)
from creative_marketer_api.config import Settings

OUTPUT_TOKENS = 32
MAX_REQUEST_BYTES = 8192
# UTF-8 byte upper bound for text tokens, including the whole schema/envelope, plus
# conservative framing allowance. This is an estimate, NOT a provider billing cap.
INPUT_TOKEN_ALLOWANCE = MAX_REQUEST_BYTES + 1024
PAIR_COST = (Decimal(INPUT_TOKEN_ALLOWANCE * 4 + OUTPUT_TOKENS * 20) / 1_000_000) * 2
APPROVAL = "I_APPROVE_ONE_SYNTHETIC_REQUEST"


def invocation(kind: str) -> ModelInvocation:
    if kind not in {"creative", "researcher"}:
        raise ValueError("unknown synthetic probe kind")
    creative = kind == "creative"
    return ModelInvocation(
        route=initial_creative_strategist_route() if creative else initial_researcher_route(),
        system_instructions="Synthetic contract validation only.",
        trusted_product_context={},
        untrusted_evidence=(),
        capability_context={} if creative else None,
        output_schema=load_creative_output_schema() if creative else load_output_schema(2),
        output_contract_key="creative.creative_concept_set"
        if creative
        else "research.research_snapshot",
        output_contract_version=1 if creative else 2,
        max_output_tokens=OUTPUT_TOKENS,
        reasoning_effort="high" if creative else "medium",
        output_task="Return the smallest valid synthetic result.",
    )


class AcceptedResponse(BaseException):
    """Escape SDK retry/error wrapping immediately on accepted response headers."""


class OneRequestGuard:
    def __init__(self) -> None:
        self.calls = 0
        self.status: int | None = None

    async def request(self, request: httpx.Request) -> None:
        self.calls += 1
        if (
            self.calls != 1
            or request.method != "POST"
            or str(request.url) != "https://api.openai.com/v1/responses"
            or len(request.content) > MAX_REQUEST_BYTES
        ):
            raise ValueError("synthetic request guard rejected request")
        body = json.loads(request.content)
        if (
            body["model"] != "gpt-5.6-sol"
            or body["max_output_tokens"] != OUTPUT_TOKENS
            or body["tools"] != []
            or body["store"] is not False
        ):
            raise ValueError("synthetic request guard rejected envelope")

    async def response(self, response: httpx.Response) -> None:
        self.status = response.status_code
        if response.is_success:
            await response.aclose()
            raise AcceptedResponse()


async def probe_once(
    kind: str, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    guard = OneRequestGuard()
    async with (
        httpx.AsyncClient(
            transport=transport,
            timeout=20,
            follow_redirects=False,
            trust_env=False,
            event_hooks={"request": [guard.request], "response": [guard.response]},
        ) as http,
        AsyncOpenAI(api_key=api_key, http_client=http, max_retries=0) as client,
    ):
        try:
            await OpenAIResponsesModelProvider(api_key, client=client).generate_structured(
                invocation(kind)
            )
        except AcceptedResponse:
            return {"outcome": "accepted_stopped", "http_status": guard.status}
        except ModelProviderError as error:
            if error.rejection is not None:
                return {"outcome": "rejected", **error.rejection.as_dict()}
            return {"outcome": "unknown_transport_outcome_do_not_retry"}
    raise RuntimeError("synthetic probe must stop at response headers")


async def preview(kind: str) -> dict[str, Any]:
    size = 0

    def offline_reply(request: httpx.Request) -> httpx.Response:
        nonlocal size
        size = len(request.content)
        return httpx.Response(200)

    await probe_once(kind, "offline-only", transport=httpx.MockTransport(offline_reply))
    call = invocation(kind)
    return {
        "mode": "offline_preview_no_network",
        "probe": kind,
        "model": call.route.model,
        "contract": call.output_contract_key,
        "contract_version": call.output_contract_version,
        "reasoning_effort": call.reasoning_effort,
        "request_bytes": size,
        "max_request_bytes": MAX_REQUEST_BYTES,
        "conservative_input_token_allowance": INPUT_TOKEN_ALLOWANCE,
        "max_output_tokens": OUTPUT_TOKENS,
        "two_probe_conservative_usd_exposure": str(PAIR_COST),
        "tools": [],
        "store": False,
        "operator_approval_required": True,
    }


def execute_approved(kind: str, approval: str, output: Path) -> dict[str, Any]:
    if approval != APPROVAL:
        raise ValueError("explicit synthetic probe approval required")
    settings = Settings()  # The same repository-root dotenv/Settings secret path.
    if settings.openai_api_key is None:
        raise ValueError("OpenAI credential is not configured")
    # Fail closed before network I/O if a private, exclusive diagnostic artifact cannot
    # be opened. Never overwrite an earlier result; no schema or output is written.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as artifact:
        artifact.write('{"outcome":"started_outcome_unknown_do_not_retry"}\n')
        artifact.flush()
        os.fsync(artifact.fileno())
        result = asyncio.run(probe_once(kind, settings.openai_api_key.get_secret_value()))
        artifact.write(json.dumps(result) + "\n")
        artifact.flush()
        os.fsync(artifact.fileno())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("creative", "researcher"))
    parser.add_argument("--execute-approved", default="")
    parser.add_argument("--diagnostic-file", type=Path)
    args = parser.parse_args()
    # SDK debug configuration must never leak headers/body through this operator CLI.
    logging.disable(logging.CRITICAL)
    try:
        if args.execute_approved:
            if args.diagnostic_file is None:
                raise ValueError("private diagnostic file is required")
            result = execute_approved(args.kind, args.execute_approved, args.diagnostic_file)
        else:
            result = asyncio.run(preview(args.kind))
    except Exception:
        print('{"outcome":"stopped_safely_do_not_retry"}')
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
