"""Production-capable Researcher Temporal worker and scoped Outbox→Inbox bridge."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from temporalio.worker import Worker

from creative_marketer.agent_runtime.application import (
    AgentRunService,
    ModelProviderRegistry,
    ModelRouter,
    initial_creative_strategist_route,
    initial_researcher_route,
)
from creative_marketer.catalog.asset_application import UnavailableObjectStore
from creative_marketer.events.application import (
    ConsumerRegistration,
    ConsumerRegistry,
    ProcessEvent,
)
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.infrastructure.database.agent_runtime_uow import (
    SqlAlchemyAgentRuntimeUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.event_consumer_uow import (
    SqlAlchemyConsumerUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.event_delivery import PostgresPublisherStore
from creative_marketer.infrastructure.model_providers import (
    DatabaseObjectStoreImageMaterializer,
    OpenAIResponsesModelProvider,
)
from creative_marketer.infrastructure.object_storage import S3ObjectStore
from creative_marketer.infrastructure.temporal.activities import TemporalActivities
from creative_marketer.infrastructure.temporal.client import (
    TemporalAgentExecutionWorkflowStarter,
    TemporalResearcherWorkflowStarter,
)
from creative_marketer.infrastructure.temporal.configuration import WORKFLOW_TASK_QUEUE
from creative_marketer.infrastructure.temporal.worker import connect_client
from creative_marketer.infrastructure.temporal.workflows import (
    AgentExecutionWorkflow,
    ResearcherWorkflow,
)
from creative_marketer.infrastructure.workload_identity import ConfiguredWorkloadIdentityProvider
from creative_marketer.observability.ports import NullTelemetry
from creative_marketer.production.application import initial_producer_route
from creative_marketer.workflow_orchestration.agent_bridge import RouteAgentWorkflow
from creative_marketer.workflow_orchestration.researcher_bridge import StartResearcherWorkflow
from creative_marketer_api.config import Settings


class DatabaseAgentTypeResolver:
    def __init__(self, factory: SqlAlchemyAgentRuntimeUnitOfWorkFactory) -> None:
        self._factory = factory

    async def agent_type(self, tenant_id: UUID, run_id: UUID) -> str | None:
        async with self._factory(tenant_id) as uow:
            run = await uow.runs.get(run_id)
            return run.agent_type if run else None


async def _bridge_loop(
    settings: Settings,
    researcher: TemporalResearcherWorkflowStarter,
    agent: TemporalAgentExecutionWorkflowStarter | None = None,
    resolver: DatabaseAgentTypeResolver | None = None,
) -> None:
    if settings.event_publisher_database_url is None:
        raise RuntimeError("EVENT_PUBLISHER_DATABASE_URL is required by the Researcher bridge")
    handler = (
        RouteAgentWorkflow(researcher, agent, resolver)
        if agent is not None and resolver is not None
        else StartResearcherWorkflow(researcher)
    )
    publisher = PostgresPublisherStore(
        create_session_factory(str(settings.event_publisher_database_url))
    )
    consumer = ProcessEvent(
        EventContractRegistry(),
        ConsumerRegistry(
            (
                ConsumerRegistration(
                    "researcher-temporal-starter",
                    frozenset({"agent.run.requested.v1"}),
                    "v1",
                    handler,
                ),
            )
        ),
        SqlAlchemyConsumerUnitOfWorkFactory(create_session_factory(str(settings.database_url))),
    )
    worker_id = uuid4()
    while True:
        now = datetime.now(UTC)
        claimed = await publisher.claim_ready_types(
            worker_id,
            event_types=("agent.run.requested.v1",),
            batch_size=25,
            now=now,
            lease_duration=timedelta(seconds=30),
        )
        for item in claimed:
            try:
                await consumer("researcher-temporal-starter", item.event, item.trace_context)
            except Exception as error:
                digest = "sha256:" + hashlib.sha256(type(error).__name__.encode()).hexdigest()
                await publisher.mark_retryable(
                    item,
                    next_attempt_at=datetime.now(UTC) + timedelta(seconds=5),
                    error_code="RESEARCHER_BRIDGE_UNAVAILABLE",
                    error_digest=digest,
                    now=datetime.now(UTC),
                )
            else:
                await publisher.mark_published(item, now=datetime.now(UTC))
        await asyncio.sleep(0.5 if claimed else 2)


async def run() -> None:
    settings = Settings()
    if settings.model_provider_backend != "openai" or settings.openai_api_key is None:
        raise RuntimeError("Researcher worker requires MODEL_PROVIDER_BACKEND=openai")
    authorization = getattr(settings, "require_live_spend_authorization", None)
    if authorization is not None:
        authorization()
    telemetry = NullTelemetry()
    routes = (
        initial_researcher_route(),
        initial_creative_strategist_route(),
        initial_producer_route(),
    )
    session_factory = create_session_factory(str(settings.database_url))
    runtime_uow = SqlAlchemyAgentRuntimeUnitOfWorkFactory(session_factory)
    object_store = (
        S3ObjectStore(
            endpoint_url=str(settings.object_storage_endpoint_url),
            public_endpoint_url=str(settings.object_storage_public_endpoint_url),
            region=settings.object_storage_region,
            bucket=settings.object_storage_bucket,
            access_key_id=settings.object_storage_access_key_id,
            secret_access_key=settings.object_storage_secret_access_key.get_secret_value(),
            upload_ttl_seconds=settings.asset_upload_ttl_seconds,
            download_ttl_seconds=settings.asset_download_ttl_seconds,
        )
        if getattr(settings, "object_storage_backend", "disabled") == "s3"
        else UnavailableObjectStore()
    )
    provider = (
        OpenAIResponsesModelProvider(
            settings.openai_api_key.get_secret_value(),
            image_materializer=DatabaseObjectStoreImageMaterializer(session_factory, object_store),
        )
        if getattr(settings, "object_storage_backend", "disabled") == "s3"
        else OpenAIResponsesModelProvider(settings.openai_api_key.get_secret_value())
    )
    runtime = AgentRunService(
        runtime_uow,
        ModelRouter(routes),
        ModelProviderRegistry({"openai": provider}),
        ConfiguredWorkloadIdentityProvider(settings.agent_workload_id, settings.app_env),
        telemetry,
    )
    client = await connect_client(
        __import__("os").getenv("TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=__import__("os").getenv("TEMPORAL_NAMESPACE", "default"),
    )
    activities = TemporalActivities(None, None, telemetry, runtime)  # type: ignore[arg-type]
    async with Worker(
        client,
        task_queue=WORKFLOW_TASK_QUEUE,
        workflows=[ResearcherWorkflow, AgentExecutionWorkflow],
        activities=[activities.execute_researcher, activities.execute_agent],
    ):
        await _bridge_loop(
            settings,
            TemporalResearcherWorkflowStarter(client),
            TemporalAgentExecutionWorkflowStarter(client),
            DatabaseAgentTypeResolver(runtime_uow),
        )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
