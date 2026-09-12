"""Independent workload-authenticated governed media worker and approval-event bridge."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from temporalio.worker import Worker

from creative_marketer.agent_governance.application import ResolveActiveAgentVersion
from creative_marketer.catalog.asset_application import AssetService
from creative_marketer.events.application import (
    ConsumerRegistration,
    ConsumerRegistry,
    ProcessEvent,
)
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.infrastructure.database.agent_governance_uow import (
    SqlAlchemyAgentRegistryUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.catalog_uow import SqlAlchemyCatalogUnitOfWorkFactory
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.event_consumer_uow import (
    SqlAlchemyConsumerUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.event_delivery import PostgresPublisherStore
from creative_marketer.infrastructure.database.permission_governance_uow import (
    SqlAlchemyPermissionUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.production_authority import (
    MediaWorkloadIdentity,
    SqlAlchemyGenerationAuthority,
)
from creative_marketer.infrastructure.database.production_uow import (
    SqlAlchemyProductionUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.tool_execution_uow import (
    SqlAlchemyGatewayUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.tool_governance_uow import (
    SqlAlchemyToolRegistryUnitOfWorkFactory,
)
from creative_marketer.infrastructure.object_storage import S3ObjectStore
from creative_marketer.infrastructure.temporal.activities import TemporalActivities
from creative_marketer.infrastructure.temporal.client import TemporalMediaProductionWorkflowStarter
from creative_marketer.infrastructure.temporal.configuration import WORKFLOW_TASK_QUEUE
from creative_marketer.infrastructure.temporal.worker import connect_client
from creative_marketer.infrastructure.temporal.workflows import MediaProductionWorkflow
from creative_marketer.observability.ports import NullTelemetry
from creative_marketer.production.execution import (
    GenerationJobResourceResolver,
    GovernedProductionJobExecutor,
    ImageGenerateToolExecutor,
    VideoImportToolExecutor,
    VideoStartToolExecutor,
    VideoStatusToolExecutor,
    normalize_generation_input,
)
from creative_marketer.production.gateway_composition import compose_production_gateway
from creative_marketer.production.infrastructure import (
    ApplicationGeneratedAssetImporter,
    FakeImageProvider,
    FakeSeedanceMediaProvider,
    OpenAIImageProvider,
    SeedanceMediaProvider,
)
from creative_marketer.tool_execution.application import (
    ToolExecutionBinding,
    ToolExecutor,
)
from creative_marketer.tool_governance.application import ResolveActiveTool
from creative_marketer.workflow_orchestration.production_bridge import (
    DatabaseProductionJobSetResolver,
    StartMediaProductionWorkflow,
)
from creative_marketer_api.config import Settings


def _workload(settings: Settings) -> MediaWorkloadIdentity:
    actor_id = settings.media_workload_actor_id
    workload_id = settings.media_workload_id
    if settings.app_env in {"development", "test"}:
        actor_id = actor_id or uuid5(NAMESPACE_URL, "creative-marketer:local-media-worker")
        workload_id = workload_id or "local-media-worker"
    if actor_id is None or workload_id is None:
        raise RuntimeError("deployment-issued media workload identity is required")
    return MediaWorkloadIdentity(actor_id, workload_id, settings.app_env)


async def _bridge_loop(settings: Settings, handler: StartMediaProductionWorkflow) -> None:
    if settings.event_publisher_database_url is None:
        raise RuntimeError("EVENT_PUBLISHER_DATABASE_URL is required by the Production bridge")
    publisher = PostgresPublisherStore(
        create_session_factory(str(settings.event_publisher_database_url))
    )
    consumer = ProcessEvent(
        EventContractRegistry(),
        ConsumerRegistry(
            (
                ConsumerRegistration(
                    "production-temporal-starter",
                    frozenset({"production.plan.approved_for_generation.v1"}),
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
            event_types=("production.plan.approved_for_generation.v1",),
            batch_size=25,
            now=now,
            lease_duration=timedelta(seconds=30),
        )
        for item in claimed:
            try:
                await consumer("production-temporal-starter", item.event, item.trace_context)
            except Exception as error:
                digest = "sha256:" + hashlib.sha256(type(error).__name__.encode()).hexdigest()
                await publisher.mark_retryable(
                    item,
                    next_attempt_at=datetime.now(UTC) + timedelta(seconds=5),
                    error_code="PRODUCTION_BRIDGE_UNAVAILABLE",
                    error_digest=digest,
                    now=datetime.now(UTC),
                )
            else:
                await publisher.mark_published(item, now=datetime.now(UTC))
        await asyncio.sleep(0.5 if claimed else 2)


async def run() -> None:
    settings = Settings()
    if settings.object_storage_backend != "s3":
        raise RuntimeError("Production worker requires private S3-compatible object storage")
    if settings.media_image_provider == "disabled" or settings.media_video_provider == "disabled":
        raise RuntimeError("Production worker requires explicit image and video provider selection")
    workload = _workload(settings)
    sessions = create_session_factory(str(settings.database_url))
    object_store = S3ObjectStore(
        endpoint_url=str(settings.object_storage_endpoint_url),
        public_endpoint_url=str(settings.object_storage_public_endpoint_url),
        region=settings.object_storage_region,
        bucket=settings.object_storage_bucket,
        access_key_id=settings.object_storage_access_key_id,
        secret_access_key=settings.object_storage_secret_access_key.get_secret_value(),
        upload_ttl_seconds=settings.asset_upload_ttl_seconds,
        download_ttl_seconds=settings.asset_download_ttl_seconds,
    )
    authority = SqlAlchemyGenerationAuthority(sessions, object_store, workload)
    image_provider = (
        FakeImageProvider()
        if settings.media_image_provider == "fake"
        else OpenAIImageProvider(settings.openai_api_key.get_secret_value())  # type: ignore[union-attr]
    )
    video_provider = (
        FakeSeedanceMediaProvider()
        if settings.media_video_provider == "fake"
        else SeedanceMediaProvider(settings.byteplus_las_api_key.get_secret_value())  # type: ignore[union-attr]
    )
    image_importer = ApplicationGeneratedAssetImporter(
        AssetService(SqlAlchemyCatalogUnitOfWorkFactory(sessions), object_store),
        sessions,
        local_demo=settings.media_image_provider == "fake",
    )
    video_importer = ApplicationGeneratedAssetImporter(
        AssetService(SqlAlchemyCatalogUnitOfWorkFactory(sessions), object_store),
        sessions,
        local_demo=settings.media_video_provider == "fake",
    )
    tool_factory = SqlAlchemyToolRegistryUnitOfWorkFactory(sessions)
    tool_resolver = ResolveActiveTool(tool_factory)
    resource_resolver = GenerationJobResourceResolver(authority)
    executors: dict[str, ToolExecutor] = {
        "media.image.generate": ImageGenerateToolExecutor(
            authority, image_provider, image_importer
        ),
        "media.video.generate.start": VideoStartToolExecutor(authority, video_provider),
        "media.video.generate.status": VideoStatusToolExecutor(authority, video_provider),
        "media.video.generate.import": VideoImportToolExecutor(
            authority, video_provider, video_importer
        ),
    }
    bindings = []
    for tool_key, executor in executors.items():
        tool = await tool_resolver(tool_key)
        bindings.append(
            ToolExecutionBinding(
                tool.definition_id,
                tool.version_id,
                normalize_generation_input,
                resource_resolver,
                executor,
                credential_capable=True,
            )
        )
    agent_factory = SqlAlchemyAgentRegistryUnitOfWorkFactory(sessions)
    agent_resolver = ResolveActiveAgentVersion(agent_factory)
    permission_factory = SqlAlchemyPermissionUnitOfWorkFactory(sessions)
    gateway = compose_production_gateway(
        agent_resolver,
        tool_resolver,
        permission_factory,
        tuple(bindings),
        SqlAlchemyGatewayUnitOfWorkFactory(sessions),
    )
    jobs = GovernedProductionJobExecutor(authority, gateway)
    client = await connect_client(
        __import__("os").getenv("TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=__import__("os").getenv("TEMPORAL_NAMESPACE", "default"),
    )
    activities = TemporalActivities(None, None, NullTelemetry(), production_jobs=jobs)  # type: ignore[arg-type]
    handler = StartMediaProductionWorkflow(
        TemporalMediaProductionWorkflowStarter(client),
        DatabaseProductionJobSetResolver(SqlAlchemyProductionUnitOfWorkFactory(sessions)),
    )
    async with Worker(
        client,
        task_queue=WORKFLOW_TASK_QUEUE,
        workflows=[MediaProductionWorkflow],
        activities=[activities.execute_production_job],
    ):
        await _bridge_loop(settings, handler)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
