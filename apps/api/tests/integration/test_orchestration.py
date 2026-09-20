from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from creative_marketer.agent_runtime.application import (
    AgentRunService,
    ModelProviderRegistry,
    ModelRouter,
    WorkloadIdentity,
    initial_supervisor_route,
)
from creative_marketer.agent_runtime.domain import (
    AgentRunStatus,
    ModelInvocationResult,
    ModelUsage,
)
from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.infrastructure.database.agent_governance_schema import agent_definitions
from creative_marketer.infrastructure.database.agent_runtime_uow import (
    SqlAlchemyAgentRuntimeUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.orchestration_uow import (
    SqlAlchemyOrchestrationUnitOfWorkFactory,
)
from creative_marketer.infrastructure.model_providers.fake import FakeModelProvider
from creative_marketer.orchestration.application import CanonicalCycleState, CycleReadinessEngine
from creative_marketer.orchestration.domain import (
    CreativeCycle,
    CreativeCycleStep,
    CycleStage,
    CycleStatus,
    CycleTransition,
    StepStatus,
    SupervisorContextManifest,
)
from scripts.bootstrap_supervisor import SUPERVISOR_PLATFORM_TEMPLATE_ID
from tests.integration.test_catalog import (
    brand_body,
    client,
    headers,
    product_body,
    seed_catalog_identity,
)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_orchestration_persistence_is_tenant_scoped_append_only_and_replay_safe(
    admin_engine: AsyncEngine, runtime_database_url: str
) -> None:
    tenant_id, user_id = await seed_catalog_identity(admin_engine)
    async for http in client(runtime_database_url):
        brand = (
            await http.post("/v1/brands", headers=headers(tenant_id, user_id), json=brand_body())
        ).json()
        workspace = (
            await http.post(
                f"/v1/brands/{brand['id']}/products",
                headers=headers(tenant_id, user_id),
                json=product_body(),
            )
        ).json()
        product_id = UUID(workspace["product"]["id"])
        snapshot = (
            await http.post(
                f"/v1/products/{product_id}/snapshots",
                headers=headers(tenant_id, user_id),
            )
        ).json()

    ctx = ExecutionContext(
        tenant_id,
        Actor(ActorKind.USER, user_id),
        user_id,
        MembershipRole.OWNER,
        MembershipStatus.ACTIVE,
        "test",
        AuthenticationAssurance(datetime.now(UTC), "integration", "explicit"),
    )
    tenant_supervisor_id = uuid4()
    async with admin_engine.begin() as connection:
        await connection.execute(
            insert(agent_definitions).values(
                id=tenant_supervisor_id,
                scope_kind="tenant",
                tenant_id=tenant_id,
                platform_template_id=SUPERVISOR_PLATFORM_TEMPLATE_ID,
                agent_key="creative_supervisor",
                agent_type="supervisor",
                status="active",
                created_by_actor_kind="user",
                created_by_actor_id=user_id,
            )
        )
    factory = SqlAlchemyOrchestrationUnitOfWorkFactory(create_session_factory(runtime_database_url))
    cycle = CreativeCycle(
        tenant_id,
        product_id,
        user_id,
        UUID(snapshot["id"]),
        snapshot["digest"],
    )
    transition = CycleTransition(
        tenant_id,
        cycle.id,
        None,
        CycleStage.CREATED,
        CycleStatus.ACTIVE,
        "CYCLE_STARTED",
        "user",
        user_id,
        ctx.correlation_id,
    )
    step = CreativeCycleStep(
        tenant_id,
        cycle.id,
        "research",
        1,
        StepStatus.STARTED,
        f"cycle:{cycle.id}:research:v1",
    )
    readiness = CycleReadinessEngine().cycle(CanonicalCycleState(cycle))
    manifest = SupervisorContextManifest(
        tenant_id,
        product_id,
        cycle.id,
        1,
        CycleStage.CREATED,
        readiness,
        cycle.product_snapshot_id,
        cycle.product_snapshot_digest,
        cycle.artifacts,
        (),
    )
    async with factory(tenant_id) as uow:
        preflight = await uow.cycles.preflight(product_id)
        assert preflight.product_exists and preflight.brief_completeness == 100
        assert await uow.cycles.active_for_product(product_id) is None
        await uow.cycles.add(cycle)
        await uow.cycles.add_transition(transition)
        await uow.cycles.add_step(step)
        await uow.cycles.add_supervisor_manifest(manifest)
        await uow.commit()

    class Identity:
        async def current(self) -> WorkloadIdentity:
            return WorkloadIdentity("test/supervisor-worker", "test")

    sessions = create_session_factory(runtime_database_url)
    runtime = AgentRunService(
        SqlAlchemyAgentRuntimeUnitOfWorkFactory(sessions),
        ModelRouter((initial_supervisor_route(),)),
        ModelProviderRegistry(
            {
                "openai": FakeModelProvider(
                    ModelInvocationResult(
                        {
                            "summary": "Cycle summary.",
                            "current_stage_explanation": "Cycle created.",
                            "blockers": [],
                            "attention_items": [],
                            "suggested_next_actions": [],
                            "completion_summary": None,
                        },
                        "fake-supervisor-response",
                        ModelUsage(100, 100, 200),
                        "openai",
                        "gpt-5.6-sol",
                    )
                )
            }
        ),
        Identity(),
    )
    requested = await runtime.request_supervisor(
        ctx,
        context_manifest_id=manifest.id,
        idempotency_key=f"cycle:{cycle.id}:supervisor:1:v1",
    )
    completed = await runtime.execute(tenant_id, requested.id)
    assert completed.status is AgentRunStatus.SUCCEEDED

    async with factory(tenant_id) as uow:
        stored = await uow.cycles.get(cycle.id, for_update=True)
        assert stored is not None
        active = await uow.cycles.active_for_product(product_id, for_update=True)
        stored_step = await uow.cycles.step(cycle.id, "research")
        stored_report = await uow.cycles.latest_supervisor_report(cycle.id)
        assert active is not None and active.id == cycle.id
        assert stored_step is not None and stored_step.id == step.id
        assert len(await uow.cycles.steps(cycle.id)) == 1
        assert len(await uow.cycles.transitions(cycle.id)) == 1
        assert stored_report is not None and stored_report.agent_run_id == requested.id
        canonical = await uow.cycles.canonical_state(stored)
        assert canonical.current_product_snapshot_id == cycle.product_snapshot_id
        changed = replace(stored, cycle_version=2, current_stage=CycleStage.CHECKING_READINESS)
        assert await uow.cycles.update(changed, expected_version=1)
        assert not await uow.cycles.update(changed, expected_version=1)
        await uow.commit()
