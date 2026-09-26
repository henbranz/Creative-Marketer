"""OpenAI structured-contract gate using input-token counting, never generation."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from creative_marketer.agent_runtime.application import (
    default_capability_registry,
    initial_commerce_operations_route,
    initial_creative_strategist_route,
    initial_intelligence_route,
    initial_researcher_route,
    initial_supervisor_route,
)
from creative_marketer.agent_runtime.domain import ModelRoute
from creative_marketer.infrastructure.model_providers.openai_diagnostics import (
    provider_rejection,
)
from creative_marketer.infrastructure.model_providers.openai_schema import (
    compile_openai_strict_output_schema,
)
from creative_marketer.production.application import initial_producer_route
from creative_marketer_api.config import Settings

APPROVAL = "I_APPROVE_OPENAI_INPUT_TOKEN_CONTRACT_GATE"
MAX_REQUEST_BYTES = 512_000
_ROUTES = {
    "researcher": initial_researcher_route,
    "creative_strategist": initial_creative_strategist_route,
    "producer": initial_producer_route,
    "intelligence": initial_intelligence_route,
    "commerce_operations": initial_commerce_operations_route,
    "supervisor": initial_supervisor_route,
}


@dataclass(frozen=True, slots=True)
class ContractCase:
    agent_type: str
    contract_key: str
    contract_version: int
    route: ModelRoute
    provider_schema: dict[str, object]
    provider_schema_digest: str
    compiler_revision: str


def contract_cases() -> tuple[ContractCase, ...]:
    cases: list[ContractCase] = []
    for contract in default_capability_registry().output_contracts():
        compiled = compile_openai_strict_output_schema(
            contract.schema, contract_key=contract.key, contract_version=contract.version
        )
        cases.append(
            ContractCase(
                contract.agent_type,
                contract.key,
                contract.version,
                _ROUTES[contract.agent_type](),
                dict(compiled.schema),
                compiled.digest,
                compiled.compiler_revision,
            )
        )
    return tuple(cases)


def _parameters(case: ContractCase) -> dict[str, Any]:
    return {
        "model": case.route.model,
        "instructions": "Validate this synthetic structured-output contract.",
        "input": "Synthetic contract validation only.",
        "text": {
            "format": {
                "type": "json_schema",
                "name": case.contract_key.replace(".", "_") + f"_v{case.contract_version}",
                "schema": case.provider_schema,
                "strict": True,
            }
        },
        "reasoning": {"effort": case.route.reasoning_effort},
        "tools": [],
    }


async def count_contracts(
    api_key: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> list[dict[str, Any]]:
    async with (
        httpx.AsyncClient(
            transport=transport,
            timeout=20,
            follow_redirects=False,
            trust_env=False,
        ) as http,
        AsyncOpenAI(api_key=api_key, http_client=http, max_retries=0) as client,
    ):
        results: list[dict[str, Any]] = []
        for case in contract_cases():
            metadata: dict[str, Any] = {
                "agent_type": case.agent_type,
                "output_contract_key": case.contract_key,
                "output_contract_version": case.contract_version,
                "model": case.route.model,
                "compiler_revision": case.compiler_revision,
                "provider_schema_digest": case.provider_schema_digest,
            }
            parameters = _parameters(case)
            if len(json.dumps(parameters, separators=(",", ":")).encode()) > MAX_REQUEST_BYTES:
                raise ValueError("provider count request exceeds local bound")
            try:
                response = await client.responses.input_tokens.count(**parameters)
            except APIStatusError as error:
                rejection = provider_rejection(error)
                results.append({**metadata, "outcome": "rejected", **rejection.as_dict()})
            except (APIConnectionError, APITimeoutError):
                results.append({**metadata, "outcome": "unknown_transport_outcome_do_not_retry"})
                break
            else:
                results.append(
                    {
                        **metadata,
                        "outcome": "accepted",
                        "input_tokens": int(response.input_tokens),
                    }
                )
        return results


async def preview() -> dict[str, Any]:
    requests: list[httpx.Request] = []

    def offline_reply(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"input_tokens": 1})

    results = await count_contracts("offline-only", transport=httpx.MockTransport(offline_reply))
    if any(request.url.path != "/v1/responses/input_tokens" for request in requests):
        raise ValueError("contract gate attempted a generation endpoint")
    return {
        "mode": "offline_preview_no_network",
        "endpoint": "/v1/responses/input_tokens",
        "contract_count": len(results),
        "contracts": results,
        "generation_requests": 0,
        "operator_approval_required": True,
    }


def execute_approved(approval: str, output: Path) -> dict[str, Any]:
    if approval != APPROVAL:
        raise ValueError("explicit input-token contract gate approval required")
    settings = Settings()
    if settings.openai_api_key is None:
        raise ValueError("OpenAI credential is not configured")
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as artifact:
        artifact.write('{"outcome":"started_outcome_unknown_do_not_retry"}\n')
        artifact.flush()
        os.fsync(artifact.fileno())
        results = asyncio.run(count_contracts(settings.openai_api_key.get_secret_value()))
        result = {
            "endpoint": "/v1/responses/input_tokens",
            "contracts": results,
            "generation_requests": 0,
        }
        artifact.write(json.dumps(result, sort_keys=True) + "\n")
        artifact.flush()
        os.fsync(artifact.fileno())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-approved", default="")
    parser.add_argument("--diagnostic-file", type=Path)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        if args.execute_approved:
            if args.diagnostic_file is None:
                raise ValueError("private diagnostic file is required")
            result = execute_approved(args.execute_approved, args.diagnostic_file)
        else:
            result = asyncio.run(preview())
    except Exception:
        print('{"outcome":"stopped_safely_do_not_retry"}')
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
