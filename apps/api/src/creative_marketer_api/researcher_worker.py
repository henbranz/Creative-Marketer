"""Production-capable Researcher Temporal worker and scoped Outbox→Inbox bridge."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from temporalio.worker import Worker

from creative_marketer.agent_runtime.application import (
    AgentRunService,
    ModelProvider,
    ModelProviderRegistry,
    ModelRouter,
    initial_commerce_operations_route,
    initial_creative_strategist_route,
    initial_intelligence_route,
    initial_researcher_route,
)
from creative_marketer.agent_runtime.domain import ModelInvocationResult, ModelUsage
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
    FakeModelProvider,
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


def _fake_demo_result(invocation: object) -> ModelInvocationResult:
    """Zero-network structured outputs for every Agent supported by the local worker."""
    from scripts.bootstrap_demo import (
        _creative_output,
        _intelligence_output,
        _production_output,
    )

    contract = invocation.output_contract_key  # type: ignore[attr-defined]
    if contract == "creative.creative_concept_set":
        output = _creative_output(invocation)
    elif contract == "production.production_plan":
        output = _production_output(invocation)
    elif contract == "intelligence.intelligence_report":
        output = _intelligence_output(invocation)
    elif contract == "commerce.operations_report":
        capability_context = invocation.capability_context or {}  # type: ignore[attr-defined]
        inventories = capability_context.get("inventory", ())
        orders = capability_context.get("orders", ())
        deterministic = capability_context.get("deterministic_exceptions", ())
        inventory_ids = {
            str(item["observation_id"])
            for item in inventories
            if isinstance(item, dict) and "observation_id" in item
        }
        order_ids = {
            str(item["observation_id"])
            for item in orders
            if isinstance(item, dict) and "observation_id" in item
        }
        inventory_exceptions = [
            {
                "observation_id": str(item["observation_id"]),
                "explanation": f"Deterministic {item['kind']} rule matched.",
            }
            for item in deterministic
            if isinstance(item, dict) and str(item.get("observation_id")) in inventory_ids
        ]
        order_exceptions = [
            {
                "observation_id": str(item["observation_id"]),
                "explanation": f"Deterministic {item['kind']} rule matched.",
            }
            for item in deterministic
            if isinstance(item, dict) and str(item.get("observation_id")) in order_ids
        ]
        output = {
            "summary": "Fake Store commerce observations were analyzed without network access.",
            "inventory_exceptions": inventory_exceptions,
            "order_exceptions": order_exceptions,
            "action_proposals": [],
            "limitations": [
                "Synthetic Fake Store analysis; exact human approval is required for mutations."
            ],
        }
    else:
        raise RuntimeError("fake worker does not support this structured output contract")
    return ModelInvocationResult(
        output,
        f"local-demo-{contract}",
        ModelUsage(100, 100, 200),
        "openai",
        "gpt-5.6-sol",
    )


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
    if settings.model_provider_backend not in {"openai", "fake"}:
        raise RuntimeError("Agent worker requires MODEL_PROVIDER_BACKEND=openai or fake")
    if settings.model_provider_backend == "openai":
        if settings.openai_api_key is None:
            raise RuntimeError("OpenAI Agent worker requires OPENAI_API_KEY")
        authorization = getattr(settings, "require_live_spend_authorization", None)
        if authorization is not None:
            authorization()
    telemetry = NullTelemetry()
    routes = (
        initial_researcher_route(),
        initial_creative_strategist_route(),
        initial_producer_route(),
        initial_intelligence_route(),
        initial_commerce_operations_route(),
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
    provider: ModelProvider
    if settings.model_provider_backend == "fake":
        provider = FakeModelProvider(_fake_demo_result)
    else:
        assert settings.openai_api_key is not None
        provider = (
            OpenAIResponsesModelProvider(
                settings.openai_api_key.get_secret_value(),
                image_materializer=DatabaseObjectStoreImageMaterializer(
                    session_factory, object_store
                ),
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
