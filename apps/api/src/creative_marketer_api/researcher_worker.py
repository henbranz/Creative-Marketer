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
    initial_supervisor_route,
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
from creative_marketer_api.fake_demo_outputs import (
    creative_output,
    intelligence_output,
    production_output,
)


class DatabaseAgentTypeResolver:
    def __init__(self, factory: SqlAlchemyAgentRuntimeUnitOfWorkFactory) -> None:
        self._factory = factory

    async def agent_type(self, tenant_id: UUID, run_id: UUID) -> str | None:
        async with self._factory(tenant_id) as uow:
            run = await uow.runs.get(run_id)
            return run.agent_type if run else None


def _fake_demo_result(invocation: object) -> ModelInvocationResult:
    """Zero-network structured outputs for every Agent supported by the local worker."""
    contract = invocation.output_contract_key  # type: ignore[attr-defined]
    if contract == "research.research_snapshot":
        evidence = invocation.untrusted_evidence[0]  # type: ignore[attr-defined]
        output = {
            "findings": [
                {
                    "key": "audience_language",
                    "category": "audience",
                    "statement": "Commuters value durable reusable products.",
                    "confidence": "HIGH",
                    "basis": "OBSERVED",
                    "citations": [
                        {
                            "evidence_snapshot_id": str(evidence.evidence_snapshot_id),
                            "block_index": evidence.block_index,
                            "block_digest": evidence.block_digest,
                        }
                    ],
                    "scope": "LOCAL DEMO evidence",
                    "implication": "Show durability in a daily routine.",
                }
            ],
            "research_gaps": ["LOCAL DEMO has intentionally bounded evidence."],
            "recommended_next_sources": [],
        }
    elif contract == "creative.creative_concept_set":
        output = creative_output(invocation)
    elif contract == "production.production_plan":
        output = production_output(invocation)
    elif contract == "intelligence.intelligence_report":
        output = intelligence_output(invocation)
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
        action_proposals: list[dict[str, object]] = []
        first_inventory = next((item for item in inventories if isinstance(item, dict)), None)
        if first_inventory is not None and first_inventory.get("external_variant_id"):
            action_proposals.append(
                {
                    "action_type": "INVENTORY_ADJUSTMENT",
                    "external_variant_id": str(first_inventory["external_variant_id"]),
                    "exact_quantity": 8,
                    "reason": "Restore the reviewed local-demo inventory buffer.",
                }
            )
            action_proposals.append(
                {
                    "action_type": "INVENTORY_ADJUSTMENT",
                    "external_variant_id": str(first_inventory["external_variant_id"]),
                    "exact_quantity": 6,
                    "reason": "Alternative local-demo buffer for rejection-path validation.",
                }
            )
        paid_order = next(
            (
                item
                for item in orders
                if isinstance(item, dict)
                and item.get("payment_state") == "PAID"
                and item.get("external_order_id")
                and item.get("currency")
            ),
            None,
        )
        if paid_order is not None:
            action_proposals.append(
                {
                    "action_type": "REFUND",
                    "external_order_id": str(paid_order["external_order_id"]),
                    "exact_amount": "10.00",
                    "currency": str(paid_order["currency"]),
                    "reason": "Issue the exact reviewed local-demo partial refund.",
                }
            )
        output = {
            "summary": "Fake Store commerce observations were analyzed without network access.",
            "inventory_exceptions": inventory_exceptions,
            "order_exceptions": order_exceptions,
            "action_proposals": action_proposals,
            "limitations": [
                "Synthetic Fake Store analysis; exact human approval is required for mutations."
            ],
        }
    elif contract == "orchestration.supervisor_report":
        capability_context = invocation.capability_context or {}  # type: ignore[attr-defined]
        cycle = capability_context.get("cycle", {})
        readiness = capability_context.get("readiness", {})
        requirements = readiness.get("requirements", []) if isinstance(readiness, dict) else []
        allowed = readiness.get("allowed_actions", []) if isinstance(readiness, dict) else []
        blockers = [
            str(item["message"])
            for item in requirements
            if isinstance(item, dict) and item.get("state") == "BLOCKED"
        ]
        attention = [
            str(item["message"])
            for item in requirements
            if isinstance(item, dict) and item.get("state") == "WAITING"
        ]
        stage = str(cycle.get("stage", "UNKNOWN")) if isinstance(cycle, dict) else "UNKNOWN"
        output = {
            "summary": f"Creative Cycle is at {stage.replace('_', ' ').title()}.",
            "current_stage_explanation": (
                str(requirements[0]["message"])
                if requirements and isinstance(requirements[0], dict)
                else "The deterministic orchestrator owns the next transition."
            ),
            "blockers": blockers,
            "attention_items": attention,
            "suggested_next_actions": list(allowed) if isinstance(allowed, list) else [],
            "completion_summary": (
                "This learning iteration is complete." if stage == "COMPLETED" else None
            ),
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
        initial_supervisor_route(),
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
