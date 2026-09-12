from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import tenant_event
from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.workflow_orchestration.contracts import MediaProductionWorkflowInput
from creative_marketer.workflow_orchestration.production_bridge import (
    StartMediaProductionWorkflow,
)


class Starter:
    def __init__(self) -> None:
        self.requests: list[MediaProductionWorkflowInput] = []

    async def start_media_production(self, request: MediaProductionWorkflowInput) -> None:
        self.requests.append(request)


class Resolver:
    def __init__(self, image_job: UUID, video_job: UUID) -> None:
        self.image_job, self.video_job = image_job, video_job
        self.calls: list[tuple[UUID, UUID]] = []

    async def resolve(
        self, tenant_id: UUID, plan_id: UUID
    ) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
        self.calls.append((tenant_id, plan_id))
        return (self.image_job,), (self.video_job,)


@pytest.mark.asyncio
async def test_approved_plan_event_starts_ids_only_production_workflow() -> None:
    tenant_id, user_id, plan_id = uuid4(), uuid4(), uuid4()
    context = ExecutionContext(
        tenant_id,
        Actor(ActorKind.USER, user_id),
        user_id,
        MembershipRole.OWNER,
        MembershipStatus.ACTIVE,
        "test",
        AuthenticationAssurance(datetime.now(UTC), "test", "mfa"),
        uuid4(),
    )
    event_type = "production.plan.approved_for_generation.v1"
    event = tenant_event(
        context,
        event_type=event_type,
        schema_version=1,
        aggregate_type="production_plan",
        aggregate_id=plan_id,
        occurred_at=datetime.now(UTC),
        payload_schema_digest=EventContractRegistry().schema_digest(event_type),
        payload={
            "production_plan_id": str(plan_id),
            "decision_id": str(uuid4()),
            "plan_digest": "sha256:" + "a" * 64,
            "video_route_version": "video-v1",
            "image_route_version": "image-v1",
            "video_pricing_version": "video-price-v1",
            "image_pricing_version": "image-price-v1",
            "estimated_max_cost": "4.200000",
            "currency": "USD",
        },
    )
    starter = Starter()
    image_job, video_job = uuid4(), uuid4()
    resolver = Resolver(image_job, video_job)
    await StartMediaProductionWorkflow(starter, resolver)(event, object())  # type: ignore[arg-type]
    assert resolver.calls == [(tenant_id, plan_id)]
    request = starter.requests[0]
    assert request.production_plan_id == str(plan_id)
    assert request.image_job_ids == (str(image_job),)
    assert request.video_job_ids == (str(video_job),)
    assert not hasattr(request, "plan")


@pytest.mark.asyncio
async def test_production_bridge_rejects_event_identity_mismatch() -> None:
    starter, resolver = Starter(), Resolver(uuid4(), uuid4())
    tenant_id, user_id, plan_id = uuid4(), uuid4(), uuid4()
    context = ExecutionContext(
        tenant_id,
        Actor(ActorKind.USER, user_id),
        user_id,
        MembershipRole.OWNER,
        MembershipStatus.ACTIVE,
        "test",
        AuthenticationAssurance(datetime.now(UTC), "test", "mfa"),
    )
    event_type = "production.plan.approved_for_generation.v1"
    event = tenant_event(
        context,
        event_type=event_type,
        schema_version=1,
        aggregate_type="production_plan",
        aggregate_id=plan_id,
        occurred_at=datetime.now(UTC),
        payload_schema_digest=EventContractRegistry().schema_digest(event_type),
        payload={
            "production_plan_id": str(uuid4()),
            "decision_id": str(uuid4()),
            "plan_digest": "sha256:" + "b" * 64,
            "video_route_version": "video-v1",
            "image_route_version": "image-v1",
            "video_pricing_version": "video-price-v1",
            "image_pricing_version": "image-price-v1",
            "estimated_max_cost": "1.000000",
            "currency": "USD",
        },
    )
    with pytest.raises(ValueError, match="identity mismatch"):
        await StartMediaProductionWorkflow(starter, resolver)(event, object())  # type: ignore[arg-type]
    assert not starter.requests
