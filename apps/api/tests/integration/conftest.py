import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from creative_marketer.audit.identity import IdentityAuditService
from creative_marketer.infrastructure.database.agent_governance_uow import (
    SqlAlchemyAgentRegistryUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.approval_uow import (
    SqlAlchemyApprovalUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.audit import PostgresStandaloneAuditWriter
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.execution_control_uow import (
    SqlAlchemyIdempotencyUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.permission_governance_uow import (
    SqlAlchemyPermissionUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.tool_execution_uow import (
    SqlAlchemyGatewayUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.tool_governance_uow import (
    SqlAlchemyToolRegistryUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.uow import SqlAlchemyUnitOfWorkFactory
from tests.integration.support import IdentityStack


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


@pytest.fixture(scope="session")
def publisher_database_url() -> str:
    value = os.environ.get("TEST_DATABASE_PUBLISHER_URL")
    if value is None:
        pytest.skip("TEST_DATABASE_PUBLISHER_URL is required for publisher security tests")
    return value


@pytest_asyncio.fixture
async def admin_engine(admin_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(admin_database_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE event_delivery.inbox_receipts, event_delivery.outbox_events, "
                "tool_execution.tool_calls, "
                "approval_governance.approval_revocations, "
                "approval_governance.approval_decisions, "
                "approval_governance.approval_requests, "
                "execution_control.idempotency_records, "
                "permission_governance.tool_permission_activations, "
                "permission_governance.tool_permission_versions, "
                "permission_governance.tool_permissions, "
                "tool_governance.tool_activations, tool_governance.tool_versions, "
                "tool_governance.tool_definitions, agent_governance.agent_activations, "
                "agent_governance.agent_versions, agent_governance.agent_definitions"
            )
        )
        await connection.execute(text("TRUNCATE audit.audit_records"))
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


@pytest_asyncio.fixture
async def publisher_engine(publisher_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(publisher_database_url, pool_size=2, max_overflow=0)
    yield engine
    await engine.dispose()


@pytest.fixture
def identity_stack(runtime_database_url: str) -> IdentityStack:
    sessions = create_session_factory(runtime_database_url)
    return IdentityStack(
        SqlAlchemyUnitOfWorkFactory(sessions),
        IdentityAuditService(PostgresStandaloneAuditWriter(sessions), b"test-fingerprint-key" * 2),
    )


@pytest.fixture
def agent_registry_factory(
    runtime_database_url: str,
) -> SqlAlchemyAgentRegistryUnitOfWorkFactory:
    return SqlAlchemyAgentRegistryUnitOfWorkFactory(create_session_factory(runtime_database_url))


@pytest.fixture
def tool_control_factory(
    admin_database_url: str,
) -> SqlAlchemyToolRegistryUnitOfWorkFactory:
    return SqlAlchemyToolRegistryUnitOfWorkFactory(create_session_factory(admin_database_url))


@pytest.fixture
def tool_runtime_factory(
    runtime_database_url: str,
) -> SqlAlchemyToolRegistryUnitOfWorkFactory:
    return SqlAlchemyToolRegistryUnitOfWorkFactory(create_session_factory(runtime_database_url))


@pytest.fixture
def permission_factory(runtime_database_url: str) -> SqlAlchemyPermissionUnitOfWorkFactory:
    return SqlAlchemyPermissionUnitOfWorkFactory(create_session_factory(runtime_database_url))


@pytest.fixture
def approval_factory(runtime_database_url: str) -> SqlAlchemyApprovalUnitOfWorkFactory:
    return SqlAlchemyApprovalUnitOfWorkFactory(create_session_factory(runtime_database_url))


@pytest.fixture
def idempotency_factory(runtime_database_url: str) -> SqlAlchemyIdempotencyUnitOfWorkFactory:
    return SqlAlchemyIdempotencyUnitOfWorkFactory(create_session_factory(runtime_database_url))


@pytest.fixture
def gateway_factory(runtime_database_url: str) -> SqlAlchemyGatewayUnitOfWorkFactory:
    return SqlAlchemyGatewayUnitOfWorkFactory(create_session_factory(runtime_database_url))
