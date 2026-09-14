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

_INPUT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["publication_draft_id"],
    "properties": {"publication_draft_id": {"type": "string", "format": "uuid"}},
}
_OUTPUT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["publication_draft_id", "status"],
    "properties": {
        "publication_draft_id": {"type": "string", "format": "uuid"},
        "status": {"enum": ["SUBMITTED", "PUBLISHED", "FAILED", "OUTCOME_UNKNOWN", "CANCELLED"]},
        "result_ref": {"type": ["string", "null"], "maxLength": 256},
    },
}


@dataclass(frozen=True, slots=True)
class SocialToolContract:
    tool_key: str
    configuration: ToolVersionConfiguration


def social_tool_contracts() -> tuple[SocialToolContract, ...]:
    values = (
        (
            "social.publish.submit",
            "Submit one exact approved PublicationDraft",
            RiskLevel.R4,
            SideEffectClass.EXTERNAL_MUTATION,
        ),
        (
            "social.publish.status",
            "Reconcile one known social publication",
            RiskLevel.R1,
            SideEffectClass.READ_ONLY,
        ),
        (
            "social.publish.cancel",
            "Cancel one not-yet-published scheduled operation",
            RiskLevel.R4,
            SideEffectClass.EXTERNAL_MUTATION,
        ),
    )
    return tuple(
        SocialToolContract(
            key,
            ToolVersionConfiguration(
                display_name=key,
                description=description,
                risk_level=risk,
                side_effect_class=effect,
                execution_class=ExecutionClass.CONNECTOR,
                credential_boundary=CredentialBoundary.CONNECTOR,
                idempotency_requirement=IdempotencyRequirement.REQUIRED,
                input_schema=validate_contract_schema(_INPUT),
                output_schema=validate_contract_schema(_OUTPUT),
                capability_tags=("social.publishing",),
            ),
        )
        for key, description, risk, effect in values
    )
