import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


@pytest.fixture(scope="session")
def admin_database_url() -> str:
    value = os.environ.get("TEST_DATABASE_ADMIN_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_ADMIN_URL is required for PostgreSQL security tests")
    return value


@pytest.fixture(scope="session")
def runtime_database_url() -> str:
    value = os.environ.get("TEST_DATABASE_RUNTIME_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_RUNTIME_URL is required for PostgreSQL security tests")
    return value


@pytest_asyncio.fixture
async def admin_engine(admin_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(admin_database_url)
    async with engine.begin() as connection:
        await connection.execute(
            text("TRUNCATE identity.memberships, identity.users, identity.tenants CASCADE")
        )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def runtime_engine(runtime_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(runtime_database_url, pool_size=1, max_overflow=0)
    yield engine
    await engine.dispose()
