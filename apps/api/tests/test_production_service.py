# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,var-annotated,index"

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from creative_marketer.action_binding import NormalizedToolInput
from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.production.application import initial_media_router, validate_production_plan
from creative_marketer.production.domain import (
    GenerationJobStatus,
    ProductionDecisionConflict,
    ProductionNotFound,
    ProductionPermissionDenied,
    ProductionPlanDecisionState,
    ProductionPricingChanged,
)
from creative_marketer.production.execution import (
    ExecutableGeneration,
    GenerationJobResourceResolver,
    GovernedProductionJobExecutor,
    ImageGenerateToolExecutor,
    VideoImportToolExecutor,
    VideoStartToolExecutor,
    VideoStatusToolExecutor,
    normalize_generation_input,
)
from creative_marketer.production.infrastructure import (
    ApplicationGeneratedAssetImporter,
    FakeImageProvider,
    FakeSeedanceMediaProvider,
)
from creative_marketer.production.media import (
    ImageGenerationRequest,
    InvalidMediaResult,
    MediaProviderError,
    MediaProviderOutcomeUnknown,
    ProviderGenerationState,
    VideoGenerationRequest,
)
from creative_marketer.production.service import ProductionPlanRecord, ProductionService
from creative_marketer.tool_execution.domain import (
    GatewayResult,
    GatewayStatus,
    OutcomeUnknown,
    PreEffectFailure,
    ToolExecutionContext,
)

from .test_production import context as planning_context
from .test_production import valid_output


class Writer:
    def __init__(self) -> None:
        self.values = []

    async def append(self, value) -> None:
        self.values.append(value)


class Repository:
    def __init__(self, record) -> None:
        self.record = record
        self.jobs = ()

    async def get_plan(self, plan_id, *, for_update=False):
        return self.record if plan_id == self.record.plan.id else None

    async def list_plans(self, product_id):
        return (self.record,) if product_id == self.record.plan.product_id else ()

    async def add_decision(self, value):
        self.record = ProductionPlanRecord(self.record.plan, value, self.record.planning_cost)

    async def add_jobs(self, values):
        self.jobs = values

    async def list_jobs(self, plan_id):
        return self.jobs if plan_id == self.record.plan.id else ()

    async def get_job(self, job_id):
        return next((item for item in self.jobs if item.id == job_id), None)


class Uow:
    def __init__(self, repository) -> None:
        self.production = repository
        self.audit = Writer()
        self.outbox = Writer()
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def commit(self):
        self.committed = True


def execution_context(tenant_id, role=MembershipRole.OWNER):
    user = uuid4()
    return ExecutionContext(
        tenant_id,
        Actor(ActorKind.USER, user),
        user,
        role,
        MembershipStatus.ACTIVE,
        "test",
        AuthenticationAssurance(datetime.now(UTC), "test", "mfa"),
        uuid4(),
    )


def service_fixture():
    context = planning_context()
    plan = validate_production_plan(
        valid_output(str(context.selected_assets[0].asset_id)),
        tenant_id=uuid4(),
        product_id=uuid4(),
        agent_run_id=uuid4(),
        context=context,
        concept_scene_keys=("scene_one",),
    )
    repository = Repository(ProductionPlanRecord(plan, None, Decimal("0.42")))
    uow = Uow(repository)
    return ProductionService(lambda _tenant: uow, initial_media_router()), repository, uow


@pytest.mark.asyncio
async def test_approval_binds_cost_routes_creates_jobs_and_is_idempotent() -> None:
    service, repository, uow = service_fixture()
    context = execution_context(repository.record.plan.tenant_id)
    approved = await service.decide(
        context,
        repository.record.plan.id,
        ProductionPlanDecisionState.APPROVED_FOR_GENERATION,
    )
    assert approved.decision is not None
    assert approved.decision.estimated_max_cost == Decimal("5.944900")
    assert [job.kind.value for job in repository.jobs] == ["IMAGE", "VIDEO"]
    assert sum((job.reserved_cost for job in repository.jobs), Decimal(0)) == Decimal("5.944900")
    assert uow.committed and len(uow.audit.values) == len(uow.outbox.values) == 1
    assert (
        await service.decide(
            context,
            repository.record.plan.id,
            ProductionPlanDecisionState.APPROVED_FOR_GENERATION,
        )
        == approved
    )
    with pytest.raises(ProductionDecisionConflict):
        await service.decide(
            context, repository.record.plan.id, ProductionPlanDecisionState.REJECTED
        )


@pytest.mark.asyncio
async def test_member_and_cost_cap_fail_before_jobs() -> None:
    service, repository, _uow = service_fixture()
    with pytest.raises(ProductionPermissionDenied):
        await service.decide(
            execution_context(repository.record.plan.tenant_id, MembershipRole.MEMBER),
            repository.record.plan.id,
            ProductionPlanDecisionState.APPROVED_FOR_GENERATION,
        )
    service.maximum_plan_cost = Decimal("1")
    with pytest.raises(ProductionPermissionDenied):
        await service.decide(
            execution_context(repository.record.plan.tenant_id),
            repository.record.plan.id,
            ProductionPlanDecisionState.APPROVED_FOR_GENERATION,
        )
    assert repository.jobs == ()


@pytest.mark.asyncio
async def test_deterministic_media_fakes_cover_success_failure_and_unknown() -> None:
    image = FakeImageProvider()
    result = await image.generate(ImageGenerationRequest("render", "1024x1024", "medium"))
    assert result.actual_cost == Decimal("0.125") and image.call_count == 1
    unknown = FakeImageProvider(outcome="unknown")
    with pytest.raises(MediaProviderOutcomeUnknown):
        await unknown.generate(ImageGenerationRequest("render", "1024x1024", "medium"))
    for outcome in ("malformed", "oversized"):
        provider = FakeImageProvider(outcome=outcome)
        if outcome == "oversized":
            with pytest.raises(InvalidMediaResult):
                await provider.generate(ImageGenerationRequest("render", "1024x1024", "medium"))
        else:
            assert (
                await provider.generate(ImageGenerationRequest("render", "1024x1024", "medium"))
            ).content == b"invalid"
    video = FakeSeedanceMediaProvider()
    started = await video.start(VideoGenerationRequest("animate", 12, "720p", "9:16", True))
    queued = await video.status(started.provider_operation_ref)
    running = await video.status(started.provider_operation_ref)
    assert queued.state is ProviderGenerationState.QUEUED
    assert running.state is ProviderGenerationState.RUNNING
    complete = await video.status(started.provider_operation_ref)
    assert complete.state is ProviderGenerationState.SUCCEEDED
    assert await video.download(complete.temporary_result_locator or "") == video.content
    ambiguous = FakeSeedanceMediaProvider(start_outcome="unknown")
    with pytest.raises(MediaProviderOutcomeUnknown):
        await ambiguous.start(VideoGenerationRequest("animate", 12, "720p", "9:16", True))
    assert ambiguous.start_count == 1
    with pytest.raises(MediaProviderError):
        await video.status("unknown-task")
    with pytest.raises(InvalidMediaResult):
        await video.download("https://unknown.invalid/video")


class Authority:
    def __init__(self, execution) -> None:
        self.execution = execution
        self.states = []

    async def prepare(self, tenant_id, job_id, kind):
        assert tenant_id == self.execution.job.tenant_id
        assert job_id == self.execution.job.id and kind == self.execution.job.kind
        return self.execution

    async def provider_started(self, job_id, provider_operation_ref):
        self.states.append(("started", provider_operation_ref))

    async def processing(self, job_id):
        self.states.append(("processing", None))

    async def importing(self, job_id):
        self.states.append(("importing", None))

    async def failed(self, job_id, failure_code, actual_cost):
        self.states.append(("failed", failure_code))

    async def outcome_unknown(self, job_id):
        self.states.append(("unknown", None))

    async def succeeded(self, job_id, asset_id, actual_cost):
        self.states.append(("succeeded", asset_id))

    async def authorize_resource(self, tenant_id, job_id):
        if tenant_id != self.execution.job.tenant_id or job_id != self.execution.job.id:
            raise ValueError("wrong tenant")

    async def current_job(self, tenant_id, job_id):
        await self.authorize_resource(tenant_id, job_id)
        return self.execution.job


class Importer:
    def __init__(self) -> None:
        self.asset_id = uuid4()

    async def import_result(self, execution, content, media_type):
        assert content and media_type in {"image/png", "video/mp4"}
        return self.asset_id


def tool_context(tenant_id):
    return ToolExecutionContext(
        tenant_id, uuid4(), "op_" + "a" * 32, uuid4(), uuid4(), uuid4(), uuid4()
    )


@pytest.mark.asyncio
async def test_media_tool_executors_import_and_never_retry_ambiguous_start() -> None:
    service, repository, _uow = service_fixture()
    await service.decide(
        execution_context(repository.record.plan.tenant_id),
        repository.record.plan.id,
        ProductionPlanDecisionState.APPROVED_FOR_GENERATION,
    )
    image_job, video_job = repository.jobs
    image_authority = Authority(
        ExecutableGeneration(
            image_job,
            {"size": "1024x1536", "quality": "high", "subject": "Product"},
            (),
        )
    )
    importer = Importer()
    normalized = NormalizedToolInput.from_trusted_value({"generation_job_id": str(image_job.id)})
    result = await ImageGenerateToolExecutor(
        image_authority, FakeImageProvider(), importer
    ).execute(tool_context(image_job.tenant_id), normalized)
    assert result.result_ref.endswith(str(image_job.id))
    assert [state for state, _ in image_authority.states] == [
        "started",
        "importing",
        "succeeded",
    ]

    provider = FakeSeedanceMediaProvider(start_outcome="unknown")
    video_authority = Authority(
        ExecutableGeneration(
            video_job,
            {
                "resolution": "720p",
                "aspect_ratio": "9:16",
                "generate_audio": True,
            },
            (),
            12,
        )
    )
    normalized = NormalizedToolInput.from_trusted_value({"generation_job_id": str(video_job.id)})
    with pytest.raises(OutcomeUnknown):
        await VideoStartToolExecutor(video_authority, provider).execute(
            tool_context(video_job.tenant_id), normalized
        )
    assert provider.start_count == 1
    assert video_authority.states == [("started", None), ("unknown", None)]


@pytest.mark.asyncio
async def test_media_tool_executor_terminal_and_failure_paths() -> None:
    service, repository, _uow = service_fixture()
    context = execution_context(repository.record.plan.tenant_id)
    await service.decide(
        context,
        repository.record.plan.id,
        ProductionPlanDecisionState.APPROVED_FOR_GENERATION,
    )
    image_job, video_job = repository.jobs
    tool = tool_context(image_job.tenant_id)
    image_input = NormalizedToolInput.from_trusted_value({"generation_job_id": str(image_job.id)})
    for outcome, expected in (("unknown", OutcomeUnknown), ("failure", PreEffectFailure)):
        authority = Authority(ExecutableGeneration(image_job, {}, ()))
        with pytest.raises(expected):
            await ImageGenerateToolExecutor(
                authority, FakeImageProvider(outcome=outcome), Importer()
            ).execute(tool, image_input)
        assert authority.states[-1][0] in {"unknown", "failed"}
    with pytest.raises(ValueError):
        await ImageGenerateToolExecutor(
            Authority(ExecutableGeneration(image_job, {}, ())), FakeImageProvider(), Importer()
        ).execute(tool, NormalizedToolInput.from_trusted_value({"unexpected": "value"}))

    video_input = NormalizedToolInput.from_trusted_value({"generation_job_id": str(video_job.id)})
    video_spec = {"resolution": "720p", "aspect_ratio": "9:16", "generate_audio": True}
    failed_start = Authority(ExecutableGeneration(video_job, video_spec, (), 12))
    with pytest.raises(PreEffectFailure):
        await VideoStartToolExecutor(
            failed_start, FakeSeedanceMediaProvider(start_outcome="failure")
        ).execute(tool_context(video_job.tenant_id), video_input)
    assert failed_start.states[-1][0] == "failed"

    started_authority = Authority(ExecutableGeneration(video_job, video_spec, (), 12))
    started = await VideoStartToolExecutor(started_authority, FakeSeedanceMediaProvider()).execute(
        tool_context(video_job.tenant_id), video_input
    )
    assert started.output["status"] == GenerationJobStatus.PROCESSING.value

    missing_operation = Authority(ExecutableGeneration(video_job, {}, (), 12))
    with pytest.raises(PreEffectFailure):
        await VideoStatusToolExecutor(missing_operation, FakeSeedanceMediaProvider()).execute(
            tool_context(video_job.tenant_id), video_input
        )
    with pytest.raises(PreEffectFailure):
        await VideoImportToolExecutor(
            missing_operation, FakeSeedanceMediaProvider(), Importer()
        ).execute(tool_context(video_job.tenant_id), video_input)

    bound_job = replace(video_job, provider_operation_ref="fake-seedance-task")
    for state, expected_status, expected_write in (
        (ProviderGenerationState.FAILED, GenerationJobStatus.FAILED, "failed"),
        (ProviderGenerationState.SUCCEEDED, GenerationJobStatus.IMPORTING, "importing"),
        (ProviderGenerationState.RUNNING, GenerationJobStatus.PROCESSING, "processing"),
    ):
        authority = Authority(ExecutableGeneration(bound_job, {}, (), 12))
        result = await VideoStatusToolExecutor(
            authority, FakeSeedanceMediaProvider(statuses=[state])
        ).execute(tool_context(video_job.tenant_id), video_input)
        assert result.output["status"] == expected_status.value
        assert authority.states[-1][0] == expected_write

    not_ready = Authority(ExecutableGeneration(bound_job, {}, (), 12))
    with pytest.raises(PreEffectFailure):
        await VideoImportToolExecutor(
            not_ready,
            FakeSeedanceMediaProvider(statuses=[ProviderGenerationState.RUNNING]),
            Importer(),
        ).execute(tool_context(video_job.tenant_id), video_input)
    imported = Authority(ExecutableGeneration(bound_job, {}, (), 12))
    imported_result = await VideoImportToolExecutor(
        imported,
        FakeSeedanceMediaProvider(statuses=[ProviderGenerationState.SUCCEEDED]),
        Importer(),
    ).execute(tool_context(video_job.tenant_id), video_input)
    assert imported_result.output["status"] == GenerationJobStatus.SUCCEEDED.value
    assert imported.states[-1][0] == "succeeded"


class Gateway:
    def __init__(self, authority):
        self.authority = authority
        self.calls = []

    async def invoke(self, invocation, request):
        self.calls.append((invocation, request))
        self.authority.execution = replace(
            self.authority.execution,
            job=replace(
                self.authority.execution.job,
                status=GenerationJobStatus.SUCCEEDED,
                output_asset_id=uuid4(),
            ),
        )
        return GatewayResult(
            GatewayStatus.EXECUTED, request.operation_id, result_ref="result://done"
        )


@pytest.mark.asyncio
async def test_governed_job_dispatch_and_resource_resolution_are_id_only() -> None:
    service, repository, _uow = service_fixture()
    actor = execution_context(repository.record.plan.tenant_id)
    await service.decide(
        actor,
        repository.record.plan.id,
        ProductionPlanDecisionState.APPROVED_FOR_GENERATION,
    )
    image_job, video_job = repository.jobs
    authority = Authority(
        ExecutableGeneration(
            image_job,
            {},
            (),
            initiating_context=actor,
            requested_agent_definition_id=uuid4(),
        )
    )
    gateway = Gateway(authority)
    result = await GovernedProductionJobExecutor(authority, gateway).execute(
        image_job.tenant_id, image_job.production_plan_id, image_job.id
    )
    assert result.status == "SUCCEEDED"
    assert gateway.calls[0][1].tool_key == "media.image.generate"
    normalized = normalize_generation_input({"generation_job_id": str(image_job.id)})
    resource = await GenerationJobResourceResolver(authority)(actor, object(), normalized)
    assert resource.resource_id == str(image_job.id)
    with pytest.raises(Exception, match="outside"):
        await GenerationJobResourceResolver(authority)(
            replace(actor, tenant_id=uuid4()), object(), normalized
        )

    terminal = replace(image_job, status=GenerationJobStatus.FAILED, failure_code="failed")
    authority.execution = replace(authority.execution, job=terminal)
    assert (
        await GovernedProductionJobExecutor(authority, gateway).execute(
            terminal.tenant_id, terminal.production_plan_id, terminal.id
        )
    ).status == "FAILED"
    authority.execution = ExecutableGeneration(video_job, {}, ())
    with pytest.raises(ValueError, match="does not belong"):
        await GovernedProductionJobExecutor(authority, gateway).execute(
            video_job.tenant_id, uuid4(), video_job.id
        )
    with pytest.raises(ValueError, match="lacks authoritative"):
        await GovernedProductionJobExecutor(authority, gateway).execute(
            video_job.tenant_id, video_job.production_plan_id, video_job.id
        )


def test_governed_job_tool_selection_covers_video_lifecycle() -> None:
    _service, repository, _uow = service_fixture()
    plan = repository.record.plan
    image, video = ProductionService._jobs(
        plan,
        initial_media_router().resolve("production_video"),
        initial_media_router().resolve("production_image"),
    )
    executor = GovernedProductionJobExecutor(object(), object())
    assert executor._tool(image) == "media.image.generate"
    assert executor._tool(video) == "media.video.generate.start"
    assert executor._tool(replace(video, status=GenerationJobStatus.PROCESSING)) == (
        "media.video.generate.status"
    )
    assert executor._tool(replace(video, status=GenerationJobStatus.IMPORTING)) == (
        "media.video.generate.import"
    )
    with pytest.raises(ValueError, match="already in progress"):
        executor._tool(replace(image, status=GenerationJobStatus.IMPORTING))
    with pytest.raises(ValueError, match="lifecycle"):
        executor._tool(replace(video, status=GenerationJobStatus.STARTING))
    with pytest.raises(ValueError):
        normalize_generation_input({"generation_job_id": str(video.id), "extra": True})


@pytest.mark.asyncio
async def test_generated_asset_import_uses_authoritative_catalog_context() -> None:
    service, repository, _uow = service_fixture()
    context = execution_context(repository.record.plan.tenant_id)
    await service.decide(
        context,
        repository.record.plan.id,
        ProductionPlanDecisionState.APPROVED_FOR_GENERATION,
    )
    image_job = repository.jobs[0]

    class Assets:
        async def ingest_generated(self, supplied_context, **values):
            assert supplied_context == context
            assert values["original_filename"].startswith("LOCAL-DEMO")
            assert values["content"]
            return SimpleNamespace(id=uuid4())

    importer = ApplicationGeneratedAssetImporter(Assets(), object(), local_demo=True)
    execution = ExecutableGeneration(
        replace(image_job, input_assets=()),
        {},
        (),
        initiating_context=context,
        brand_id=uuid4(),
        product_id=repository.record.plan.product_id,
    )
    assert await importer.import_result(execution, b"\x89PNG\r\n\x1a\nbytes", "image/png")
    with pytest.raises(ValueError, match="lacks authoritative"):
        await importer.import_result(
            ExecutableGeneration(image_job, {}, ()), b"\x89PNG\r\n\x1a\nbytes", "image/png"
        )


@pytest.mark.asyncio
async def test_production_service_not_found_rejection_and_pricing_drift() -> None:
    service, repository, _uow = service_fixture()
    context = execution_context(repository.record.plan.tenant_id)
    missing = uuid4()
    with pytest.raises(ProductionNotFound):
        await service.get_plan(context, missing)
    with pytest.raises(ProductionNotFound):
        await service.list_jobs(context, missing)
    with pytest.raises(ProductionNotFound):
        await service.get_job(context, missing)
    with pytest.raises(ProductionNotFound):
        await service.decide(context, missing, ProductionPlanDecisionState.REJECTED)

    rejected = await service.decide(
        context, repository.record.plan.id, ProductionPlanDecisionState.REJECTED
    )
    assert rejected.decision is not None and repository.jobs == ()
    service, repository, _uow = service_fixture()
    repository.record = replace(
        repository.record,
        plan=replace(
            repository.record.plan,
            cost=replace(repository.record.plan.cost, video_pricing_version="retired"),
        ),
    )
    with pytest.raises(ProductionPricingChanged):
        await service.decide(
            context,
            repository.record.plan.id,
            ProductionPlanDecisionState.APPROVED_FOR_GENERATION,
        )
