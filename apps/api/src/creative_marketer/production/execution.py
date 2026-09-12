from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from creative_marketer.action_binding import NormalizedToolInput
from creative_marketer.tool_execution.domain import (
    OutcomeUnknown,
    PreEffectFailure,
    ToolExecutionContext,
    ToolExecutorResult,
)

from .domain import GenerationJob, GenerationJobStatus, MediaKind
from .media import (
    ImageGenerationRequest,
    ImageProvider,
    MaterializedReference,
    MediaProviderError,
    MediaProviderOutcomeUnknown,
    ProviderGenerationState,
    VideoGenerationRequest,
    VideoProvider,
    validate_image_result,
    validate_video_result,
)


@dataclass(frozen=True, slots=True)
class ExecutableGeneration:
    job: GenerationJob
    generation_spec: dict[str, object]
    references: tuple[MaterializedReference, ...]
    duration_seconds: int | None = None


class GenerationAuthority(Protocol):
    """Authoritative state seam; implementation revalidates approval, route, rights, and budget."""

    async def prepare(
        self, tenant_id: UUID, job_id: UUID, kind: MediaKind
    ) -> ExecutableGeneration: ...

    async def provider_started(self, job_id: UUID, provider_operation_ref: str | None) -> None: ...
    async def processing(self, job_id: UUID) -> None: ...
    async def importing(self, job_id: UUID) -> None: ...
    async def failed(self, job_id: UUID, failure_code: str, actual_cost: Decimal) -> None: ...
    async def outcome_unknown(self, job_id: UUID) -> None: ...
    async def succeeded(self, job_id: UUID, asset_id: UUID, actual_cost: Decimal) -> None: ...


class GeneratedAssetImporter(Protocol):
    async def import_result(
        self,
        execution: ExecutableGeneration,
        content: bytes,
        media_type: str,
    ) -> UUID: ...


def _job_id(value: NormalizedToolInput) -> UUID:
    raw = value.value()
    if not isinstance(raw, Mapping) or set(raw) != {"generation_job_id"}:
        raise ValueError("governed media input must contain only generation_job_id")
    return UUID(str(raw["generation_job_id"]))


def _instruction(spec: dict[str, object]) -> str:
    # Deterministic, ephemeral provider instruction. It is never persisted, logged, or emitted.
    return json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(slots=True)
class ImageGenerateToolExecutor:
    authority: GenerationAuthority
    provider: ImageProvider
    importer: GeneratedAssetImporter

    async def execute(
        self, context: ToolExecutionContext, normalized_input: NormalizedToolInput
    ) -> ToolExecutorResult:
        job_id = _job_id(normalized_input)
        execution = await self.authority.prepare(context.tenant_id, job_id, MediaKind.IMAGE)
        spec = execution.generation_spec
        await self.authority.provider_started(job_id, None)
        try:
            result = await self.provider.generate(
                ImageGenerationRequest(
                    _instruction(spec),
                    str(spec.get("size", "1024x1536")),
                    str(spec.get("quality", "high")).casefold(),
                    execution.references,
                )
            )
        except MediaProviderOutcomeUnknown as error:
            await self.authority.outcome_unknown(job_id)
            raise OutcomeUnknown("image provider outcome is unknown") from error
        except MediaProviderError as error:
            await self.authority.failed(job_id, error.code, Decimal(0))
            raise PreEffectFailure("image generation failed safely") from error
        media_type = validate_image_result(result.content)
        await self.authority.importing(job_id)
        asset_id = await self.importer.import_result(execution, result.content, media_type)
        actual = (
            result.actual_cost if result.actual_cost is not None else execution.job.reserved_cost
        )
        await self.authority.succeeded(job_id, asset_id, actual)
        return ToolExecutorResult(
            {
                "generation_job_id": str(job_id),
                "status": GenerationJobStatus.SUCCEEDED.value,
                "result_ref": f"result://production/assets/{asset_id}",
            },
            f"result://production/jobs/{job_id}",
        )


@dataclass(slots=True)
class VideoStartToolExecutor:
    authority: GenerationAuthority
    provider: VideoProvider

    async def execute(
        self, context: ToolExecutionContext, normalized_input: NormalizedToolInput
    ) -> ToolExecutorResult:
        job_id = _job_id(normalized_input)
        execution = await self.authority.prepare(context.tenant_id, job_id, MediaKind.VIDEO)
        spec = execution.generation_spec
        request = VideoGenerationRequest(
            _instruction(spec),
            execution.duration_seconds or 0,
            str(spec["resolution"]),
            str(spec["aspect_ratio"]),
            bool(spec.get("generate_audio", False)),
            execution.references,
        )
        # Persist STARTING before the request leaves our boundary. If the provider accepts the
        # request but its response is lost, OUTCOME_UNKNOWN is now a valid, durable transition
        # and this operation must never be started a second time automatically.
        await self.authority.provider_started(job_id, None)
        try:
            started = await self.provider.start(request)
        except MediaProviderOutcomeUnknown as error:
            await self.authority.outcome_unknown(job_id)
            raise OutcomeUnknown("video start outcome is unknown") from error
        except MediaProviderError as error:
            await self.authority.failed(job_id, error.code, Decimal(0))
            raise PreEffectFailure("video start failed safely") from error
        # This authoritative write occurs before success returns to Tool Gateway.
        await self.authority.provider_started(job_id, started.provider_operation_ref)
        return ToolExecutorResult(
            {
                "generation_job_id": str(job_id),
                "status": GenerationJobStatus.PROCESSING.value,
                "result_ref": f"result://production/jobs/{job_id}",
            },
            f"result://production/jobs/{job_id}",
        )


@dataclass(slots=True)
class VideoStatusToolExecutor:
    authority: GenerationAuthority
    provider: VideoProvider

    async def execute(
        self, context: ToolExecutionContext, normalized_input: NormalizedToolInput
    ) -> ToolExecutorResult:
        job_id = _job_id(normalized_input)
        execution = await self.authority.prepare(context.tenant_id, job_id, MediaKind.VIDEO)
        operation = execution.job.provider_operation_ref
        if operation is None:
            raise PreEffectFailure("known provider operation is required for polling")
        result = await self.provider.status(operation)
        if result.state in {ProviderGenerationState.FAILED, ProviderGenerationState.EXPIRED}:
            await self.authority.failed(job_id, result.failure_code or "MEDIA_FAILED", Decimal(0))
            status = GenerationJobStatus.FAILED
        elif result.state is ProviderGenerationState.SUCCEEDED:
            await self.authority.importing(job_id)
            status = GenerationJobStatus.IMPORTING
        else:
            await self.authority.processing(job_id)
            status = GenerationJobStatus.PROCESSING
        return ToolExecutorResult(
            {
                "generation_job_id": str(job_id),
                "status": status.value,
                "result_ref": f"result://production/jobs/{job_id}",
            },
            f"result://production/jobs/{job_id}",
        )


@dataclass(slots=True)
class VideoImportToolExecutor:
    authority: GenerationAuthority
    provider: VideoProvider
    importer: GeneratedAssetImporter

    async def execute(
        self, context: ToolExecutionContext, normalized_input: NormalizedToolInput
    ) -> ToolExecutorResult:
        job_id = _job_id(normalized_input)
        execution = await self.authority.prepare(context.tenant_id, job_id, MediaKind.VIDEO)
        operation = execution.job.provider_operation_ref
        if operation is None:
            raise PreEffectFailure("known provider operation is required for import")
        status = await self.provider.status(operation)
        if (
            status.state is not ProviderGenerationState.SUCCEEDED
            or status.temporary_result_locator is None
        ):
            raise PreEffectFailure("provider output is not ready for import")
        content = await self.provider.download(status.temporary_result_locator)
        media_type = validate_video_result(content)
        asset_id = await self.importer.import_result(execution, content, media_type)
        await self.authority.succeeded(job_id, asset_id, execution.job.reserved_cost)
        return ToolExecutorResult(
            {
                "generation_job_id": str(job_id),
                "status": GenerationJobStatus.SUCCEEDED.value,
                "result_ref": f"result://production/assets/{asset_id}",
            },
            f"result://production/jobs/{job_id}",
        )
