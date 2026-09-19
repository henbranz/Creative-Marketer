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
    "required": ["commerce_action_proposal_id"],
    "properties": {"commerce_action_proposal_id": {"type": "string", "format": "uuid"}},
}
_OUTPUT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["commerce_action_proposal_id", "status"],
    "properties": {
        "commerce_action_proposal_id": {"type": "string", "format": "uuid"},
        "status": {"enum": ["SUBMITTED", "SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN"]},
        "result_ref": {"type": ["string", "null"], "maxLength": 256},
    },
}


@dataclass(frozen=True, slots=True)
class CommerceToolContract:
    tool_key: str
    configuration: ToolVersionConfiguration


def commerce_tool_contracts() -> tuple[CommerceToolContract, ...]:
    values = (
        (
            "commerce.inventory.adjust",
            "Set one observed variant available quantity to an exact approved value",
            RiskLevel.R5,
            SideEffectClass.EXTERNAL_MUTATION,
        ),
        (
            "commerce.refund.submit",
            "Submit one exact approved financial refund",
            RiskLevel.R6,
            SideEffectClass.EXTERNAL_MUTATION,
        ),
        (
            "commerce.operation.status",
            "Reconcile one known commerce operation",
            RiskLevel.R1,
            SideEffectClass.READ_ONLY,
        ),
    )
    return tuple(
        CommerceToolContract(
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
                capability_tags=("commerce.operations",),
            ),
        )
        for key, description, risk, effect in values
    )
