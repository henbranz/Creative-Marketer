from dataclasses import FrozenInstanceError, replace
from uuid import uuid4

import pytest

from creative_marketer.identity.application.authentication import ActorKind
from creative_marketer.tool_governance.application import CreateToolVersion, PlatformControlContext
from creative_marketer.tool_governance.domain import (
    CredentialBoundary,
    ExecutionClass,
    IdempotencyRequirement,
    InvalidToolSchema,
    RiskLevel,
    SideEffectClass,
    ToolContractSchema,
    ToolDefinition,
    ToolRegistryConflict,
    ToolVersion,
    ToolVersionConfiguration,
    canonical_json,
    sha256_digest,
)
from creative_marketer.tool_governance.schema_validation import validate_contract_schema

DIALECT = "https://json-schema.org/draft/2020-12/schema"


def schema(*, properties: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "$schema": DIALECT,
        "type": "object",
        "properties": {"value": {"type": "string"}} if properties is None else properties,
        "additionalProperties": False,
    }


def configuration() -> ToolVersionConfiguration:
    return ToolVersionConfiguration(
        display_name="Publish social post",
        description="Publishes one normalized post through a connector boundary.",
        risk_level=RiskLevel.R4,
        side_effect_class=SideEffectClass.EXTERNAL_MUTATION,
        execution_class=ExecutionClass.CONNECTOR,
        credential_boundary=CredentialBoundary.CONNECTOR,
        idempotency_requirement=IdempotencyRequirement.REQUIRED,
        input_schema=validate_contract_schema(schema()),
        output_schema=validate_contract_schema(
            schema(properties={"publication_id": {"type": "string", "format": "uuid"}})
        ),
        capability_tags=("social.publish", "external.write"),
    )


def test_tool_contract_is_immutable_and_order_independent() -> None:
    first_schema = validate_contract_schema(schema(properties={"a": {"type": "string"}}))
    reordered_schema = validate_contract_schema(
        {
            "additionalProperties": False,
            "properties": {"a": {"type": "string"}},
            "type": "object",
            "$schema": DIALECT,
        }
    )
    assert first_schema == reordered_schema
    first = configuration()
    reordered = replace(first, capability_tags=tuple(reversed(first.capability_tags)))
    assert first == reordered
    assert first.configuration_digest == reordered.configuration_digest
    with pytest.raises(FrozenInstanceError):
        first.description = "changed"  # type: ignore[misc]
    mutable_copy = first.input_schema.primitive()
    mutable_copy["type"] = "array"
    assert first.input_schema.primitive()["type"] == "object"


@pytest.mark.parametrize(
    "change",
    [
        lambda value: replace(value, risk_level=RiskLevel.R5),
        lambda value: replace(value, side_effect_class=SideEffectClass.INTERNAL_MUTATION),
        lambda value: replace(value, execution_class=ExecutionClass.PROVIDER),
        lambda value: replace(value, credential_boundary=CredentialBoundary.NONE),
        lambda value: replace(value, idempotency_requirement=IdempotencyRequirement.SUPPORTED),
        lambda value: replace(value, input_schema=validate_contract_schema(schema(properties={}))),
        lambda value: replace(value, output_schema=validate_contract_schema(schema(properties={}))),
    ],
)
def test_every_semantic_change_changes_configuration_digest(change: object) -> None:
    original = configuration()
    changed = change(original)  # type: ignore[operator]
    assert changed.configuration_digest != original.configuration_digest


@pytest.mark.parametrize(
    "document",
    [
        {"type": "object"},
        {"$schema": DIALECT, "type": "array"},
        {"$schema": DIALECT, "type": "invalid"},
        {"$schema": DIALECT, "type": "object", "$ref": "https://evil.example/schema"},
        {
            "$schema": DIALECT,
            "type": "object",
            "properties": {"nested": {"$ref": "https://evil.example/nested"}},
        },
        {
            "$schema": DIALECT,
            "type": "object",
            "properties": {"value": {"type": "string", "default": "Bearer raw-secret"}},
        },
        {
            "$schema": DIALECT,
            "type": "object",
            "properties": {"value": {"type": "string", "example": "api_key=raw-value"}},
        },
        {
            "$schema": DIALECT,
            "type": "object",
            "properties": {"access_token": {"type": "string"}},
        },
    ],
)
def test_schema_validation_rejects_invalid_remote_or_secret_contracts(
    document: dict[str, object],
) -> None:
    with pytest.raises(InvalidToolSchema):
        validate_contract_schema(document)


def test_schema_validation_rejects_non_object_document() -> None:
    with pytest.raises(InvalidToolSchema, match="document"):
        validate_contract_schema([{"type": "object"}])


def test_schema_validation_rejects_oversize_and_pathological_depth() -> None:
    with pytest.raises(InvalidToolSchema, match="65536"):
        validate_contract_schema(
            {"$schema": DIALECT, "type": "object", "description": "x" * 66_000}
        )
    nested: dict[str, object] = {"type": "string"}
    for _ in range(34):
        nested = {"allOf": [nested]}
    with pytest.raises(InvalidToolSchema, match="nesting"):
        validate_contract_schema({"$schema": DIALECT, "type": "object", "properties": nested})


def test_schema_allows_local_refs_and_opaque_resource_reference_names() -> None:
    contract = validate_contract_schema(
        {
            "$schema": DIALECT,
            "type": "object",
            "$defs": {"id": {"type": "string", "format": "uuid"}},
            "properties": {
                "social_account_id": {"$ref": "#/$defs/id"},
                "access_token_reference": {"type": "string", "format": "uuid"},
            },
            "additionalProperties": False,
        }
    )
    assert contract.digest.startswith("sha256:")


@pytest.mark.parametrize(
    "tool_key", ["*", "social.*", "SOCIAL.PUBLISH", " social.publish", "social publish", "single"]
)
def test_tool_definition_rejects_noncanonical_keys(tool_key: str) -> None:
    with pytest.raises(ValueError):
        ToolDefinition(tool_key, "social", "system", uuid4())


def test_tool_definition_and_version_are_platform_only_typed_contracts() -> None:
    definition = ToolDefinition("social.post.publish", "social", "system", uuid4())
    version = ToolVersion(definition.id, 1, configuration(), "workload", uuid4())
    assert version.configuration_digest == configuration().configuration_digest
    with pytest.raises(ValueError, match="platform"):
        replace(definition, created_by_actor_kind="agent")
    with pytest.raises(ValueError, match="positive"):
        replace(version, version_number=0)
    with pytest.raises(ValueError):
        replace(configuration(), risk_level="HIGH")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="credential-shaped"):
        replace(configuration(), description="Authorization: Bearer raw-secret")


def test_platform_control_context_rejects_user_and_agent_authority() -> None:
    for actor_kind in (ActorKind.USER, ActorKind.AGENT):
        with pytest.raises(ToolRegistryConflict, match="platform authority"):
            PlatformControlContext(actor_kind, uuid4(), "test", uuid4())


@pytest.mark.asyncio
async def test_version_registration_revalidates_schema_before_persistence() -> None:
    invalid_document = {"type": "object"}
    invalid_snapshot = ToolContractSchema(
        canonical_json(invalid_document), sha256_digest(invalid_document)
    )
    unsafe = replace(configuration(), input_schema=invalid_snapshot)

    class NeverUsedFactory:
        def __call__(self) -> None:
            raise AssertionError("invalid schema must be rejected before persistence")

    with pytest.raises(InvalidToolSchema):
        await CreateToolVersion(NeverUsedFactory())(  # type: ignore[arg-type]
            control := PlatformControlContext(ActorKind.SYSTEM, uuid4(), "test", uuid4()),
            uuid4(),
            unsafe,
        )
    assert control.actor_kind is ActorKind.SYSTEM
