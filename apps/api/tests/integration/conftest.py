import os
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from creative_marketer.audit.identity import IdentityAuditService
from creative_marketer.infrastructure.database.agent_governance_schema import (
    agent_activations,
    agent_definitions,
    agent_versions,
)
from creative_marketer.infrastructure.database.agent_governance_uow import (
    SqlAlchemyAgentRegistryUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.approval_uow import (
    SqlAlchemyApprovalUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.audit import PostgresStandaloneAuditWriter
from creative_marketer.infrastructure.database.catalog_uow import SqlAlchemyCatalogUnitOfWorkFactory
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.execution_control_uow import (
    SqlAlchemyIdempotencyUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.permission_governance_uow import (
    SqlAlchemyPermissionUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.research_uow import (
    SqlAlchemyResearchUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.tool_execution_uow import (
    SqlAlchemyGatewayUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.tool_governance_uow import (
    SqlAlchemyToolRegistryUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.uow import SqlAlchemyUnitOfWorkFactory
from scripts.bootstrap_intelligence import (
    INTELLIGENCE_PLATFORM_TEMPLATE_ID,
    intelligence_configuration,
)
from tests.integration.support import IdentityStack

INTELLIGENCE_PLATFORM_VERSION_ID = UUID("775ebee9-df02-59d5-a468-032fc7e21384")
INTELLIGENCE_SYSTEM_ACTOR_ID = UUID("ffecd023-0ca1-5208-b90c-2fbc5370c07a")


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
                "TRUNCATE measurement.performance_snapshots, "
                "intelligence.experiment_decisions, intelligence.experiment_proposals, "
                "intelligence.insight_decisions, intelligence.insight_candidates, "
                "intelligence.reports, intelligence.performance_comparisons, "
                "intelligence.creative_feature_snapshots, intelligence.context_manifests, "
                "measurement.attribution_results, measurement.conversion_observations, "
                "measurement.attribution_references, measurement.collection_runs, "
                "measurement.performance_observations, "
                "publishing.publications, publishing.publication_jobs, "
                "publishing.publication_decisions, publishing.publication_drafts, "
                "publishing.social_accounts, assembly.final_creative_decisions, "
                "assembly.final_creatives, "
                "assembly.assembly_jobs, assembly.manual_source_bindings, "
                "assembly.overlay_instructions, assembly.caption_cues, "
                "assembly.assembly_items, assembly.assembly_plans, "
                "knowledge_projection.projection_changes, "
                "knowledge_projection.projection_nodes, "
                "catalog.asset_lineage, production.media_budget_usage, "
                "production.generation_jobs, production.plan_decisions, "
                "production.generation_segments, production.production_shots, "
                "production.production_scenes, production.production_plans, "
                "creative.concept_decisions, creative.concepts, "
                "creative.concept_sets, research.research_snapshots, "
                "agent_runtime.model_cost_reconciliations, agent_runtime.model_attempts, "
                "agent_runtime.agent_budget_usage, agent_runtime.agent_runs, "
                "research.social_evidence_snapshots, research.research_targets, "
                "research.evidence_snapshots, research.source_fetches, "
                "research.sources, catalog.assets, catalog.product_knowledge_snapshots, "
                "catalog.product_briefs, catalog.product_profiles, catalog.products, "
                "catalog.brand_profiles, catalog.brands, "
                "event_delivery.inbox_receipts, event_delivery.outbox_events, "
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
        configuration = intelligence_configuration()
        await connection.execute(
            insert(agent_definitions).values(
                id=INTELLIGENCE_PLATFORM_TEMPLATE_ID,
                scope_kind="platform",
                tenant_id=None,
                platform_template_id=None,
                agent_key="performance_intelligence",
                agent_type="intelligence",
                status="active",
                created_by_actor_kind="system",
                created_by_actor_id=INTELLIGENCE_SYSTEM_ACTOR_ID,
            )
        )
        await connection.execute(
            insert(agent_versions).values(
                id=INTELLIGENCE_PLATFORM_VERSION_ID,
                definition_id=INTELLIGENCE_PLATFORM_TEMPLATE_ID,
                scope_kind="platform",
                tenant_id=None,
                version_number=1,
                **configuration.primitive(),
                configuration_digest=configuration.configuration_digest,
                created_by_actor_kind="system",
                created_by_actor_id=INTELLIGENCE_SYSTEM_ACTOR_ID,
            )
        )
        await connection.execute(
            insert(agent_activations).values(
                definition_id=INTELLIGENCE_PLATFORM_TEMPLATE_ID,
                active_version_id=INTELLIGENCE_PLATFORM_VERSION_ID,
                scope_kind="platform",
                tenant_id=None,
                activated_by_actor_kind="system",
                activated_by_actor_id=INTELLIGENCE_SYSTEM_ACTOR_ID,
            )
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
def catalog_factory(runtime_database_url: str) -> SqlAlchemyCatalogUnitOfWorkFactory:
    return SqlAlchemyCatalogUnitOfWorkFactory(create_session_factory(runtime_database_url))


@pytest.fixture
def research_factory(runtime_database_url: str) -> SqlAlchemyResearchUnitOfWorkFactory:
    return SqlAlchemyResearchUnitOfWorkFactory(create_session_factory(runtime_database_url))


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
