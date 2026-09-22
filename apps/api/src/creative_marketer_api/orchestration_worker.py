"""IDs-only Creative Cycle, fake publishing, and fake measurement Temporal worker."""

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Executable
from temporalio.worker import Worker

from creative_marketer.agent_runtime.application import (
    AgentRunService,
    ModelProviderRegistry,
    ModelRouter,
    initial_agent_model_routes,
)
from creative_marketer.assembly.application import AssemblyService
from creative_marketer.catalog.application import CatalogService
from creative_marketer.events.application import (
    ConsumerRegistration,
    ConsumerRegistry,
    ConsumerUnitOfWork,
    ProcessEvent,
)
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import DomainEvent, EventScopeKind
from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.infrastructure.database.agent_runtime_uow import (
    SqlAlchemyAgentRuntimeUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.assembly_uow import (
    SqlAlchemyAssemblyUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.catalog_uow import SqlAlchemyCatalogUnitOfWorkFactory
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.event_consumer_uow import (
    SqlAlchemyConsumerUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.event_delivery import PostgresPublisherStore
from creative_marketer.infrastructure.database.measurement_uow import (
    SqlAlchemyMeasurementUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.orchestration_schema import creative_cycles
from creative_marketer.infrastructure.database.orchestration_uow import (
    SqlAlchemyOrchestrationUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.publishing_authority import (
    SqlAlchemyPublicationExecutionAuthority,
)
from creative_marketer.infrastructure.database.publishing_schema import (
    publication_drafts,
    publications,
)
from creative_marketer.infrastructure.database.publishing_uow import (
    SqlAlchemyPublishingUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.schema import memberships, tenants, users
from creative_marketer.infrastructure.model_providers import ExecutionProcessOnlyModelProvider
from creative_marketer.infrastructure.temporal.activities import TemporalActivities
from creative_marketer.infrastructure.temporal.configuration import ORCHESTRATION_TASK_QUEUE
from creative_marketer.infrastructure.temporal.worker import connect_client
from creative_marketer.infrastructure.temporal.workflows import (
    CreativeCycleWorkflow,
    PerformanceCollectionWorkflow,
    PublicationWorkflow,
)
from creative_marketer.infrastructure.workload_identity import ConfiguredWorkloadIdentityProvider
from creative_marketer.measurement.application import MeasurementService
from creative_marketer.measurement.provider import FakeSocialMetricsProvider
from creative_marketer.orchestration.application import CreativeCycleService
from creative_marketer.orchestration.domain import CycleStatus
from creative_marketer.publishing.application import PublishingService
from creative_marketer.publishing.execution import GovernedPublicationJobExecutor
from creative_marketer.publishing.gateway_composition import PublishingGatewayFactory
from creative_marketer.publishing.provider import FakeSocialProvider
from creative_marketer.workflow_orchestration.contracts import (
    CreativeCycleActivityResult,
    MeasurementActivityResult,
)
from creative_marketer_api.config import Settings
from creative_marketer_api.orchestration_temporal import LazyOrchestrationWorkflowCoordinator

WORKLOAD_ACTOR_ID = uuid5(NAMESPACE_URL, "creative-marketer:orchestration-worker")
CYCLE_WAKE_EVENTS = frozenset(
    {
        "research.snapshot.created.v1",
        "creative.concept_set.created.v1",
        "creative.concept.approved_for_production.v1",
        "production.plan.created.v1",
        "production.generation.completed.v1",
        "assembly.final_creative.created.v1",
        "assembly.final_creative.approved_for_publishing.v1",
        "publishing.draft.created.v1",
        "publishing.draft.approved.v1",
        "publishing.publication.published.v1",
        "measurement.performance_snapshot.created.v1",
        "intelligence.report.created.v1",
        "intelligence.experiment.approved.v1",
    }
)


class WakeActiveCreativeCycles:
    """Event facts are wake hints only; reconciliation reloads PostgreSQL authority."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        workflows: LazyOrchestrationWorkflowCoordinator,
    ) -> None:
        self._sessions, self._workflows = sessions, workflows

    async def __call__(self, event: DomainEvent, _uow: ConsumerUnitOfWork) -> None:
        if (
            event.event_type not in CYCLE_WAKE_EVENTS
            or event.scope_kind is not EventScopeKind.TENANT
            or event.tenant_id is None
        ):
            raise ValueError("unsupported Creative Cycle wake event")
        async with self._sessions() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(event.tenant_id)},
            )
            cycle_ids = (
                (
                    await session.execute(
                        select(creative_cycles.c.id)
                        .where(
                            creative_cycles.c.status.in_(("ACTIVE", "BLOCKED", "NEEDS_RECOVERY"))
                        )
                        .order_by(creative_cycles.c.created_at)
                        .limit(100)
                    )
                )
                .scalars()
                .all()
            )
        for cycle_id in cycle_ids:
            await self._workflows.wake_cycle(event.tenant_id, cycle_id)


async def _event_bridge_loop(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    workflows: LazyOrchestrationWorkflowCoordinator,
) -> None:
    if settings.event_publisher_database_url is None:
        raise RuntimeError("EVENT_PUBLISHER_DATABASE_URL is required by orchestration bridge")
    publisher = PostgresPublisherStore(
        create_session_factory(str(settings.event_publisher_database_url))
    )
    consumer = ProcessEvent(
        EventContractRegistry(),
        ConsumerRegistry(
            (
                ConsumerRegistration(
                    "creative-cycle-temporal-wakeup",
                    CYCLE_WAKE_EVENTS,
                    "v1",
                    WakeActiveCreativeCycles(sessions, workflows),
                ),
            )
        ),
        SqlAlchemyConsumerUnitOfWorkFactory(sessions),
    )
    worker_id = uuid4()
    while True:
        now = datetime.now(UTC)
        claimed = await publisher.claim_ready_types(
            worker_id,
            event_types=tuple(CYCLE_WAKE_EVENTS),
            batch_size=25,
            now=now,
            lease_duration=timedelta(seconds=30),
        )
        for item in claimed:
            try:
                await consumer("creative-cycle-temporal-wakeup", item.event, item.trace_context)
            except Exception as error:
                digest = "sha256:" + hashlib.sha256(type(error).__name__.encode()).hexdigest()
                await publisher.mark_retryable(
                    item,
                    next_attempt_at=datetime.now(UTC) + timedelta(seconds=5),
                    error_code="CREATIVE_CYCLE_WAKE_UNAVAILABLE",
                    error_digest=digest,
                    now=datetime.now(UTC),
                )
            else:
                await publisher.mark_published(item, now=datetime.now(UTC))
        await asyncio.sleep(0.5 if claimed else 2)


class TrustedContextResolver:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], environment: str) -> None:
        self._sessions = sessions
        self._environment = environment

    async def for_cycle(
        self, tenant_id: UUID, cycle_id: UUID, correlation_id: UUID
    ) -> ExecutionContext:
        return await self._resolve(
            tenant_id,
            select(creative_cycles.c.initiated_by).where(creative_cycles.c.id == cycle_id),
            correlation_id,
        )

    async def for_publication(
        self, tenant_id: UUID, publication_id: UUID, correlation_id: UUID
    ) -> ExecutionContext:
        return await self._resolve(
            tenant_id,
            select(publication_drafts.c.created_by_user_id)
            .join(
                publications,
                publications.c.publication_draft_id == publication_drafts.c.id,
            )
            .where(publications.c.id == publication_id),
            correlation_id,
        )

    async def _resolve(
        self, tenant_id: UUID, user_query: Executable, correlation_id: UUID
    ) -> ExecutionContext:
        async with self._sessions() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            user_id = await session.scalar(user_query)
            row = (
                await session.execute(
                    select(
                        memberships.c.role,
                        memberships.c.status,
                        users.c.status.label("user_status"),
                        tenants.c.status.label("tenant_status"),
                    )
                    .join(users, users.c.id == memberships.c.user_id)
                    .join(tenants, tenants.c.id == memberships.c.tenant_id)
                    .where(
                        memberships.c.tenant_id == tenant_id,
                        memberships.c.user_id == user_id,
                    )
                )
            ).first()
        if (
            user_id is None
            or row is None
            or row.status != "active"
            or row.user_status != "active"
            or row.tenant_status != "active"
        ):
            raise RuntimeError("initiating user authority is no longer active")
        return ExecutionContext(
            tenant_id,
            Actor(ActorKind.WORKLOAD, WORKLOAD_ACTOR_ID),
            user_id,
            MembershipRole(row.role),
            MembershipStatus(row.status),
            self._environment,
            AuthenticationAssurance(datetime.now(UTC), "workload", "deployment"),
            correlation_id,
        )


class CycleReconciler:
    def __init__(self, service: CreativeCycleService, contexts: TrustedContextResolver) -> None:
        self._service, self._contexts = service, contexts

    async def reconcile(
        self, tenant_id: UUID, cycle_id: UUID, correlation_id: UUID
    ) -> CreativeCycleActivityResult:
        context = await self._contexts.for_cycle(tenant_id, cycle_id, correlation_id)
        cycle = await self._service.reconcile(context, cycle_id)
        return CreativeCycleActivityResult(
            str(cycle.id),
            cycle.status.value,
            cycle.current_stage.value,
            cycle.status in {CycleStatus.COMPLETED, CycleStatus.CANCELLED, CycleStatus.FAILED},
        )


class FakeMeasurementExecutor:
    def __init__(self, service: MeasurementService, contexts: TrustedContextResolver) -> None:
        self._service, self._contexts = service, contexts

    async def collect(
        self, tenant_id: UUID, publication_id: UUID, checkpoint: str
    ) -> MeasurementActivityResult:
        context = await self._contexts.for_publication(tenant_id, publication_id, UUID(int=0))
        snapshot = await self._service.collect(context, publication_id, checkpoint=checkpoint)
        return MeasurementActivityResult(str(publication_id), str(snapshot.id), "SUCCEEDED")


async def run() -> None:
    settings = Settings()
    if settings.app_env not in {"development", "test"}:
        raise RuntimeError("Phase 8 orchestration worker is fake-provider-only")
    sessions = create_session_factory(str(settings.database_url))
    runtime = AgentRunService(
        SqlAlchemyAgentRuntimeUnitOfWorkFactory(sessions),
        ModelRouter(initial_agent_model_routes()),
        ModelProviderRegistry({"openai": ExecutionProcessOnlyModelProvider()}),
        ConfiguredWorkloadIdentityProvider(settings.agent_workload_id, settings.app_env),
    )
    workflows = LazyOrchestrationWorkflowCoordinator(
        settings.temporal_address,
        settings.temporal_namespace,
        accelerated_demo=True,
    )
    service = CreativeCycleService(
        SqlAlchemyOrchestrationUnitOfWorkFactory(sessions),
        CatalogService(SqlAlchemyCatalogUnitOfWorkFactory(sessions)),
        runtime,
        AssemblyService(SqlAlchemyAssemblyUnitOfWorkFactory(sessions)),
        None,
        workflows,
        workflows,
    )
    contexts = TrustedContextResolver(sessions, settings.app_env)
    publishing = PublishingService(
        SqlAlchemyPublishingUnitOfWorkFactory(sessions), FakeSocialProvider()
    )
    publishing_authority = SqlAlchemyPublicationExecutionAuthority(
        sessions, publishing, settings.app_env
    )
    publishing_gateway = await PublishingGatewayFactory(sessions, publishing_authority)()
    activities = TemporalActivities(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        agent_runtime=runtime,
        publication_jobs=GovernedPublicationJobExecutor(
            publishing_authority,
            publishing_gateway,
        ),
        measurement_jobs=FakeMeasurementExecutor(
            MeasurementService(
                SqlAlchemyMeasurementUnitOfWorkFactory(sessions), FakeSocialMetricsProvider()
            ),
            contexts,
        ),
        creative_cycles=CycleReconciler(service, contexts),
    )
    client = await connect_client(
        os.getenv("TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
    )
    async with Worker(
        client,
        task_queue=ORCHESTRATION_TASK_QUEUE,
        workflows=[CreativeCycleWorkflow, PublicationWorkflow, PerformanceCollectionWorkflow],
        activities=[
            activities.reconcile_creative_cycle,
            activities.submit_publication,
            activities.reconcile_publication,
            activities.cancel_publication,
            activities.collect_performance,
        ],
    ):
        await _event_bridge_loop(settings, sessions, workflows)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
