"""Offline, content-free audit of every installed structured-output contract."""

from __future__ import annotations

import json

from creative_marketer.agent_runtime.application import default_capability_registry
from creative_marketer.agent_runtime.domain import ModelProviderSchemaUnsupported
from creative_marketer.infrastructure.model_providers.openai_schema import (
    audit_openai_schema,
    compile_openai_strict_output_schema,
)


def audit_contracts() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for contract in default_capability_registry().output_contracts():
        canonical = audit_openai_schema(contract.schema)
        try:
            compiled = compile_openai_strict_output_schema(
                contract.schema, contract_key=contract.key, contract_version=contract.version
            )
        except ModelProviderSchemaUnsupported:
            rows.append(
                {
                    "agent_type": contract.agent_type,
                    "contract_key": contract.key,
                    "contract_version": contract.version,
                    "root_type": canonical.root_type,
                    "object_schemas": canonical.object_schemas,
                    "any_of_branches": canonical.any_of_branches,
                    "partial_object_branches": canonical.partial_object_branches,
                    "unsupported_keywords": canonical.unsupported_keywords,
                    "normalizations": canonical.normalizations,
                    "local_result": "REJECTED",
                    "provider_count_result": "NOT_RUN",
                }
            )
            continue
        provider = audit_openai_schema(compiled.schema)
        rows.append(
            {
                "agent_type": contract.agent_type,
                "contract_key": contract.key,
                "contract_version": contract.version,
                "root_type": canonical.root_type,
                "object_schemas": canonical.object_schemas,
                "any_of_branches": canonical.any_of_branches,
                "partial_object_branches": canonical.partial_object_branches,
                "unsupported_keywords": canonical.unsupported_keywords,
                "normalizations": canonical.normalizations,
                "provider_object_schemas": provider.object_schemas,
                "provider_any_of_branches": provider.any_of_branches,
                "provider_partial_object_branches": provider.partial_object_branches,
                "compiler_revision": compiled.compiler_revision,
                "provider_schema_digest": compiled.digest,
                "local_result": "ACCEPTED",
                "provider_count_result": "NOT_RUN",
            }
        )
    return rows


def main() -> int:
    print(json.dumps(audit_contracts(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
