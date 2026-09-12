"""Independent deterministic FFmpeg assembly worker and event bridge."""

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from temporalio.worker import Worker

from creative_marketer.assembly.execution import (
    AssemblyJobExecutor,
    AssemblyWorkloadIdentity,
)
from creative_marketer.assembly.infrastructure import FFmpegAssemblyRenderer
from creative_marketer.catalog.asset_application import AssetService
from creative_marketer.events.application import (
    ConsumerRegistration,
    ConsumerRegistry,
    ProcessEvent,
)
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.infrastructure.database.catalog_uow import SqlAlchemyCatalogUnitOfWorkFactory
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.event_consumer_uow import (
    SqlAlchemyConsumerUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.event_delivery import PostgresPublisherStore
from creative_marketer.infrastructure.object_storage import S3ObjectStore
from creative_marketer.infrastructure.temporal.activities import TemporalActivities
from creative_marketer.infrastructure.temporal.client import (
    TemporalFinalCreativeAssemblyWorkflowStarter,
)
from creative_marketer.infrastructure.temporal.configuration import ASSEMBLY_TASK_QUEUE
from creative_marketer.infrastructure.temporal.worker import connect_client
from creative_marketer.infrastructure.temporal.workflows import FinalCreativeAssemblyWorkflow
from creative_marketer.observability.ports import NullTelemetry
from creative_marketer.workflow_orchestration.assembly_bridge import (
    StartFinalCreativeAssemblyWorkflow,
)
from creative_marketer_api.config import Settings


def _workload(settings: Settings) -> AssemblyWorkloadIdentity:
    actor_id = settings.assembly_workload_actor_id
    workload_id = settings.assembly_workload_id
    if settings.app_env in {"development", "test"}:
        actor_id = actor_id or uuid5(NAMESPACE_URL, "creative-marketer:local-assembly-worker")
        workload_id = workload_id or "local-assembly-worker"
    if actor_id is None or workload_id is None:
        raise RuntimeError("deployment-issued assembly workload identity is required")
    return AssemblyWorkloadIdentity(actor_id, workload_id, settings.app_env)


async def _bridge_loop(settings: Settings, handler: StartFinalCreativeAssemblyWorkflow) -> None:
    if settings.event_publisher_database_url is None:
        raise RuntimeError("EVENT_PUBLISHER_DATABASE_URL is required by the Assembly bridge")
    publisher = PostgresPublisherStore(
        create_session_factory(str(settings.event_publisher_database_url))
    )
    consumer = ProcessEvent(
        EventContractRegistry(),
        ConsumerRegistry(
            (
                ConsumerRegistration(
                    "assembly-temporal-starter",
                    frozenset({"assembly.plan.created.v1"}),
                    "v1",
                    handler,
                ),
            )
        ),
        SqlAlchemyConsumerUnitOfWorkFactory(create_session_factory(str(settings.database_url))),
    )
    worker_id = uuid4()
    while True:
        claimed = await publisher.claim_ready_types(
            worker_id,
            event_types=("assembly.plan.created.v1",),
            batch_size=25,
            now=datetime.now(UTC),
            lease_duration=timedelta(seconds=30),
        )
        for item in claimed:
            try:
                await consumer("assembly-temporal-starter", item.event, item.trace_context)
            except Exception as error:
                digest = "sha256:" + hashlib.sha256(type(error).__name__.encode()).hexdigest()
                await publisher.mark_retryable(
                    item,
                    next_attempt_at=datetime.now(UTC) + timedelta(seconds=5),
                    error_code="ASSEMBLY_BRIDGE_UNAVAILABLE",
                    error_digest=digest,
                    now=datetime.now(UTC),
                )
            else:
                await publisher.mark_published(item, now=datetime.now(UTC))
        await asyncio.sleep(0.5 if claimed else 2)


async def run() -> None:
    settings = Settings()
    if settings.object_storage_backend != "s3":
        raise RuntimeError("Assembly worker requires private S3-compatible object storage")
    renderer = FFmpegAssemblyRenderer()
    await renderer.version()
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
    executor = AssemblyJobExecutor(
        sessions,
        object_store,
        AssetService(SqlAlchemyCatalogUnitOfWorkFactory(sessions), object_store),
        renderer,
        _workload(settings),
    )
    client = await connect_client(
        os.getenv("TEMPORAL_ADDRESS", "localhost:7233"),
        namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
    )
    activities = TemporalActivities(None, None, NullTelemetry(), assembly_jobs=executor)  # type: ignore[arg-type]
    handler = StartFinalCreativeAssemblyWorkflow(
        TemporalFinalCreativeAssemblyWorkflowStarter(client, ASSEMBLY_TASK_QUEUE)
    )
    async with Worker(
        client,
        task_queue=ASSEMBLY_TASK_QUEUE,
        workflows=[FinalCreativeAssemblyWorkflow],
        activities=[activities.assemble_final_creative],
    ):
        await _bridge_loop(settings, handler)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
