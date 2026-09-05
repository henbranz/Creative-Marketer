import asyncio
import os
from datetime import timedelta

from temporalio.client import Client, Interceptor
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.worker import Worker

from creative_marketer.infrastructure.temporal.activities import TemporalActivities
from creative_marketer.infrastructure.temporal.configuration import WORKFLOW_TASK_QUEUE
from creative_marketer.infrastructure.temporal.workflows import (
    ApprovalBlockingWorkflow,
    MediaGenerationWorkflow,
    ScheduledPublicationWorkflow,
)


def create_worker(
    client: Client,
    activities: TemporalActivities,
    *,
    task_queue: str = WORKFLOW_TASK_QUEUE,
    graceful_shutdown_timeout: timedelta = timedelta(seconds=30),
    max_cached_workflows: int = 1000,
) -> Worker:
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[
            ApprovalBlockingWorkflow,
            MediaGenerationWorkflow,
            ScheduledPublicationWorkflow,
        ],
        activities=[
            activities.invoke_tool,
            activities.start_generation,
            activities.poll_generation,
        ],
        graceful_shutdown_timeout=graceful_shutdown_timeout,
        max_cached_workflows=max_cached_workflows,
    )


async def run_worker(client: Client, activities: TemporalActivities) -> None:
    async with create_worker(client, activities):
        await asyncio.Future()


async def connect_client(
    target: str,
    *,
    namespace: str = "default",
    interceptors: list[Interceptor] | None = None,
) -> Client:
    active_interceptors = interceptors or [TracingInterceptor(always_create_workflow_spans=True)]
    return await Client.connect(target, namespace=namespace, interceptors=active_interceptors)


def main() -> None:
    target = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    raise SystemExit(
        "Temporal server connection is configured for "
        f"{target}/{namespace}, but production worker composition is intentionally blocked until "
        "authenticated workload identity and durable request resolution are implemented. "
        "Run `make temporal-test` for the executable adoption spike."
    )


if __name__ == "__main__":
    main()
