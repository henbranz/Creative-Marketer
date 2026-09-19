"""Dedicated least-privilege Commerce Temporal worker (Fake provider only)."""

from __future__ import annotations

import asyncio
from uuid import NAMESPACE_URL, uuid5

from temporalio.worker import Worker

from creative_marketer.commerce.application import CommerceService
from creative_marketer.commerce.execution import CommerceWorkloadIdentity
from creative_marketer.commerce.gateway_composition import CommerceGatewayFactory
from creative_marketer.commerce.provider import FakeCommerceProvider, MutationDisposition
from creative_marketer.infrastructure.database.commerce_authority import (
    SqlAlchemyCommerceExecutionAuthority,
)
from creative_marketer.infrastructure.database.commerce_uow import (
    SqlAlchemyCommerceUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.commerce_worker import (
    GovernedCommerceActionExecutor,
    SqlAlchemyCommerceSyncExecutor,
    SqlAlchemyTrustedWorkflowToolRequestResolver,
)
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.execution_control_uow import (
    SqlAlchemyIdempotencyUnitOfWorkFactory,
)
from creative_marketer.infrastructure.temporal.activities import TemporalActivities
from creative_marketer.infrastructure.temporal.configuration import COMMERCE_TASK_QUEUE
from creative_marketer.infrastructure.temporal.worker import connect_client
from creative_marketer.infrastructure.temporal.workflows import (
    CommerceActionWorkflow,
    CommerceSyncWorkflow,
)
from creative_marketer.observability.ports import NullTelemetry
from creative_marketer_api.config import Settings


def commerce_workload(settings: Settings) -> CommerceWorkloadIdentity:
    actor_id = settings.commerce_workload_actor_id
    workload_id = settings.commerce_workload_id
    if settings.app_env in {"development", "test"}:
        actor_id = actor_id or uuid5(NAMESPACE_URL, "creative-marketer:local-commerce-worker")
        workload_id = workload_id or "local-commerce-worker"
    if (
        actor_id is None
        or workload_id is None
        or (
            settings.app_env in {"staging", "production"}
            and (
                actor_id.int == 0
                or workload_id.startswith(("local-", "placeholder", "replace-", "example-"))
            )
        )
    ):
        raise RuntimeError("deployment-issued Commerce workload identity is required")
    return CommerceWorkloadIdentity(actor_id, workload_id, settings.app_env)


async def run() -> None:
    settings = Settings()
    workload = commerce_workload(settings)
    sessions = create_session_factory(str(settings.database_url))
    # The local Fake Store deliberately reports the first refund as uncertain so the
    # end-to-end demo exercises read-only reconciliation without a duplicate submit.
    provider = FakeCommerceProvider(next_refund_disposition=MutationDisposition.OUTCOME_UNKNOWN)
    authority = SqlAlchemyCommerceExecutionAuthority(sessions, workload)
    gateway = await CommerceGatewayFactory(sessions, authority, provider)()
    resolver = SqlAlchemyTrustedWorkflowToolRequestResolver(sessions)
    actions = GovernedCommerceActionExecutor(
        gateway, resolver, SqlAlchemyIdempotencyUnitOfWorkFactory(sessions)
    )
    sync = SqlAlchemyCommerceSyncExecutor(
        sessions,
        CommerceService(SqlAlchemyCommerceUnitOfWorkFactory(sessions), provider),
        provider,
        settings.app_env,
    )
    activities = TemporalActivities(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        NullTelemetry(),
        commerce_sync=sync,
        commerce_actions=actions,
    )
    client = await connect_client(settings.temporal_address, namespace=settings.temporal_namespace)
    async with Worker(
        client,
        task_queue=COMMERCE_TASK_QUEUE,
        workflows=[CommerceSyncWorkflow, CommerceActionWorkflow],
        activities=[
            activities.sync_commerce,
            activities.submit_commerce_action,
            activities.reconcile_commerce_action,
        ],
    ):
        await asyncio.Future()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
