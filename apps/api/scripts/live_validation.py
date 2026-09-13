"""Operator-only live-provider preflight and governed acceptance-flow guide."""

from __future__ import annotations

import argparse
import asyncio
import socket
import sys
from typing import Any, Protocol
from urllib.parse import urlsplit

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, AuthenticationError

from creative_marketer.agent_runtime.application import (
    initial_creative_strategist_route,
    initial_researcher_route,
)
from creative_marketer.production.application import (
    SEEDANCE_MODEL,
    initial_media_router,
    initial_producer_route,
)
from creative_marketer_api.config import Settings
from scripts import live_acceptance as acceptance

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


async def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "preflight",
            "openai-smoke",
            "image-smoke",
            "seedance-smoke",
            "e2e",
            "reset",
            "status",
        ),
    )
    command = parser.parse_args().command
    if command == "reset":
        return acceptance.reset_session()
    settings = Settings()
    if command == "preflight":
        return await preflight(settings)
    try:
        if command == "openai-smoke":
            return acceptance.openai_smoke(settings)
        if command == "image-smoke":
            return acceptance.media_smoke(settings, "IMAGE")
        if command == "seedance-smoke":
            return acceptance.media_smoke(settings, "VIDEO")
        if command == "status":
            return acceptance.session_status(settings)
        return acceptance.e2e(settings)
    except RuntimeError as error:
        print(f"Live validation blocked: {error}")
        return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
