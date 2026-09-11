"""Production-capable Researcher Temporal worker and scoped Outbox→Inbox bridge."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from temporalio.worker import Worker

from creative_marketer.agent_runtime.application import (
    AgentRunService,
    ModelProviderRegistry,
    ModelRouter,
    initial_researcher_route,
)
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
from creative_marketer.infrastructure.model_providers import OpenAIResponsesModelProvider
from creative_marketer.infrastructure.temporal.activities import TemporalActivities
from creative_marketer.infrastructure.temporal.client import TemporalResearcherWorkflowStarter
from creative_marketer.infrastructure.temporal.configuration import WORKFLOW_TASK_QUEUE
from creative_marketer.infrastructure.temporal.worker import connect_client
from creative_marketer.infrastructure.temporal.workflows import ResearcherWorkflow
from creative_marketer.infrastructure.workload_identity import ConfiguredWorkloadIdentityProvider
from creative_marketer.observability.ports import NullTelemetry
from creative_marketer.workflow_orchestration.researcher_bridge import StartResearcherWorkflow
from creative_marketer_api.config import Settings


async def _bridge_loop(settings: Settings, starter: TemporalResearcherWorkflowStarter) -> None:
    if settings.event_publisher_database_url is None:
        raise RuntimeError("EVENT_PUBLISHER_DATABASE_URL is required by the Researcher bridge")
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
                    StartResearcherWorkflow(starter),
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
    telemetry = NullTelemetry()
    route = initial_researcher_route()
    runtime = AgentRunService(
        SqlAlchemyAgentRuntimeUnitOfWorkFactory(create_session_factory(str(settings.database_url))),
        ModelRouter((route,)),
        ModelProviderRegistry(
            {"openai": OpenAIResponsesModelProvider(settings.openai_api_key.get_secret_value())}
        ),
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
        workflows=[ResearcherWorkflow],
        activities=[activities.execute_researcher],
    ):
        await _bridge_loop(settings, TemporalResearcherWorkflowStarter(client))


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
