"""Operator-only live-provider preflight and governed acceptance-flow guide."""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, AuthenticationError

from creative_marketer.agent_runtime.application import (
    initial_creative_strategist_route,
    initial_researcher_route,
)
from creative_marketer.agent_runtime.domain import ModelRoute
from creative_marketer.production.application import (
    OPENAI_IMAGE_MODEL,
    SEEDANCE_MODEL,
    SeedancePricing,
    initial_media_router,
    initial_producer_route,
)
from creative_marketer_api.config import Settings

ACK = "I_UNDERSTAND_THIS_SPENDS_MONEY"


class ModelLookup(Protocol):
    models: Any


def _safe_error(code: str, *, provider: str, model: str, category: str) -> int:
    print(f"{provider:<12} FAIL")
    print(f"  model: {model}")
    print(f"  code: {code}")
    print(f"  HTTP category: {category}")
    return 2


async def preflight(settings: Settings, client: ModelLookup | None = None) -> int:
    expected = {
        "Researcher": initial_researcher_route().model,
        "Creative Strategist": initial_creative_strategist_route().model,
        "Producer": initial_producer_route().model,
        "Image": initial_media_router().resolve("production_image").model,
        "Video": initial_media_router().resolve("production_video").model,
    }
    if expected != {
        "Researcher": "gpt-5.6-sol",
        "Creative Strategist": "gpt-5.6-sol",
        "Producer": "gpt-5.6-sol",
        "Image": "gpt-image-2.5-sunburst-2026-09-08",
        "Video": "dreamina-seedance-2-5-260628",
    }:
        print("Application routes FAIL: LIVE_ROUTE_CONFIGURATION_MISMATCH")
        return 2
    print("Application routes         OK")
    if settings.openai_api_key is None:
        print("OpenAI key                NOT CONFIGURED")
        return 2
    openai_client = client or AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(), max_retries=0
    )
    for model in dict.fromkeys(
        (
            expected["Researcher"],
            expected["Creative Strategist"],
            expected["Producer"],
            expected["Image"],
        )
    ):
        try:
            await openai_client.models.retrieve(model)
        except AuthenticationError:
            return _safe_error(
                "PROVIDER_AUTHENTICATION_FAILED",
                provider="OpenAI",
                model=model,
                category="4xx",
            )
        except APIStatusError as error:
            if error.status_code in {403, 404}:
                return _safe_error(
                    "PROVIDER_MODEL_NOT_AVAILABLE_TO_ACCOUNT",
                    provider="OpenAI",
                    model=model,
                    category="4xx",
                )
            return _safe_error(
                "PROVIDER_PREFLIGHT_FAILED",
                provider="OpenAI",
                model=model,
                category=f"HTTP {error.status_code}",
            )
        except APIConnectionError:
            return _safe_error(
                "PROVIDER_UNREACHABLE", provider="OpenAI", model=model, category="network"
            )
        else:
            print(f"OpenAI {model:<38} ACCESSIBLE")
    if settings.byteplus_las_api_key is None:
        print("BytePlus key              NOT CONFIGURED")
        return 2
    parsed = urlsplit(str(settings.byteplus_las_base_url))
    try:
        socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    except OSError:
        print("BytePlus LAS              FAIL: PROVIDER_UNREACHABLE")
        return 2
    print(f"BytePlus {SEEDANCE_MODEL:<36} ROUTE OK")
    print("BytePlus authentication   DEFERRED TO MINIMAL SMOKE (no documented free check)")
    print("Preflight made no inference or media-generation requests.")
    return 0


@dataclass(frozen=True)
class LocalApi:
    base_url: str
    tenant_id: str
    credential: str

    def request(self, path: str, *, method: str = "GET", body: object | None = None) -> Any:
        payload = None if body is None else json.dumps(body).encode()
        request = Request(
            self.base_url.rstrip("/") + path,
            data=payload,
            method=method,
            headers={
                "Authorization": f"Bearer {self.credential}",
                "X-Tenant-ID": self.tenant_id,
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                return json.loads(response.read())
        except HTTPError as error:
            raise RuntimeError(f"LOCAL_API_HTTP_{error.code}") from None
        except URLError:
            raise RuntimeError("LOCAL_API_UNREACHABLE") from None


def _api(settings: Settings) -> LocalApi:
    settings.require_live_spend_authorization()
    if settings.live_e2e_product_id is None:
        raise RuntimeError("LIVE_E2E_PRODUCT_ID is required")
    import os

    tenant = os.getenv("CM_TENANT_ID", "").strip()
    credential = os.getenv("CM_API_TOKEN", "").strip()
    if not tenant or not credential:
        raise RuntimeError("CM_TENANT_ID and CM_API_TOKEN are required")
    return LocalApi(os.getenv("CM_API_BASE_URL", "http://localhost:8000"), tenant, credential)


def _runs(api: LocalApi, product: str, kind: str) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], api.request(f"/v1/products/{product}/{kind}/runs"))


def _successful(runs: list[dict[str, Any]], route: ModelRoute) -> dict[str, Any] | None:
    return next(
        (
            run
            for run in runs
            if run["status"] == "SUCCEEDED"
            and run["resolved_model"] == route.model
            and run["model_route_version"] == route.route_version
        ),
        None,
    )


def _report_run(label: str, run: dict[str, Any]) -> None:
    print(f"{label}: PASS")
    print(f"  model: {run['resolved_model']}")
    print(f"  route version: {run['model_route_version']}")
    print(
        f"  usage: {run['input_tokens']} input / {run['output_tokens']} output / "
        f"{run['total_tokens']} total"
    )
    print(f"  cost: {run['estimated_cost']} {run['currency']}")
    print("  schema validation: PASS")


def openai_smoke(settings: Settings) -> int:
    api = _api(settings)
    product = str(settings.live_e2e_product_id)
    api.request(f"/v1/products/{product}")
    researcher_route = initial_researcher_route()
    researcher = _successful(_runs(api, product, "research"), researcher_route)
    if researcher is None:
        api.request(
            f"/v1/products/{product}/research/runs",
            method="POST",
            body={"idempotency_key": f"live-research-{uuid4()}"},
        )
        print("Researcher requested through AgentRuntime. Re-run after the Agent worker completes.")
        return 3
    _report_run("Researcher", researcher)
    creative_route = initial_creative_strategist_route()
    creative = _successful(_runs(api, product, "creative"), creative_route)
    if creative is None:
        api.request(
            f"/v1/products/{product}/creative/runs",
            method="POST",
            body={
                "idempotency_key": f"live-creative-{uuid4()}",
                "concept_count": 5,
                "channel_intent": "ORGANIC_SHORT_FORM",
            },
        )
        print("Creative Strategist requested. Re-run after the Agent worker completes.")
        return 3
    _report_run("Creative Strategist", creative)
    producer_route = initial_producer_route()
    producer = _successful(_runs(api, product, "production"), producer_route)
    if producer is None:
        concepts = api.request(f"/v1/products/{product}/creative/concept-sets")
        approved = next(
            (
                concept
                for concept_set in concepts
                for concept in concept_set["concepts"]
                if concept.get("decision_state") == "APPROVED_FOR_PRODUCTION"
            ),
            None,
        )
        if approved is None:
            print("PAUSED: approve one Creative Concept in the UI, then re-run this command.")
            return 3
        api.request(
            f"/v1/creative/concepts/{approved['id']}/production/runs",
            method="POST",
            body={"idempotency_key": f"live-producer-{uuid4()}"},
        )
        print("Producer requested through AgentRuntime. Re-run after the Agent worker completes.")
        return 3
    _report_run("Producer", producer)
    return 0


def media_smoke(settings: Settings, kind: str) -> int:
    api = _api(settings)
    product = str(settings.live_e2e_product_id)
    producer = _successful(_runs(api, product, "production"), initial_producer_route())
    if producer is None:
        print("PAUSED: complete live-openai-smoke with the current Sol route first.")
        return 3
    plans = api.request(f"/v1/products/{product}/production/plans")
    plan = next((item for item in plans if item["agent_run_id"] == producer["id"]), None)
    if plan is None:
        print("PAUSED: complete live-openai-smoke to create a Production Plan.")
        return 3
    if plan["status"] == "UNREVIEWED":
        if kind == "VIDEO":
            preview = SeedancePricing().cost(output_duration_seconds=4, resolution="720p")
            print(f"Seedance 4s 720p no-input-video preview: {preview} USD")
        print("PAUSED: review and approve the exact Production Plan in the UI.")
        return 3
    jobs = api.request(f"/v1/production/plans/{plan['id']}/jobs")
    selected = [job for job in jobs if job["kind"] == kind]
    if not selected:
        print(f"FAIL: approved plan has no {kind.lower()} GenerationJob")
        return 2
    expected = OPENAI_IMAGE_MODEL if kind == "IMAGE" else SEEDANCE_MODEL
    if any(job["model"] != expected for job in selected):
        print("FAIL: historical/current route mismatch; no fallback was attempted")
        return 2
    if all(job["status"] == "SUCCEEDED" for job in selected):
        for job in selected:
            print(
                f"{kind.title()} PASS: {job['model']} / {job['actual_cost']} {job['currency']} / "
                f"private Asset {job['output_asset_id']}"
            )
        return 0
    print(
        "Generation is governed and approved. Run/keep the Production worker active, then re-run."
    )
    return 3


def e2e(settings: Settings) -> int:
    print("Live E2E uses the existing APIs, Agent worker, Production worker, and Assembly worker.")
    status = openai_smoke(settings)
    if status:
        return status
    for kind in ("IMAGE", "VIDEO"):
        status = media_smoke(settings, kind)
        if status:
            return status
    api = _api(settings)
    product = str(settings.live_e2e_product_id)
    producer = _successful(_runs(api, product, "production"), initial_producer_route())
    if producer is None:
        return 3
    production_plans = api.request(f"/v1/products/{product}/production/plans")
    production_plan = next(
        (item for item in production_plans if item["agent_run_id"] == producer["id"]), None
    )
    if production_plan is None:
        return 3
    assembly_plans = api.request(f"/v1/production/plans/{production_plan['id']}/assembly-plans")
    if not assembly_plans:
        readiness = api.request(f"/v1/production/plans/{production_plan['id']}/assembly-readiness")
        if not readiness["ready"]:
            print("PAUSED: bind any required manual source media in the UI.")
            return 3
        api.request(f"/v1/production/plans/{production_plan['id']}/assembly-plans", method="POST")
        print(
            "Final Assembly requested through the existing API. Re-run after the worker completes."
        )
        return 3
    assembly = assembly_plans[0]
    if assembly["job"]["status"] != "SUCCEEDED":
        print("Final Assembly is pending. Keep the Assembly worker active, then re-run.")
        return 3
    final_id = assembly["job"]["final_creative_id"]
    if not final_id:
        print("FAIL: successful AssemblyJob has no FinalCreative")
        return 2
    final = api.request(f"/v1/final-creatives/{final_id}")
    if final["decision_state"] != "APPROVED_FOR_PUBLISHING":
        print("PAUSED: play and review the final MP4, then approve it for publishing in the UI.")
        return 3
    print(f"Final Creative PASS: {final['id']} / private Asset {final['output_asset_id']}")
    print(
        f"  provenance: AssemblyPlan {final['assembly_plan_id']} -> ProductionPlan "
        f"{final['production_plan_id']} -> AgentRun {producer['id']}"
    )
    print("Verify the configured Obsidian watcher projected the final provenance graph.")
    return 0


async def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("preflight", "openai-smoke", "image-smoke", "seedance-smoke", "e2e")
    )
    command = parser.parse_args().command
    settings = Settings()
    if command == "preflight":
        return await preflight(settings)
    try:
        if command == "openai-smoke":
            return openai_smoke(settings)
        if command == "image-smoke":
            return media_smoke(settings, "IMAGE")
        if command == "seedance-smoke":
            return media_smoke(settings, "VIDEO")
        return e2e(settings)
    except RuntimeError as error:
        print(f"Live validation blocked: {error}")
        return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
