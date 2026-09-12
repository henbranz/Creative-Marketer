# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,var-annotated"

from datetime import UTC, datetime
from decimal import Decimal
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
    ProductionDecisionConflict,
    ProductionPermissionDenied,
    ProductionPlanDecisionState,
)
from creative_marketer.production.execution import (
    ExecutableGeneration,
    ImageGenerateToolExecutor,
    VideoStartToolExecutor,
)
from creative_marketer.production.infrastructure import (
    FakeImageProvider,
    FakeSeedanceMediaProvider,
)
from creative_marketer.production.media import (
    ImageGenerationRequest,
    MediaProviderOutcomeUnknown,
    ProviderGenerationState,
    VideoGenerationRequest,
)
from creative_marketer.production.service import ProductionPlanRecord, ProductionService
from creative_marketer.tool_execution.domain import OutcomeUnknown, ToolExecutionContext

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
