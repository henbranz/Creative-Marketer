"""Explicit environment contract shared by drift tests."""

from pathlib import Path

OPERATIONAL_ENVIRONMENT_KEYS = {
    "MIGRATION_DATABASE_URL",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "TEMPORAL_ADDRESS",
    "TEMPORAL_NAMESPACE",
    "TEMPORAL_TEST_SERVER_PATH",
    "BOOTSTRAP_TENANT_ID",
    "BOOTSTRAP_USER_ID",
    "BOOTSTRAP_PLATFORM_ACTOR_ID",
    "OBSIDIAN_VAULT_PATH",
    "CM_API_BASE_URL",
    "CM_TENANT_ID",
    "CM_API_TOKEN",
    "TEST_OBJECT_STORAGE_URL",
    "TEST_DATABASE_ADMIN_URL",
    "TEST_DATABASE_RUNTIME_URL",
    "TEST_DATABASE_PUBLISHER_URL",
}


def assignments(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def assignment_keys(path: Path) -> set[str]:
    return set(assignments(path))
