# mypy: disable-error-code="arg-type,no-untyped-call,no-untyped-def,unused-ignore"

import hashlib
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.infrastructure.database.knowledge_projection import (
    SqlAlchemyCanonicalKnowledgeReader,
)
from creative_marketer.infrastructure.obsidian.bridge import relative_note_path
from creative_marketer.knowledge.domain import KnowledgeNodeType
from creative_marketer.publishing.domain import (
    FAKE_PLATFORM_CAPABILITIES,
    PlatformCapabilities,
    PlatformCapabilityUnsupported,
    PublicationApprovalBinding,
    PublicationMediaIncompatible,
    PublicationMode,
    PublicationStatus,
    SocialAccount,
    SocialPlatform,
    build_publication,
    build_publication_draft,
)
from creative_marketer.publishing.execution import (
    ExecutablePublication,
    GovernedPublicationJobExecutor,
    PublicationResourceResolver,
    SocialPublishingToolExecutor,
    normalize_publication_input,
    social_tool_bindings,
)
from creative_marketer.publishing.provider import FakeBehavior, FakeSocialProvider
from creative_marketer.publishing.tool_contracts import social_tool_contracts
from creative_marketer.tool_execution.application import ResourceAccessDenied
from creative_marketer.tool_execution.domain import (
    GatewayResult,
    GatewayStatus,
    OutcomeUnknown,
    PreEffectFailure,
    ToolExecutionContext,
)
from creative_marketer.workflow_orchestration.contracts import PublicationWorkflowResult

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def draft(**changes: object):
    values = {
        "tenant_id": uuid4(),
        "product_id": uuid4(),
        "final_creative_id": uuid4(),
        "final_creative_digest": DIGEST_A,
        "output_asset_id": uuid4(),
        "output_asset_digest": DIGEST_B,
        "platform": SocialPlatform.INSTAGRAM,
        "social_account_id": uuid4(),
        "external_destination_id": "fake-instagram-account",
        "caption": "A reviewed publication caption",
        "mode": PublicationMode.POST_NOW,
        "created_by_user_id": uuid4(),
    }
    values.update(changes)
    return build_publication_draft(**values)  # type: ignore[arg-type]


def test_publication_approval_binds_every_material_side_effect() -> None:
    original = draft(hashtags=("launch",), platform_settings={"comments": True})
    binding = PublicationApprovalBinding.from_draft(original)

    mutations = (
        {"caption": "Changed caption"},
        {"platform": SocialPlatform.FACEBOOK},
        {"social_account_id": uuid4()},
        {"external_destination_id": "another-account"},
        {
            "mode": PublicationMode.SCHEDULE,
            "scheduled_at": datetime.now(UTC) + timedelta(days=1),
        },
        {"platform_settings": {"comments": False}},
    )
    for mutation in mutations:
        assert PublicationApprovalBinding.from_draft(draft(**mutation)) != binding


def test_draft_and_publication_are_immutable_facts() -> None:
    value = draft()
    with pytest.raises(FrozenInstanceError):
        value.caption = "mutated"  # type: ignore[misc]
    published = build_publication(
        value,
        external_post_id="fake-post-1",
        external_operation_id="fake-op-1",
        canonical_permalink="https://social.invalid/fake/post/fake-post-1",
        provider="fake",
        connector_version="fake-social-v1",
        submitted_at=datetime.now(UTC),
        published_at=datetime.now(UTC),
    )
    with pytest.raises(FrozenInstanceError):
        published.external_post_id = "other"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_fake_provider_is_idempotent_and_never_uses_a_real_destination() -> None:
    provider = FakeSocialProvider()
    value = draft()

    first = await provider.submit_publication(value)
    second = await provider.submit_publication(value)

    assert first == second
    assert provider.submit_count == 1
    assert first.state.value == "PUBLISHED"
    assert first.permalink and first.permalink.startswith("https://social.invalid/")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("behavior", "initial"),
    [
        (FakeBehavior.DELAYED, "SUBMITTED"),
        (FakeBehavior.OUTCOME_UNKNOWN, "OUTCOME_UNKNOWN"),
    ],
)
async def test_fake_provider_reconciles_without_resubmitting(
    behavior: FakeBehavior, initial: str
) -> None:
    provider = FakeSocialProvider(behavior)
    submitted = await provider.submit_publication(draft())
    assert submitted.state.value == initial
    settled = await provider.get_publication_status(submitted.external_operation_id)
    assert settled.state.value == "PUBLISHED"
    assert provider.submit_count == 1


@pytest.mark.asyncio
async def test_fake_provider_reconciles_after_process_restart() -> None:
    operation_id = "fake-op-012345678901234567890123"
    settled = await FakeSocialProvider().get_publication_status(operation_id)
    assert settled.state.value == "PUBLISHED"
    assert settled.external_post_id == "fake-post-012345678901234567890123"

    cancelled = await FakeSocialProvider().cancel_scheduled_publication(operation_id)
    assert cancelled.state.value == "CANCELLED"


def test_platform_compatibility_is_enforced() -> None:
    instagram = FAKE_PLATFORM_CAPABILITIES[SocialPlatform.INSTAGRAM]
    with pytest.raises(PublicationMediaIncompatible):
        instagram.validate(
            media_kind="video",
            duration_ms=1_000,
            width=1_920,
            height=1_080,
            caption="valid",
            mode=PublicationMode.POST_NOW,
        )
    with pytest.raises(PlatformCapabilityUnsupported):
        instagram.validate(
            media_kind="video",
            duration_ms=1_000,
            width=1_080,
            height=1_920,
            caption="x" * 2_201,
            mode=PublicationMode.POST_NOW,
        )


def test_social_account_rejects_real_provider_configuration() -> None:
    with pytest.raises(ValueError, match="real social providers"):
        SocialAccount(uuid4(), SocialPlatform.INSTAGRAM, "Real", "account", provider="instagram")


def test_job_transitions_are_controlled() -> None:
    from creative_marketer.publishing.domain import PublicationJob

    job = PublicationJob(uuid4(), uuid4(), "op_" + "a" * 32, PublicationStatus.APPROVED, uuid4())
    assert job.transition(PublicationStatus.SUBMITTING).status is PublicationStatus.SUBMITTING
    with pytest.raises(ValueError, match="invalid PublicationJob transition"):
        job.transition(PublicationStatus.PUBLISHED)


def test_publishing_domain_rejects_every_invalid_side_effect_shape() -> None:
    unsupported = PlatformCapabilities(
        SocialPlatform.INSTAGRAM,
        frozenset({"video"}),
        10,
        False,
        maximum_duration_seconds=1,
    )
    for values in (
        {"media_kind": "image", "duration_ms": 1_000, "mode": PublicationMode.POST_NOW},
        {"media_kind": "video", "duration_ms": 1_000, "mode": PublicationMode.SCHEDULE},
        {"media_kind": "video", "duration_ms": 2_000, "mode": PublicationMode.POST_NOW},
    ):
        with pytest.raises((PublicationMediaIncompatible, PlatformCapabilityUnsupported)):
            unsupported.validate(width=1080, height=1920, caption="valid", **values)
    with pytest.raises(ValueError, match="display name"):
        SocialAccount(uuid4(), SocialPlatform.INSTAGRAM, "", "fake")
    with pytest.raises(ValueError, match="external identifier"):
        SocialAccount(uuid4(), SocialPlatform.INSTAGRAM, "Fake", "")

    value = draft()
    invalid_drafts = (
        lambda: replace(value, caption=""),
        lambda: replace(value, external_destination_id=""),
        lambda: replace(value, final_creative_digest="invalid"),
        lambda: replace(value, mode=PublicationMode.SCHEDULE),
        lambda: replace(value, scheduled_at=datetime.now(UTC)),
        lambda: replace(value, hashtags=("",)),
        lambda: replace(value, semantic_digest=DIGEST_A),
    )
    for mutation in invalid_drafts:
        with pytest.raises(ValueError):
            mutation()

    published = build_publication(
        value,
        external_post_id="fake-post",
        external_operation_id="fake-op",
        canonical_permalink="https://social.invalid/fake/post/fake-post",
        provider="fake",
        connector_version="fake-social-v1",
        submitted_at=datetime.now(UTC),
        published_at=datetime.now(UTC),
    )
    with pytest.raises(ValueError, match="completed published"):
        replace(published, status=PublicationStatus.FAILED)
    with pytest.raises(ValueError, match="digest"):
        replace(published, request_digest="invalid")


def test_publication_projects_to_knowledge_and_obsidian_without_credentials() -> None:
    now = datetime.now(UTC)
    product_id, final_id, asset_id, account_id = (uuid4() for _ in range(4))
    draft_id, decision_id, publication_id = uuid4(), uuid4(), uuid4()
    empty: dict[str, list[dict[str, object]]] = {
        key: []
        for key in (
            "brands",
            "products",
            "product_snapshots",
            "assets",
            "definitions",
            "versions",
            "activations",
            "runs",
            "sources",
            "evidence",
            "research_targets",
            "social_evidence",
            "research_snapshots",
            "concept_sets",
            "concepts",
            "decisions",
        )
    }
    empty.update(
        {
            "social_accounts": [
                {
                    "id": account_id,
                    "platform": "instagram",
                    "display_name": "Fake Instagram",
                    "username": "@fake",
                    "provider": "fake",
                    "status": "ACTIVE",
                    "created_at": now,
                    "updated_at": now,
                }
            ],
            "publication_drafts": [
                {
                    "id": draft_id,
                    "product_id": product_id,
                    "final_creative_id": final_id,
                    "output_asset_id": asset_id,
                    "social_account_id": account_id,
                    "platform": "instagram",
                    "mode": "POST_NOW",
                    "scheduled_at": None,
                    "hashtags": ["launch"],
                    "caption": "safe caption",
                    "semantic_digest": DIGEST_A,
                    "created_at": now,
                }
            ],
            "publication_decisions": [
                {
                    "id": decision_id,
                    "publication_draft_id": draft_id,
                    "state": "APPROVED",
                    "action_digest": DIGEST_B,
                    "created_at": now,
                }
            ],
            "publications": [
                {
                    "id": publication_id,
                    "publication_draft_id": draft_id,
                    "final_creative_id": final_id,
                    "social_account_id": account_id,
                    "platform": "instagram",
                    "status": "PUBLISHED",
                    "provider": "fake",
                    "external_post_id": "fake-post",
                    "canonical_permalink": "https://social.invalid/fake/post/fake-post",
                    "semantic_digest": DIGEST_A,
                    "created_at": now,
                    "published_at": now,
                }
            ],
        }
    )
    graph = SqlAlchemyCanonicalKnowledgeReader(None)._build(empty)  # type: ignore[arg-type]
    node_types = {node.node_type for node in graph.nodes}
    assert {
        KnowledgeNodeType.SOCIAL_ACCOUNT,
        KnowledgeNodeType.PUBLICATION_DRAFT,
        KnowledgeNodeType.PUBLICATION_DECISION,
        KnowledgeNodeType.PUBLICATION,
    }.issubset(node_types)
    assert "safe caption" not in str(graph.nodes)
    assert relative_note_path("publication", str(publication_id)).parts[0] == "Publishing"


@pytest.mark.asyncio
async def test_temporal_executor_can_submit_only_through_tool_gateway() -> None:
    tenant_id = uuid4()
    draft_id = uuid4()
    user_id = uuid4()
    agent_id = uuid4()
    context = ExecutionContext(
        tenant_id,
        Actor(ActorKind.USER, user_id),
        user_id,
        MembershipRole.OWNER,
        MembershipStatus.ACTIVE,
        "test",
        AuthenticationAssurance(datetime.now(UTC), "test", "mfa"),
    )
    operation_id = (
        "op_"
        + hashlib.sha256(f"{tenant_id}:{draft_id}:social.publish.submit".encode()).hexdigest()[:32]
    )

    class Authority:
        async def prepare(self, tenant: object, draft_value: object):
            assert tenant == tenant_id and draft_value == draft_id
            return ExecutablePublication(
                context, agent_id, PublicationStatus.APPROVED, operation_id
            )

        async def current(self, tenant: object, draft_value: object):
            return PublicationWorkflowResult(str(draft_id), "PUBLISHED", "publication-1")

    class Gateway:
        def __init__(self):
            self.calls = []

        async def invoke(self, invocation: object, request: object):
            self.calls.append((invocation, request))
            return GatewayResult(GatewayStatus.EXECUTED, operation_id)

    authority = Authority()
    gateway = Gateway()
    result = await GovernedPublicationJobExecutor(authority, gateway).submit(  # type: ignore[arg-type]
        tenant_id, draft_id
    )

    assert result.status == "PUBLISHED"
    assert len(gateway.calls) == 1
    request = gateway.calls[0][1]
    assert request.tool_key == "social.publish.submit"
    assert request.raw_input == {"publication_draft_id": str(draft_id)}
    assert "caption" not in request.raw_input


def test_social_tool_contracts_and_bindings_are_exact_and_credential_capable() -> None:
    contracts = social_tool_contracts()
    assert [item.tool_key for item in contracts] == [
        "social.publish.submit",
        "social.publish.status",
        "social.publish.cancel",
    ]
    assert all(item.configuration.idempotency_requirement.name == "REQUIRED" for item in contracts)
    tools = {
        item.tool_key: SimpleNamespace(definition_id=uuid4(), version_id=uuid4())
        for item in contracts
    }
    bindings = social_tool_bindings(SimpleNamespace(), tools)  # type: ignore[arg-type]
    assert len(bindings) == 3
    assert all(binding.credential_capable for binding in bindings)


@pytest.mark.asyncio
async def test_resource_resolution_and_executor_fail_closed() -> None:
    tenant_id, draft_id = uuid4(), uuid4()
    ctx = ExecutionContext(
        tenant_id,
        Actor(ActorKind.SYSTEM, uuid4()),
        None,
        None,
        None,
        "test",
        None,
    )

    class Authority:
        denied = False
        result: object = PublicationWorkflowResult(str(draft_id), "PUBLISHED", "publication-1")

        async def authorize_resource(self, tenant, draft_value):
            assert tenant == tenant_id and draft_value == draft_id
            if self.denied:
                raise LookupError("another tenant")

        async def execute_tool(self, tenant, draft_value, operation):
            assert tenant == tenant_id and draft_value == draft_id and operation
            value = self.result
            if isinstance(value, Exception):
                raise value
            return value

    authority = Authority()
    normalized = normalize_publication_input({"publication_draft_id": str(draft_id)})
    with pytest.raises((ValueError, TypeError)):
        normalize_publication_input({"publication_draft_id": str(draft_id), "caption": "leak"})
    resolution = await PublicationResourceResolver(authority)(ctx, SimpleNamespace(), normalized)
    assert resolution.resource_id == str(draft_id)
    authority.denied = True
    with pytest.raises(ResourceAccessDenied):
        await PublicationResourceResolver(authority)(ctx, SimpleNamespace(), normalized)

    tool_context = ToolExecutionContext(
        tenant_id, uuid4(), "op_" + "a" * 32, uuid4(), uuid4(), uuid4(), uuid4()
    )
    authority.denied = False
    executor = SocialPublishingToolExecutor(authority, "submit")
    succeeded = await executor.execute(tool_context, normalized)
    assert succeeded.result_ref.endswith("publication-1")
    authority.result = PublicationWorkflowResult(str(draft_id), "PUBLISHED")
    assert (await executor.execute(tool_context, normalized)).result_ref.endswith(str(draft_id))
    authority.result = PublicationWorkflowResult(str(draft_id), "OUTCOME_UNKNOWN")
    with pytest.raises(OutcomeUnknown):
        await executor.execute(tool_context, normalized)
    authority.result = PublicationWorkflowResult(str(draft_id), "FAILED")
    with pytest.raises(PreEffectFailure):
        await executor.execute(tool_context, normalized)
    authority.result = OutcomeUnknown("unknown")
    with pytest.raises(OutcomeUnknown):
        await executor.execute(tool_context, normalized)
    coded = RuntimeError("safe")
    coded.code = "PUBLICATION_OUTCOME_UNKNOWN"  # type: ignore[attr-defined]
    authority.result = coded
    with pytest.raises(OutcomeUnknown):
        await executor.execute(tool_context, normalized)
    authority.result = RuntimeError("provider detail")
    with pytest.raises(PreEffectFailure, match="safely"):
        await executor.execute(tool_context, normalized)


@pytest.mark.asyncio
async def test_governed_executor_handles_replay_waiting_and_authority_mismatch() -> None:
    tenant_id, draft_id, agent_id = uuid4(), uuid4(), uuid4()
    ctx = ExecutionContext(
        tenant_id,
        Actor(ActorKind.SYSTEM, uuid4()),
        None,
        None,
        None,
        "test",
        None,
    )

    class Authority:
        operation_id = ""
        wrong_tenant = False

        async def prepare(self, tenant, draft_value):
            return ExecutablePublication(
                ctx if not self.wrong_tenant else replace(ctx, tenant_id=uuid4()),
                agent_id,
                PublicationStatus.APPROVED,
                self.operation_id,
            )

        async def current(self, tenant, draft_value):
            return PublicationWorkflowResult(str(draft_id), "SUBMITTED", failure_code="pending")

    class Gateway:
        status = GatewayStatus.REPLAYED

        async def invoke(self, invocation, request):
            return GatewayResult(self.status, request.operation_id, reason_code="approval_required")

    authority, gateway = Authority(), Gateway()
    executor = GovernedPublicationJobExecutor(authority, gateway)
    submit_operation = (
        "op_"
        + hashlib.sha256(f"{tenant_id}:{draft_id}:social.publish.submit".encode()).hexdigest()[:32]
    )
    authority.operation_id = submit_operation
    assert (await executor.submit(tenant_id, draft_id)).status == "SUBMITTED"
    gateway.status = GatewayStatus.AWAITING_APPROVAL
    waiting = await executor.reconcile(tenant_id, draft_id)
    assert waiting.failure_code == "approval_required"
    assert (await executor.cancel(tenant_id, draft_id)).status == "SUBMITTED"
    authority.wrong_tenant = True
    with pytest.raises(ValueError, match="another tenant"):
        await executor.cancel(tenant_id, draft_id)
    authority.wrong_tenant = False
    authority.operation_id = "op_" + "f" * 32
    with pytest.raises(ValueError, match="not authoritative"):
        await executor.submit(tenant_id, draft_id)
