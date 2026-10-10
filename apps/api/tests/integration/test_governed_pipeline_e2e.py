# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from creative_marketer.agent_governance.application import ResolveActiveAgentVersion
from creative_marketer.agent_runtime.application import (
    AgentRunService,
    ModelProviderRegistry,
    ModelRouter,
    initial_creative_strategist_route,
    initial_researcher_route,
)
from creative_marketer.agent_runtime.domain import ModelInvocationResult, ModelUsage
from creative_marketer.assembly.application import AssemblyService
from creative_marketer.assembly.domain import FinalCreativeDecisionState
from creative_marketer.assembly.execution import AssemblyJobExecutor, AssemblyWorkloadIdentity
from creative_marketer.assembly.rendering import MaterializedSource, RenderResult
from creative_marketer.catalog.asset_application import AssetService
from creative_marketer.catalog.asset_domain import AssetKind, AssetRole, AssetStatus
from creative_marketer.creative.application import CreativeService
from creative_marketer.creative.domain import CreativeDecisionState
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import EventContractError
from creative_marketer.infrastructure.database.agent_governance_uow import (
    SqlAlchemyAgentRegistryUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.agent_runtime_uow import (
    SqlAlchemyAgentRuntimeUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.assembly_uow import (
    SqlAlchemyAssemblyUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.catalog_uow import (
    SqlAlchemyCatalogUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.creative_uow import (
    SqlAlchemyCreativeUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.event_delivery_schema import outbox_events
from creative_marketer.infrastructure.database.orchestration_uow import (
    SqlAlchemyOrchestrationUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.permission_governance_uow import (
    SqlAlchemyPermissionUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.production_authority import (
    MediaWorkloadIdentity,
    SqlAlchemyGenerationAuthority,
)
from creative_marketer.infrastructure.database.production_schema import (
    generation_jobs,
    media_budget_usage,
)
from creative_marketer.infrastructure.database.production_uow import (
    SqlAlchemyProductionUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.tool_execution_schema import tool_calls
from creative_marketer.infrastructure.database.tool_execution_uow import (
    SqlAlchemyGatewayUnitOfWorkFactory,
)
from creative_marketer.infrastructure.database.tool_governance_uow import (
    SqlAlchemyToolRegistryUnitOfWorkFactory,
)
from creative_marketer.infrastructure.model_providers.fake import FakeModelProvider
from creative_marketer.infrastructure.object_storage.s3 import S3ObjectStore
from creative_marketer.orchestration.application import PipelineStateService
from creative_marketer.orchestration.execution import (
    PipelineActionExecutor,
    pipeline_approval_phrase,
)
from creative_marketer.orchestration.pipeline import PipelineAction, PipelineLocator
from creative_marketer.production.application import (
    initial_media_router,
    initial_producer_route,
)
from creative_marketer.production.domain import (
    GenerationJobStatus,
    ProductionPlanDecisionState,
)
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
)
from creative_marketer.production.infrastructure.fakes import DEMO_MP4
from creative_marketer.production.service import ProductionService
from creative_marketer.tool_execution.application import ToolExecutionBinding
from creative_marketer.tool_governance.application import ResolveActiveTool
from scripts import bootstrap_demo
from tests.integration.test_asset_library import (
    OBJECT_STORAGE_ACCESS_KEY_ID,
    OBJECT_STORAGE_SECRET_ACCESS_KEY,
)


@dataclass(slots=True)
class _Renderer:
    calls: int = 0

    async def source_has_audio(self, path: str, workspace: str) -> bool:
        assert Path(path).is_relative_to(Path(workspace))
        return False

    async def render(
        self,
        plan,
        sources: tuple[MaterializedSource, ...],
        workspace: str,
    ) -> RenderResult:
        self.calls += 1
        assert len(sources) == len(plan.items)
        assert all(Path(source.path).is_file() for source in sources)
        output = Path(workspace) / "governed-final.mp4"
        output.write_bytes(DEMO_MP4)
        return RenderResult(
            str(output),
            "ffmpeg",
            "fixture-7.1",
            plan.timeline_duration_ms,
            1080,
            1920,
            24,
            False,
            len(DEMO_MP4),
            "sha256:" + sha256(DEMO_MP4).hexdigest(),
        )


def _model_result(invocation) -> ModelInvocationResult:
    if invocation.output_contract_key == "research.research_snapshot":
        evidence = invocation.untrusted_evidence[0]
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
                    "scope": "governed E2E evidence",
                    "implication": "Show durability in a daily routine.",
                }
            ],
            "research_gaps": ["The fixture evidence is intentionally bounded."],
            "recommended_next_sources": [],
        }
    elif invocation.output_contract_key == "creative.creative_concept_set":
        output = bootstrap_demo._creative_output(invocation)
        requested = int(invocation.capability_context["strategy_request"]["concept_count"])
        concepts = list(cast(list[dict[str, Any]], output["concepts"]))
        while len(concepts) < requested:
            index = len(concepts) + 1
            concept = deepcopy(concepts[(index - 1) % 3])
            concept["concept_key"] = f"governed_e2e_{index}"
            concept["title"] = f"Governed E2E concept {index}"
            concept["creative_angle"] = f"Daily durability proof variation {index}"
            concept["hook"]["spoken_or_voiceover"] = f"Built for routine number {index}."
            concept["hook"]["visual_open"] = f"Distinct bottle reveal variation {index}."
            concept["scenes"][0]["visual_direction"] = (
                f"Distinct governed tabletop reveal variation {index}."
            )
            concepts.append(concept)
        output["concepts"] = concepts
    else:
        output = bootstrap_demo._production_output(invocation)
    return ModelInvocationResult(
        output,
        f"governed-e2e-{invocation.output_contract_key}",
        ModelUsage(100, 100, 200),
        "openai",
        "gpt-5.6-sol",
    )


@dataclass(slots=True)
class _ChangedAuthorityOutputs:
    research_attempts: int = 0
    producer_attempts: int = 0

    def __call__(self, invocation) -> ModelInvocationResult:
        result = _model_result(invocation)
        if invocation.output_contract_key == "research.research_snapshot":
            self.research_attempts += 1
            if self.research_attempts > 1:
                output = deepcopy(dict(result.output))
                findings = cast(list[dict[str, Any]], output["findings"])
                findings[0]["key"] = "changed_market_authority"
                findings[0]["category"] = "positioning"
                findings[0]["statement"] = (
                    "The current market evidence materially changes the recommended message."
                )
                findings[0]["implication"] = "Create a new strategy from current authority."
                return replace(
                    result,
                    output=output,
                    provider_response_id=f"governed-e2e-research-{self.research_attempts}",
                )
        if invocation.output_contract_key == "production.production_plan":
            self.producer_attempts += 1
            if self.producer_attempts == 1:
                return replace(
                    result,
                    output={"schema_version": 3, "invalid_fixture": True},
                    provider_response_id="governed-e2e-historical-producer-failure",
                )
        return result


async def _media_executor(sessions, store) -> GovernedProductionJobExecutor:
    authority = SqlAlchemyGenerationAuthority(
        sessions,
        store,
        MediaWorkloadIdentity(uuid4(), "governed-e2e-media-worker", "development"),
    )
    assets = AssetService(SqlAlchemyCatalogUnitOfWorkFactory(sessions), store)
    image_importer = ApplicationGeneratedAssetImporter(assets, sessions, local_demo=True)
    video_importer = ApplicationGeneratedAssetImporter(assets, sessions, local_demo=True)
    image_provider = FakeImageProvider()
    video_provider = FakeSeedanceMediaProvider()
    tool_resolver = ResolveActiveTool(SqlAlchemyToolRegistryUnitOfWorkFactory(sessions))
    resource_resolver = GenerationJobResourceResolver(authority)
    executors = {
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
    for tool_key, tool_executor in executors.items():
        tool = await tool_resolver(tool_key)
        bindings.append(
            ToolExecutionBinding(
                tool.definition_id,
                tool.version_id,
                normalize_generation_input,
                resource_resolver,
                tool_executor,
                credential_capable=True,
            )
        )
    gateway = compose_production_gateway(
        ResolveActiveAgentVersion(SqlAlchemyAgentRegistryUnitOfWorkFactory(sessions)),
        tool_resolver,
        SqlAlchemyPermissionUnitOfWorkFactory(sessions),
        tuple(bindings),
        SqlAlchemyGatewayUnitOfWorkFactory(sessions),
    )
    return GovernedProductionJobExecutor(authority, gateway)


async def _complete_approved_concept_to_final(
    *,
    context,
    concept_id,
    locator,
    runtime,
    state,
    executor,
    production,
    assembly,
    sessions,
    store,
    exercise_spend_cap_recovery=False,
    monkeypatch=None,
    inspection_sessions=None,
):
    producer_action = await state.resolve_next_action(context, locator)
    assert producer_action.action is PipelineAction.RUN_PRODUCER
    assert producer_action.concept_id == concept_id

    admitted = await executor.execute(
        context,
        locator,
        expected_action=PipelineAction.RUN_PRODUCER,
        explicit_approval=pipeline_approval_phrase(PipelineAction.RUN_PRODUCER),
    )
    locator = admitted.locator
    producer_run = await runtime.get_run(context, admitted.resource_id)
    assert producer_run.status.value == "PENDING"
    assert producer_run.recovery_of_run_id is None
    await runtime.execute(context.tenant_id, producer_run.id)
    review = await state.resolve_next_action(context, locator)
    assert review.action is PipelineAction.REVIEW_PRODUCTION_PLAN
    assert review.production_plan_id is not None
    locator = replace(
        locator,
        concept_id=concept_id,
        production_plan_id=review.production_plan_id,
    )
    await production.decide(
        context,
        review.production_plan_id,
        ProductionPlanDecisionState.APPROVED_FOR_GENERATION,
    )
    media = await state.resolve_next_action(context, locator)
    assert media.action is PipelineAction.WAIT_FOR_MEDIA

    if exercise_spend_cap_recovery:
        assert monkeypatch is not None
        assert inspection_sessions is not None
        original_jobs = await production.list_jobs(context, review.production_plan_id)
        original_ids = tuple(job.id for job in original_jobs)
        original_reservations = tuple(job.reserved_cost for job in original_jobs)
        async with inspection_sessions.begin() as session:
            original_tool_call_count = await session.scalar(
                select(func.count())
                .select_from(tool_calls)
                .where(tool_calls.c.tenant_id == context.tenant_id)
            )
        async with inspection_sessions.begin() as session:
            await session.execute(
                update(generation_jobs)
                .where(generation_jobs.c.production_plan_id == review.production_plan_id)
                .values(
                    status=GenerationJobStatus.BLOCKED_SPEND_CAP.value,
                    failure_code="LIVE_E2E_SPEND_CAP_REACHED",
                )
            )
        production.live_spend_cap = Decimal("100")
        transition_id = uuid4()
        validate_event = EventContractRegistry.validate_event

        def reject_retry_event(registry, candidate):
            if candidate.event_type == "production.media.retry_requested.v1":
                raise EventContractError("forced retry event validation failure")
            return validate_event(registry, candidate)

        monkeypatch.setattr(EventContractRegistry, "validate_event", reject_retry_event)
        with pytest.raises(EventContractError, match="forced retry event validation failure"):
            await production.retry_after_spend_cap_increase(
                context,
                review.production_plan_id,
                transition_id=transition_id,
            )
        monkeypatch.setattr(EventContractRegistry, "validate_event", validate_event)

        rolled_back = await production.list_jobs(context, review.production_plan_id)
        assert tuple(job.id for job in rolled_back) == original_ids
        assert all(job.status is GenerationJobStatus.BLOCKED_SPEND_CAP for job in rolled_back)
        assert all(job.failure_code == "LIVE_E2E_SPEND_CAP_REACHED" for job in rolled_back)
        assert all(job.provider_operation_ref is None for job in rolled_back)
        assert all(job.actual_cost == job.unknown_cost == 0 for job in rolled_back)
        async with inspection_sessions.begin() as session:
            retry_event_count = await session.scalar(
                select(func.count())
                .select_from(outbox_events)
                .where(
                    outbox_events.c.aggregate_id == review.production_plan_id,
                    outbox_events.c.event_type == "production.media.retry_requested.v1",
                )
            )
            reservation_count = await session.scalar(
                select(func.count())
                .select_from(media_budget_usage)
                .where(
                    media_budget_usage.c.generation_job_id.in_(original_ids),
                    media_budget_usage.c.entry_kind == "RESERVED",
                )
            )
            tool_call_count = await session.scalar(
                select(func.count())
                .select_from(tool_calls)
                .where(tool_calls.c.tenant_id == context.tenant_id)
            )
        assert retry_event_count == 0
        assert reservation_count == len(original_ids)
        assert tool_call_count == original_tool_call_count

        requeued = await production.retry_after_spend_cap_increase(
            context,
            review.production_plan_id,
            transition_id=transition_id,
        )
        assert tuple(job.id for job in requeued) == original_ids
        assert tuple(job.reserved_cost for job in requeued) == original_reservations
        assert all(job.provider_operation_ref is None for job in requeued)
        after_retry = await state.resolve_next_action(context, locator)
        assert after_retry.action is PipelineAction.WAIT_FOR_MEDIA
        async with inspection_sessions.begin() as session:
            retry_event_count = await session.scalar(
                select(func.count())
                .select_from(outbox_events)
                .where(
                    outbox_events.c.aggregate_id == review.production_plan_id,
                    outbox_events.c.event_type == "production.media.retry_requested.v1",
                )
            )
            reservation_count = await session.scalar(
                select(func.count())
                .select_from(media_budget_usage)
                .where(
                    media_budget_usage.c.generation_job_id.in_(original_ids),
                    media_budget_usage.c.entry_kind == "RESERVED",
                )
            )
            tool_call_count = await session.scalar(
                select(func.count())
                .select_from(tool_calls)
                .where(tool_calls.c.tenant_id == context.tenant_id)
            )
        assert retry_event_count == 1
        assert reservation_count == len(original_ids)
        assert tool_call_count == original_tool_call_count

    jobs = await production.list_jobs(context, review.production_plan_id)
    media_executor = await _media_executor(sessions, store)
    for job in jobs:
        for _ in range(6):
            result = await media_executor.execute(
                context.tenant_id, review.production_plan_id, job.id
            )
            if result.status in {
                GenerationJobStatus.SUCCEEDED.value,
                GenerationJobStatus.FAILED.value,
                GenerationJobStatus.OUTCOME_UNKNOWN.value,
            }:
                break
        assert result.status == GenerationJobStatus.SUCCEEDED.value

    manual_gate = await state.resolve_next_action(context, locator)
    assert manual_gate.action is PipelineAction.BIND_MANUAL_ASSEMBLY_INPUT
    assembly_input_factory = SqlAlchemyAssemblyUnitOfWorkFactory(sessions)
    async with assembly_input_factory(context.tenant_id) as uow:
        source = await uow.assembly.production_input(review.production_plan_id)
    assert source is not None
    manual_shot = next(
        shot
        for scene in source.scenes
        for shot in scene.shots
        if shot.source_strategy == "MANUAL_CAPTURE"
    )
    videos = await AssetService(SqlAlchemyCatalogUnitOfWorkFactory(sessions), store).list(
        context,
        product_id=bootstrap_demo.PRODUCT_ID,
        kind=AssetKind.VIDEO,
        status=AssetStatus.READY,
    )
    manual_video = next(value for value in videos if value.role is AssetRole.PRODUCTION_REFERENCE)
    await assembly.bind_manual_source(context, manual_shot.id, manual_video.id)

    ready = await state.resolve_next_action(context, locator)
    assert ready.action is PipelineAction.CREATE_ASSEMBLY_PLAN
    assembled = await executor.execute(
        context,
        locator,
        expected_action=PipelineAction.CREATE_ASSEMBLY_PLAN,
    )
    locator = assembled.locator
    assert (
        await state.resolve_next_action(context, locator)
    ).action is PipelineAction.WAIT_FOR_ASSEMBLY

    record = await assembly.get_plan(context, assembled.resource_id)
    renderer = _Renderer()
    final = await AssemblyJobExecutor(
        sessions,
        store,
        AssetService(SqlAlchemyCatalogUnitOfWorkFactory(sessions), store),
        renderer,
        AssemblyWorkloadIdentity(uuid4(), "governed-e2e-assembly", "test"),
    ).execute(context.tenant_id, record.job.id)
    review_final = await state.resolve_next_action(context, locator)
    assert review_final.action is PipelineAction.REVIEW_FINAL_CREATIVE
    assert review_final.final_creative_id == final.id
    assert final.output_asset_id is not None

    await assembly.decide_final(
        context,
        final.id,
        FinalCreativeDecisionState.APPROVED_FOR_PUBLISHING,
    )
    complete = await state.resolve_next_action(context, locator)
    assert complete.action is PipelineAction.FINAL_CREATIVE_READY
    stored = await assembly.get_final(context, final.id)
    assert stored.final_creative.output_asset_id == final.output_asset_id
    assert stored.decision is not None
    assert renderer.calls == 1
    return producer_run, final


@pytest.mark.postgres
@pytest.mark.object_storage
@pytest.mark.asyncio
async def test_database_backed_governed_pipeline_reaches_persisted_final_creative(
    monkeypatch,
    admin_engine,
    admin_database_url: str,
    runtime_database_url: str,
) -> None:
    """Traverse the canonical executor and real persistence from Product to final approval."""

    del admin_engine
    endpoint = __import__("os").environ["TEST_OBJECT_STORAGE_URL"]
    for key, value in {
        "APP_ENV": "development",
        "DATABASE_URL": admin_database_url,
        "OBJECT_STORAGE_BACKEND": "s3",
        "OBJECT_STORAGE_ENDPOINT_URL": endpoint,
        "OBJECT_STORAGE_PUBLIC_ENDPOINT_URL": endpoint,
        "OBJECT_STORAGE_ACCESS_KEY_ID": OBJECT_STORAGE_ACCESS_KEY_ID,
        "OBJECT_STORAGE_SECRET_ACCESS_KEY": OBJECT_STORAGE_SECRET_ACCESS_KEY,
        "CORS_ORIGINS": '["http://localhost:3000"]',
        "MODEL_PROVIDER_BACKEND": "disabled",
        "MEDIA_IMAGE_PROVIDER": "fake",
        "MEDIA_VIDEO_PROVIDER": "fake",
    }.items():
        monkeypatch.setenv(key, value)
    await bootstrap_demo.run()

    sessions = create_session_factory(runtime_database_url)
    context = bootstrap_demo._context()
    store = S3ObjectStore(
        endpoint_url=endpoint,
        public_endpoint_url=endpoint,
        region="us-east-1",
        bucket="creative-marketer-assets",
        access_key_id=OBJECT_STORAGE_ACCESS_KEY_ID,
        secret_access_key=OBJECT_STORAGE_SECRET_ACCESS_KEY,
    )
    runtime = AgentRunService(
        SqlAlchemyAgentRuntimeUnitOfWorkFactory(sessions),
        ModelRouter(
            (
                initial_researcher_route(),
                initial_creative_strategist_route(),
                initial_producer_route(),
            )
        ),
        ModelProviderRegistry({"openai": FakeModelProvider(_model_result)}),
        bootstrap_demo.DemoWorkload(),
    )
    creative = CreativeService(SqlAlchemyCreativeUnitOfWorkFactory(sessions))
    production = ProductionService(
        SqlAlchemyProductionUnitOfWorkFactory(sessions), initial_media_router()
    )
    assembly = AssemblyService(SqlAlchemyAssemblyUnitOfWorkFactory(sessions))
    state = PipelineStateService(SqlAlchemyOrchestrationUnitOfWorkFactory(sessions))
    executor = PipelineActionExecutor(state, runtime, creative, production, assembly)
    locator = PipelineLocator(bootstrap_demo.PRODUCT_ID, use_latest_when_unbound=False)

    assert (await state.resolve_next_action(context, locator)).action is PipelineAction.RUN_RESEARCH
    admitted = await executor.execute(
        context,
        locator,
        expected_action=PipelineAction.RUN_RESEARCH,
        explicit_approval=pipeline_approval_phrase(PipelineAction.RUN_RESEARCH),
    )
    locator = admitted.locator
    research = await runtime.get_run(context, admitted.resource_id)
    assert research.status.value == "PENDING"
    await runtime.execute(context.tenant_id, research.id)

    assert (await state.resolve_next_action(context, locator)).action is PipelineAction.RUN_CREATIVE
    admitted = await executor.execute(
        context,
        locator,
        expected_action=PipelineAction.RUN_CREATIVE,
        explicit_approval=pipeline_approval_phrase(PipelineAction.RUN_CREATIVE),
    )
    locator = admitted.locator
    creative_run = await runtime.get_run(context, admitted.resource_id)
    assert creative_run.status.value == "PENDING"
    await runtime.execute(context.tenant_id, creative_run.id)
    assert (
        await state.resolve_next_action(context, locator)
    ).action is PipelineAction.APPROVE_CREATIVE

    concept_set = next(
        value
        for value in await creative.list_sets(context, bootstrap_demo.PRODUCT_ID)
        if value.agent_run_id == creative_run.id
    )
    concept = concept_set.concepts[0]
    await creative.decide(
        context,
        concept.id,
        CreativeDecisionState.APPROVED_FOR_PRODUCTION,
        note="Governed offline E2E approval",
    )
    await _complete_approved_concept_to_final(
        context=context,
        concept_id=concept.id,
        locator=locator,
        runtime=runtime,
        state=state,
        executor=executor,
        production=production,
        assembly=assembly,
        sessions=sessions,
        store=store,
        exercise_spend_cap_recovery=True,
        monkeypatch=monkeypatch,
        inspection_sessions=create_session_factory(admin_database_url),
    )


@pytest.mark.postgres
@pytest.mark.object_storage
@pytest.mark.asyncio
async def test_changed_authority_restrategy_starts_new_lineage_and_reaches_final_creative(
    monkeypatch,
    admin_engine,
    admin_database_url: str,
    runtime_database_url: str,
) -> None:
    """Regress the live REQUIRES_RESTRATEGY branch without mutating live state."""

    del admin_engine
    endpoint = __import__("os").environ["TEST_OBJECT_STORAGE_URL"]
    for key, value in {
        "APP_ENV": "development",
        "DATABASE_URL": admin_database_url,
        "OBJECT_STORAGE_BACKEND": "s3",
        "OBJECT_STORAGE_ENDPOINT_URL": endpoint,
        "OBJECT_STORAGE_PUBLIC_ENDPOINT_URL": endpoint,
        "OBJECT_STORAGE_ACCESS_KEY_ID": OBJECT_STORAGE_ACCESS_KEY_ID,
        "OBJECT_STORAGE_SECRET_ACCESS_KEY": OBJECT_STORAGE_SECRET_ACCESS_KEY,
        "CORS_ORIGINS": '["http://localhost:3000"]',
        "MODEL_PROVIDER_BACKEND": "disabled",
        "MEDIA_IMAGE_PROVIDER": "fake",
        "MEDIA_VIDEO_PROVIDER": "fake",
    }.items():
        monkeypatch.setenv(key, value)
    await bootstrap_demo.run()

    sessions = create_session_factory(runtime_database_url)
    context = bootstrap_demo._context()
    store = S3ObjectStore(
        endpoint_url=endpoint,
        public_endpoint_url=endpoint,
        region="us-east-1",
        bucket="creative-marketer-assets",
        access_key_id=OBJECT_STORAGE_ACCESS_KEY_ID,
        secret_access_key=OBJECT_STORAGE_SECRET_ACCESS_KEY,
    )
    outputs = _ChangedAuthorityOutputs()
    runtime = AgentRunService(
        SqlAlchemyAgentRuntimeUnitOfWorkFactory(sessions),
        ModelRouter(
            (
                initial_researcher_route(),
                initial_creative_strategist_route(),
                initial_producer_route(),
            )
        ),
        ModelProviderRegistry({"openai": FakeModelProvider(outputs)}),
        bootstrap_demo.DemoWorkload(),
    )
    creative = CreativeService(SqlAlchemyCreativeUnitOfWorkFactory(sessions))
    production = ProductionService(
        SqlAlchemyProductionUnitOfWorkFactory(sessions), initial_media_router()
    )
    assembly = AssemblyService(SqlAlchemyAssemblyUnitOfWorkFactory(sessions))
    state = PipelineStateService(SqlAlchemyOrchestrationUnitOfWorkFactory(sessions))
    executor = PipelineActionExecutor(state, runtime, creative, production, assembly)
    locator = PipelineLocator(bootstrap_demo.PRODUCT_ID, use_latest_when_unbound=False)

    admitted_research = await executor.execute(
        context,
        locator,
        expected_action=PipelineAction.RUN_RESEARCH,
        explicit_approval=pipeline_approval_phrase(PipelineAction.RUN_RESEARCH),
    )
    locator = admitted_research.locator
    await runtime.execute(context.tenant_id, admitted_research.resource_id)

    admitted_creative = await executor.execute(
        context,
        locator,
        expected_action=PipelineAction.RUN_CREATIVE,
        explicit_approval=pipeline_approval_phrase(PipelineAction.RUN_CREATIVE),
    )
    locator = admitted_creative.locator
    await runtime.execute(context.tenant_id, admitted_creative.resource_id)
    historical_creative = await runtime.get_run(context, admitted_creative.resource_id)
    historical_set = next(
        value
        for value in await creative.list_sets(context, bootstrap_demo.PRODUCT_ID)
        if value.agent_run_id == historical_creative.id
    )
    historical_concept = historical_set.concepts[0]
    await creative.decide(
        context,
        historical_concept.id,
        CreativeDecisionState.APPROVED_FOR_PRODUCTION,
        note="Historical concept retained for changed-authority regression",
    )

    historical_producer_admission = await executor.execute(
        context,
        locator,
        expected_action=PipelineAction.RUN_PRODUCER,
        explicit_approval=pipeline_approval_phrase(PipelineAction.RUN_PRODUCER),
    )
    locator = historical_producer_admission.locator
    historical_producer = await runtime.execute(
        context.tenant_id, historical_producer_admission.resource_id
    )
    assert historical_producer.status.value == "FAILED"
    assert historical_producer.recovery_of_run_id is None
    historical_producer_frozen = await runtime.get_run(context, historical_producer.id)

    current_research = await runtime.request_researcher(
        context,
        product_id=bootstrap_demo.PRODUCT_ID,
        idempotency_key="governed-e2e-current-research-refresh",
    )
    await runtime.execute(context.tenant_id, current_research.id)
    locator = replace(locator, research_run_id=current_research.id)
    revalidation_required = await state.resolve_next_action(context, locator)
    assert revalidation_required.action is PipelineAction.REVALIDATE_CREATIVE

    revalidated = await executor.execute(
        context,
        locator,
        expected_action=PipelineAction.REVALIDATE_CREATIVE,
    )
    locator = revalidated.locator
    restrategy_required = await state.resolve_next_action(context, locator)
    assert restrategy_required.action is PipelineAction.RESTRATEGIZE_CREATIVE
    assert restrategy_required.concept_id == historical_concept.id

    restrategy = await executor.execute(
        context,
        locator,
        expected_action=PipelineAction.RESTRATEGIZE_CREATIVE,
        explicit_approval=pipeline_approval_phrase(PipelineAction.RESTRATEGIZE_CREATIVE),
    )
    locator = restrategy.locator
    assert locator.concept_id is None
    assert locator.creative_revalidation_id is None
    assert locator.producer_run_id is None
    assert locator.production_plan_id is None
    new_creative_run = await runtime.get_run(context, restrategy.resource_id)
    assert new_creative_run.status.value == "PENDING"
    assert new_creative_run.recovery_of_run_id is None
    await runtime.execute(context.tenant_id, new_creative_run.id)

    assert (await runtime.get_run(context, historical_creative.id)) == historical_creative
    assert (await runtime.get_run(context, historical_producer.id)) == historical_producer_frozen
    new_set = next(
        value
        for value in await creative.list_sets(context, bootstrap_demo.PRODUCT_ID)
        if value.agent_run_id == new_creative_run.id
    )
    new_concept = new_set.concepts[0]
    assert new_concept.id != historical_concept.id
    await creative.decide(
        context,
        new_concept.id,
        CreativeDecisionState.APPROVED_FOR_PRODUCTION,
        note="Approve the new current-authority concept",
    )

    new_producer, final = await _complete_approved_concept_to_final(
        context=context,
        concept_id=new_concept.id,
        locator=locator,
        runtime=runtime,
        state=state,
        executor=executor,
        production=production,
        assembly=assembly,
        sessions=sessions,
        store=store,
    )
    assert new_producer.id != historical_producer.id
    assert new_producer.recovery_of_run_id is None
    assert final.output_asset_id is not None
    assert (await runtime.get_run(context, historical_producer.id)) == historical_producer_frozen
