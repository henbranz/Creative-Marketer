from __future__ import annotations

import base64
from decimal import Decimal
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

from creative_marketer.production.application import OPENAI_IMAGE_MODEL
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
        normalized_usage = {
            key: int(getattr(usage, key, 0))
            for key in ("input_tokens", "output_tokens", "total_tokens")
        }
        return ImageGenerationResult(
            content,
            media_type,
            getattr(response, "id", None),
            Decimal(str(getattr(response, "cost", 0))) if hasattr(response, "cost") else None,
            normalized_usage,
        )
