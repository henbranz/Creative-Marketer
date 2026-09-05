from typing import Literal

import pytest
from pydantic import ValidationError

from creative_marketer_api.config import Settings


def test_settings_reject_invalid_database_url() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="sqlite:///local.db")


def test_settings_reject_unknown_environment() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="demo",
            database_url="postgresql+psycopg://test:test@localhost:5432/test",
        )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_settings_reject_development_identity_in_deployed_environments(
    environment: Literal["staging", "production"],
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env=environment,
            dev_identity_enabled=True,
            database_url="postgresql+psycopg://test:test@localhost:5432/test",
        )
