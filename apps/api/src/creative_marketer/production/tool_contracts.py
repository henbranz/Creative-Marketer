from __future__ import annotations

from dataclasses import dataclass

from creative_marketer.tool_governance.domain import (
    CredentialBoundary,
    ExecutionClass,
    IdempotencyRequirement,
    RiskLevel,
    SideEffectClass,
    ToolVersionConfiguration,
)
from creative_marketer.tool_governance.schema_validation import validate_contract_schema

_JOB_INPUT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["generation_job_id"],
    "properties": {"generation_job_id": {"type": "string", "format": "uuid"}},
}
_RESULT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["generation_job_id", "status"],
    "properties": {
        "generation_job_id": {"type": "string", "format": "uuid"},
        "status": {
            "enum": [
                "STARTING",
                "PROCESSING",
                "IMPORTING",
                "SUCCEEDED",
                "FAILED",
                "OUTCOME_UNKNOWN",
            ]
        },
        "result_ref": {"type": ["string", "null"], "maxLength": 256},
    },
}


@dataclass(frozen=True, slots=True)
class MediaToolContract:
    tool_key: str
    configuration: ToolVersionConfiguration


def media_tool_contracts() -> tuple[MediaToolContract, ...]:
    values = (
        (
            "media.image.generate",
            "Generate and import one governed image job",
            RiskLevel.R2,
            SideEffectClass.EXTERNAL_MUTATION,
        ),
        (
            "media.video.generate.start",
            "Start one governed asynchronous video job",
            RiskLevel.R2,
            SideEffectClass.EXTERNAL_MUTATION,
        ),
        (
            "media.video.generate.status",
            "Read authoritative status for a known video job",
            RiskLevel.R1,
            SideEffectClass.READ_ONLY,
        ),
        (
            "media.video.generate.import",
            "Import a completed provider result into private Assets",
            RiskLevel.R2,
            SideEffectClass.EXTERNAL_MUTATION,
        ),
    )
    return tuple(
        MediaToolContract(
            key,
            ToolVersionConfiguration(
                display_name=key,
                description=description,
                risk_level=risk,
                side_effect_class=effect,
                execution_class=ExecutionClass.CONNECTOR,
                credential_boundary=CredentialBoundary.CONNECTOR,
                idempotency_requirement=IdempotencyRequirement.REQUIRED,
                input_schema=validate_contract_schema(_JOB_INPUT),
                output_schema=validate_contract_schema(_RESULT),
                capability_tags=("media.production",),
            ),
        )
        for key, description, risk, effect in values
    )
