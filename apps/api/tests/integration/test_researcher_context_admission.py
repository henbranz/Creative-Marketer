# mypy: disable-error-code="no-untyped-def,no-untyped-call"

from dataclasses import replace

import pytest
from sqlalchemy import text

from creative_marketer.agent_governance.application import (
    ActivateAgentVersion,
    CreateAgentVersion,
    CreateTenantAgentDefinition,
)
from creative_marketer.agent_runtime.application import (
    AgentRunService,
    ModelProviderRegistry,
    ModelRouter,
    initial_researcher_route,
)
from creative_marketer.agent_runtime.domain import (
    AgentContextBudgetExceeded,
    ModelInvocationResult,
    ModelUsage,
)
from creative_marketer.catalog.application import CatalogService
from creative_marketer.infrastructure.database.agent_runtime_uow import (
    SqlAlchemyAgentRuntimeUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.model_providers.fake import FakeModelProvider
from creative_marketer.research.application import ResearchService
from creative_marketer.research.domain import ResearchCategory
from tests.integration.test_agent_runtime import IdentityProvider
from tests.integration.test_asset_library import product_setup
from tests.integration.test_research_evidence import MemoryStore, StaticFetcher
from tests.test_agent_runtime_application import configuration


@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["FIXED_CONTEXT_TOO_LARGE", "NO_EVIDENCE_BLOCK_FITS"])
async def test_admission_denial_commits_only_safe_audit_and_leaves_key_reusable(
    reason,
    admin_engine,
    catalog_factory,
    research_factory,
    agent_registry_factory,
    runtime_database_url,
):
    ctx, _, product = await product_setup(admin_engine, catalog_factory)
    catalog = CatalogService(catalog_factory)
    workspace = await catalog.get_workspace(ctx, product.id)
    if reason == "FIXED_CONTEXT_TOO_LARGE":
        # A valid bounded semantic field can still exceed a UTF-8 byte envelope.
        await catalog.save_brief(ctx, replace(workspace.brief, product_why="界" * 3000))
    snapshot = await catalog.create_snapshot(ctx, product.id)
    research = ResearchService(
        research_factory, StaticFetcher(b"<p>" + b"e" * 8000 + b"</p>"), MemoryStore()
    )
    await research.create_source(
        ctx,
        product_id=product.id,
        url="https://public.example/evidence",
        display_name="Evidence",
        category=ResearchCategory.COMPETITOR,
    )
    definition = await CreateTenantAgentDefinition(agent_registry_factory)(
        ctx, agent_key="researcher", agent_type="researcher"
    )
    version = await CreateAgentVersion(agent_registry_factory)(ctx, definition.id, configuration())
    await ActivateAgentVersion(agent_registry_factory)(ctx, definition.id, version.id)
    provider = FakeModelProvider(
        ModelInvocationResult({}, None, ModelUsage(0, 0, 0), "openai", "gpt-5.6-sol")
    )
    uows = SqlAlchemyAgentRuntimeUnitOfWorkFactory(create_session_factory(runtime_database_url))
    service = AgentRunService(
        uows,
        ModelRouter((initial_researcher_route(),)),
        ModelProviderRegistry({"openai": provider}),
        IdentityProvider(),
    )
    with pytest.raises(AgentContextBudgetExceeded) as raised:
        await service.request_researcher(ctx, product_id=product.id, idempotency_key="denied-key")
    assert raised.value.diagnostics["reason"] == reason
    async with uows(ctx.tenant_id) as uow:
        assert await uow.runs.get_by_idempotency("denied-key") is None
    async with admin_engine.connect() as conn:
        for table in ("agent_runs", "model_attempts", "agent_budget_usage"):
            assert (
                await conn.scalar(
                    text(f"SELECT count(*) FROM agent_runtime.{table} WHERE tenant_id=:tenant"),
                    {"tenant": ctx.tenant_id},
                )
                == 0
            )
        audit = (
            await conn.execute(
                text(
                    "SELECT safe_metadata,agent_run_id FROM audit.audit_records "
                    "WHERE tenant_id=:tenant AND action='agent.run.context_budget_denied'"
                ),
                {"tenant": ctx.tenant_id},
            )
        ).one()
        assert audit.safe_metadata == raised.value.diagnostics
        assert audit.agent_run_id is None
        assert (
            await conn.scalar(
                text(
                    "SELECT count(*) FROM event_delivery.outbox_events "
                    "WHERE tenant_id=:tenant AND event_type='agent.run.requested.v1'"
                ),
                {"tenant": ctx.tenant_id},
            )
            == 0
        )
    assert provider.calls == []
    assert (await catalog.get_workspace(ctx, product.id)).latest_snapshot == snapshot
