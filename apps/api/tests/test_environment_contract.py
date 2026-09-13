from __future__ import annotations

import subprocess

import pytest

import scripts.environment_contract_support as support
from creative_marketer_api.config import REPOSITORY_ROOT, Settings


def test_root_environment_is_complete_safe_and_gitignored() -> None:
    documented = support.assignment_keys(REPOSITORY_ROOT / ".env.example")
    settings = {name.upper() for name in Settings.model_fields}
    frontend = {"NEXT_PUBLIC_API_BASE_URL", "NEXT_PUBLIC_OBSIDIAN_VAULT_NAME"}
    operational = support.OPERATIONAL_ENVIRONMENT_KEYS
    assert settings | frontend | operational <= documented
    example = (REPOSITORY_ROOT / ".env.example").read_text()
    assert "sk-" not in example
    assert "I_UNDERSTAND_THIS_SPENDS_MONEY\n" not in example
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".env"], cwd=REPOSITORY_ROOT, check=False
    )
    assert ignored.returncode == 0


def test_domain_and_application_layers_do_not_read_provider_secrets() -> None:
    roots = (
        REPOSITORY_ROOT / "apps/api/src/creative_marketer/agent_runtime",
        REPOSITORY_ROOT / "apps/api/src/creative_marketer/production",
    )
    combined = "\n".join(
        path.read_text()
        for root in roots
        for path in root.rglob("*.py")
        if "infrastructure" not in path.parts
    )
    assert "OPENAI_API_KEY" not in combined
    assert "BYTEPLUS_LAS_API_KEY" not in combined


@pytest.mark.parametrize(
    "name",
    ["OPENAI_API_KEY", "BYTEPLUS_LAS_API_KEY", "CM_API_TOKEN"],
)
def test_sensitive_contract_fields_are_empty(name: str) -> None:
    assert support.assignments(REPOSITORY_ROOT / ".env.example")[name] == ""
