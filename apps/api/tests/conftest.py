import os

import pytest

from creative_marketer_api.config import Settings

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://test:test@localhost:5432/test",
)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="test",
        database_url="postgresql+psycopg://test:test@localhost:5432/test",
        cors_origins=["http://localhost:3000"],
    )
