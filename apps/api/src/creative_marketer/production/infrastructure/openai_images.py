from __future__ import annotations

import base64
from decimal import Decimal
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

from creative_marketer.production.application import OPENAI_IMAGE_MODEL, ImageReservationPricing
from creative_marketer.production.media import (
    ImageGenerationRequest,
    ImageGenerationResult,
    MediaProviderError,
    MediaProviderOutcomeUnknown,
    validate_image_result,
)


class OpenAIImageProvider:
    """Current GPT Image adapter; SDK and base64 representations stay infrastructure-only."""

    def __init__(self, api_key: str, *, client: AsyncOpenAI | None = None) -> None:
        if not api_key or api_key.startswith(("disabled-", "test-", "fake-", "replace-")):
            raise ValueError("a non-placeholder OpenAI API key is required")
        self._client = client or AsyncOpenAI(api_key=api_key, max_retries=0)

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        if request.quality not in {"medium", "high"} or request.size not in {
            "1024x1024",
            "1024x1536",
            "1536x1024",
        }:
            raise ValueError("image quality or size is outside the pinned policy")
        try:
            images: Any = self._client.images
            if request.references:
                files = [
                    (f"reference-{index}.png", item.data, item.media_type)
                    for index, item in enumerate(request.references)
                ]
                response: Any = await images.edit(
                    model=OPENAI_IMAGE_MODEL,
                    image=files,
                    prompt=request.instruction,
                    quality=request.quality,
                    size=request.size,
                    output_format="png",
                )
            else:
                response = await images.generate(
                    model=OPENAI_IMAGE_MODEL,
                    prompt=request.instruction,
                    quality=request.quality,
                    size=request.size,
                    output_format="png",
                )
        except (APITimeoutError, APIConnectionError) as error:
            raise MediaProviderOutcomeUnknown("OpenAI image outcome is unknown") from error
        except (RateLimitError, APIStatusError) as error:
            raise MediaProviderError("OpenAI image generation failed") from error
        try:
            encoded = response.data[0].b64_json
            content = base64.b64decode(encoded, validate=True)
            media_type = validate_image_result(content)
        except (AttributeError, IndexError, TypeError, ValueError) as error:
            raise MediaProviderError("OpenAI returned malformed image output") from error
        usage = getattr(response, "usage", None)
        normalized_usage = self._usage(usage)
        provider_cost = getattr(response, "cost", None)
        actual_cost = (
            Decimal(str(provider_cost))
            if provider_cost is not None
            else ImageReservationPricing().actual_cost(normalized_usage)
        )
        return ImageGenerationResult(
            content,
            media_type,
            getattr(response, "id", None),
            actual_cost,
            normalized_usage,
        )

    @staticmethod
    def _usage(usage: Any) -> dict[str, int]:
        if usage is None:
            return {}
        result = {
            key: int(getattr(usage, key, 0))
            for key in ("input_tokens", "output_tokens", "total_tokens")
        }
        details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        for name, source, aliases in (
            ("text_input_tokens", details, ("text_tokens", "text_input_tokens")),
            ("image_input_tokens", details, ("image_tokens", "image_input_tokens")),
            (
                "cached_text_input_tokens",
                details,
                ("cached_text_tokens", "cached_text_input_tokens"),
            ),
            (
                "cached_image_input_tokens",
                details,
                ("cached_image_tokens", "cached_image_input_tokens"),
            ),
            ("image_output_tokens", output_details, ("image_tokens", "image_output_tokens")),
        ):
            value = next(
                (getattr(source, alias) for alias in aliases if hasattr(source, alias)), None
            )
            if value is not None:
                result[name] = int(value)
        if "image_output_tokens" not in result and result.get("output_tokens", 0):
            result["image_output_tokens"] = result["output_tokens"]
        return result
