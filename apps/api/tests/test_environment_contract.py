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
    assert "BYTEPLUS_ARK_API_KEY" not in combined
    assert "ARK_API_KEY" not in combined


def test_active_modelark_configuration_has_no_las_endpoint_or_secret_contract() -> None:
    paths = (
        REPOSITORY_ROOT / ".env.example",
        REPOSITORY_ROOT / "docker-compose.yml",
        REPOSITORY_ROOT / "scripts/environment.py",
        REPOSITORY_ROOT / "apps/api/src/creative_marketer_api/config.py",
        REPOSITORY_ROOT / "apps/api/src/creative_marketer_api/production_worker.py",
        REPOSITORY_ROOT / "apps/api/src/creative_marketer/production/infrastructure/seedance.py",
    )
    active_contract = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "BYTEPLUS_LAS" not in active_contract
    assert "operator.las" not in active_contract

    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    production_worker = compose.split("  production-worker:", 1)[1].split(
        "\n  orchestration-worker:", 1
    )[0]
    assert "env_file:" in production_worker
    assert "- path: .env" in production_worker


def test_assembly_worker_is_providerless_even_when_root_selects_live_providers() -> None:
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assembly_worker = compose.split("  assembly-worker:", 1)[1].split("\n  web:", 1)[0]
    assert "env_file:" not in assembly_worker
    assert "MEDIA_IMAGE_PROVIDER: disabled" in assembly_worker
    assert "MEDIA_VIDEO_PROVIDER: disabled" in assembly_worker
    assert 'ALLOW_BILLABLE_MEDIA: "false"' in assembly_worker
    assert (
        "ASSEMBLY_FAKE_PLACEHOLDER_MODE: ${ASSEMBLY_FAKE_PLACEHOLDER_MODE:-false}"
        in assembly_worker
    )
    assert "${MEDIA_IMAGE_PROVIDER" not in assembly_worker
    assert "${MEDIA_VIDEO_PROVIDER" not in assembly_worker
    for forbidden in (
        "OPENAI_API_KEY",
        "BYTEPLUS_ARK_API_KEY",
        "ARK_API_KEY",
        "RUN_LIVE_E2E",
        "MODEL_PROVIDER_BACKEND",
    ):
        assert forbidden not in assembly_worker


def test_assembly_worker_has_no_media_generation_or_provider_execution_path() -> None:
    roots = (
        REPOSITORY_ROOT / "apps/api/src/creative_marketer/assembly",
        REPOSITORY_ROOT / "apps/api/src/creative_marketer_api/assembly_worker.py",
    )
    paths = [
        path
        for root in roots
        for path in ((root,) if root.is_file() else tuple(root.rglob("*.py")))
    ]
    source = "\n".join(path.read_text(encoding="utf-8").lower() for path in paths)
    for forbidden in (
        "openai",
        "modelark",
        "generationjob",
        "allow_billable_media",
        "media_image_provider",
        "media_video_provider",
        "production_worker",
    ):
        assert forbidden not in source


@pytest.mark.parametrize(
    "name",
    ["OPENAI_API_KEY", "BYTEPLUS_ARK_API_KEY", "CM_API_TOKEN"],
)
def test_sensitive_contract_fields_are_empty(name: str) -> None:
    assert support.assignments(REPOSITORY_ROOT / ".env.example")[name] == ""
