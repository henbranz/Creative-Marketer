import os

import pytest

from creative_marketer_api.config import Settings

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://test:test@localhost:5432/test",
)
os.environ.setdefault("AUDIT_FINGERPRINT_KEY", "test-audit-fingerprint-key-32-bytes")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="test",
        database_url="postgresql+psycopg://test:test@localhost:5432/test",
        cors_origins=["http://localhost:3000"],
        audit_fingerprint_key="test-audit-fingerprint-key-32-bytes",
    )
