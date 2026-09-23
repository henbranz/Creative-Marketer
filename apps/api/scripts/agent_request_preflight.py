"""Read-only frozen Researcher/Creative request gate. No provider network transport."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

import httpx
from openai import AsyncOpenAI
from sqlalchemy import text

from creative_marketer.agent_runtime.application import (
    conservative_creative_input_token_bound,
    conservative_input_token_bound,
    default_capability_registry,
    initial_agent_model_routes,
)
from creative_marketer.agent_runtime.domain import ModelInvocation
from creative_marketer.infrastructure.database.agent_runtime_repositories import (
    SqlAlchemyAgentRunRepository,
)
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.model_providers.openai_responses import (
    OpenAIResponsesModelProvider,
)
from creative_marketer.infrastructure.model_providers.openai_schema import (
    validate_openai_strict_output_schema,
)
from creative_marketer_api.config import Settings


def schema_metadata(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Count schema structure, not property names, descriptions, or literal values."""
    keywords: set[str] = set()
    nodes: list[Mapping[str, Any]] = []

    def walk(node: Mapping[str, Any]) -> None:
        nodes.append(node)
        keywords.update(node)
        for key in ("properties", "$defs"):
            for child in node.get(key, {}).values():
                walk(child)
        if isinstance(node.get("items"), Mapping):
            walk(node["items"])
        for child in node.get("anyOf", []):
            walk(child)

    def depth(node: Mapping[str, Any], seen: tuple[str, ...] = ()) -> int:
        if "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
                raise ValueError("local gate cannot verify this reference")
            target = schema
            for part in ref[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            return depth(target, (*seen, ref))
        children = list(node.get("properties", {}).values()) + node.get("anyOf", [])
        if "items" in node:
            children.append(node["items"])
        own = int(node.get("type") == "object" or node.get("type") == "array")
        return own + max((depth(child, seen) for child in children), default=0)

    walk(schema)
    return {
        "schema_keywords": sorted(keywords),
        "schema_bytes": len(json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode()),
        "schema_container_depth": depth(schema),
        "object_schemas": sum(node.get("type") == "object" for node in nodes),
        "properties": sum(len(node.get("properties", {})) for node in nodes),
        "refs": sum("$ref" in node for node in nodes),
        "anyof_branches": sum(len(node.get("anyOf", [])) for node in nodes),
        "nullable_fields": sum(isinstance(node.get("type"), list) for node in nodes),
        "enum_or_const_without_type": sum(
            ("enum" in node or "const" in node) and "type" not in node for node in nodes
        ),
    }


async def inspect_invocation(
    invocation: ModelInvocation, input_bound: int, total_limit: int
) -> dict[str, Any]:
    """Exercise the real adapter and SDK with a mandatory in-memory HTTP transport.

    A pass is NOT provider acceptance or retry authority. No validation-only Responses
    endpoint is assumed. Image-bearing invocations are deliberately out of scope.
    """
    if (
        invocation.image_inputs
        or invocation.route.provider != "openai"
        or invocation.route.model != "gpt-5.6-sol"
    ):
        raise ValueError("unsupported preflight scope")
    if invocation.reasoning_effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
        raise ValueError("unsupported reasoning effort")
    name = (
        invocation.output_contract_key.replace(".", "_")
        + "_v"
        + str(invocation.output_contract_version)
    )
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        raise ValueError("invalid structured output name")
    if (
        not 0 < invocation.max_output_tokens <= 128_000
        or input_bound <= 0
        or input_bound + invocation.max_output_tokens > min(total_limit, 1_050_000)
    ):
        raise ValueError("invalid token envelope")
    validate_openai_strict_output_schema(invocation.output_schema)
    metadata = schema_metadata(invocation.output_schema)
    if metadata["properties"] > 5000 or metadata["schema_container_depth"] > 10:
        raise ValueError("schema exceeds documented structural limits")
    captured: dict[str, Any] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        fmt = body["text"]["format"]
        if (
            request.method != "POST"
            or request.url.path != "/v1/responses"
            or set(body)
            != {
                "model",
                "instructions",
                "input",
                "text",
                "reasoning",
                "max_output_tokens",
                "tools",
                "store",
            }
        ):
            raise ValueError("unexpected request envelope")
        if (
            fmt["type"] != "json_schema"
            or fmt["strict"] is not True
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", fmt["name"])
        ):
            raise ValueError("invalid structured output format")
        if (
            body["tools"] != []
            or body["store"] is not False
            or len(body["input"]) != 1
            or body["input"][0]["role"] != "user"
            or not isinstance(body["input"][0]["content"], str)
        ):
            raise ValueError("unexpected content or request options")
        captured.update(
            {
                "model": body["model"],
                "reasoning_effort": body["reasoning"]["effort"],
                "max_output_tokens": body["max_output_tokens"],
                "messages": 1,
                "content_types": ["string"],
                "images": False,
                "contract_name": fmt["name"],
                "contract_version": invocation.output_contract_version,
                "request_bytes": len(request.content),
                "tools": [],
                "store": False,
            }
        )
        return httpx.Response(
            200,
            json={
                "id": "offline_mock",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": body["model"],
                "output": [
                    {
                        "type": "message",
                        "id": "offline_message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "{}", "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            },
        )

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(capture), trust_env=False) as http,
        AsyncOpenAI(
            api_key="offline-only",
            base_url="https://api.openai.com/v1",
            max_retries=0,
            http_client=http,
        ) as client,
    ):
        await OpenAIResponsesModelProvider("offline-only", client=client).generate_structured(
            invocation
        )
    return {
        **metadata,
        **captured,
        "estimated_input_bound": input_bound,
        "max_total_tokens": total_limit,
        "local_checks": "PASS",
        "provider_acceptance": "UNVERIFIED",
        "retry_authorized": False,
    }


def check_settings(settings: Settings) -> None:
    key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    if (
        settings.model_provider_backend != "openai"
        or settings.agent_workload_id != "local-live-agent-worker"
        or not key.strip()
        or key.startswith(("disabled-", "test-", "fake-", "replace-"))
    ):
        raise ValueError("local live OpenAI configuration is not ready")


async def inspect_run(settings: Settings, tenant_id: UUID, run_id: UUID) -> dict[str, Any]:
    check_settings(settings)
    factory = create_session_factory(str(settings.database_url))
    async with factory() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        repository = SqlAlchemyAgentRunRepository(session)
        run = await repository.get(run_id)
        if (
            run is None
            or run.tenant_id != tenant_id
            or run.agent_type not in {"researcher", "creative_strategist"}
        ):
            raise ValueError("frozen run unavailable in supported tenant scope")
        route = next(
            (
                route
                for route in initial_agent_model_routes()
                if route.route_version == run.resolved_model_route_version
            ),
            None,
        )
        if route is None or (
            route.model,
            route.provider,
            route.pricing.version,
            route.reasoning_effort,
            route.max_output_tokens,
        ) != (
            run.resolved_model,
            run.resolved_provider,
            run.pricing_version,
            run.reasoning_effort,
            run.max_output_tokens,
        ):
            raise ValueError("frozen route mismatch")
        context = await repository.resolve_context(run)
        invocation = (
            default_capability_registry().resolve(run.agent_type).invocation(run, route, context)
        )
        bound = (
            conservative_input_token_bound(context)
            if run.agent_type == "researcher"
            else conservative_creative_input_token_bound(context)
        )
        result = await inspect_invocation(invocation, bound, run.max_total_tokens)
        await session.rollback()
        return {
            "run_id": str(run_id),
            "agent_type": run.agent_type,
            "route": route.route_version,
            "pricing": route.pricing.version,
            "context_kind": run.input_context_kind,
            **result,
        }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tenant_id", type=UUID)
    parser.add_argument("run_id", type=UUID)
    args = parser.parse_args()
    try:
        result = await inspect_run(Settings(), args.tenant_id, args.run_id)
    except Exception:
        # Settings/database exceptions can contain secrets or SQL parameters.
        print("LOCAL_REQUEST_PREFLIGHT_FAILED (no provider request sent)")
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
