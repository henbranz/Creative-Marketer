# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-def,no-untyped-call,assignment,import-untyped,var-annotated"

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticationAssurance,
    ExecutionContext,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.publishing.application import PublicationDraftRecord, PublishingService
from creative_marketer.publishing.domain import (
    FinalCreativeAuthority,
    FinalCreativeNotApproved,
    PublicationApprovalRequired,
    PublicationCancelled,
    PublicationDecisionState,
    PublicationDraftOutdated,
    PublicationMode,
    PublicationNotFound,
    PublicationOutcomeUnknown,
    PublicationPermissionDenied,
    PublicationStatus,
    SocialAccount,
    SocialAccountInactive,
    SocialAccountStatus,
    SocialPlatform,
)
from creative_marketer.publishing.provider import FakeBehavior, FakeSocialProvider

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def context(role: MembershipRole = MembershipRole.OWNER) -> ExecutionContext:
    user_id = uuid4()
    return ExecutionContext(
        uuid4(),
        Actor(ActorKind.USER, user_id),
        user_id,
        role,
        MembershipStatus.ACTIVE,
        "test",
        AuthenticationAssurance(datetime.now(UTC), "test", "mfa"),
    )


class Sink:
    def __init__(self) -> None:
        self.values = []

    async def append(self, value) -> None:
        self.values.append(value)


class Repository:
    def __init__(self, authority: FinalCreativeAuthority) -> None:
        self.authority = authority
        self.accounts = {}
        self.drafts = {}
        self.decisions = {}
        self.jobs = {}
        self.publications = {}

    async def final_creative_authority(self, final_creative_id: UUID):
        return self.authority if final_creative_id == self.authority.final_creative_id else None

    async def add_account(self, account) -> None:
        self.accounts[account.id] = account

    async def list_accounts(self):
        return tuple(self.accounts.values())

    async def get_account(self, account_id, *, for_update=False):
        return self.accounts.get(account_id)

    async def add_draft(self, draft) -> None:
        self.drafts[draft.id] = draft

    async def get_draft(self, draft_id, *, for_update=False):
        draft = self.drafts.get(draft_id)
        if draft is None:
            return None
        return PublicationDraftRecord(draft, self.decisions.get(draft_id), self.jobs.get(draft_id))

    async def list_drafts(self, product_id):
        return tuple(
            PublicationDraftRecord(item, self.decisions.get(item.id), self.jobs.get(item.id))
            for item in self.drafts.values()
            if item.product_id == product_id
        )

    async def add_decision(self, decision) -> None:
        self.decisions[decision.binding.publication_draft_id] = decision

    async def add_job(self, job) -> None:
        self.jobs[job.publication_draft_id] = job

    async def update_job(self, job) -> None:
        self.jobs[job.publication_draft_id] = job

    async def add_publication(self, publication) -> None:
        self.publications[publication.publication_draft_id] = publication

    async def publication_for_draft(self, draft_id):
        return self.publications.get(draft_id)

    async def list_publications(self, product_id):
        return tuple(item for item in self.publications.values() if item.product_id == product_id)

    async def get_publication(self, publication_id):
        return next(
            (item for item in self.publications.values() if item.id == publication_id),
            None,
        )


class UnitOfWork:
    def __init__(self, repository: Repository, audit: Sink, outbox: Sink) -> None:
        self.publishing = repository
        self.audit = audit
        self.outbox = outbox
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1


class Factory:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        self.audit = Sink()
        self.outbox = Sink()
        self.units = []

    def __call__(self, tenant_id):
        assert tenant_id == self.repository.authority.tenant_id
        unit = UnitOfWork(self.repository, self.audit, self.outbox)
        self.units.append(unit)
        return unit


def stack(
    *,
    approved: bool = True,
    behavior: FakeBehavior = FakeBehavior.SUCCESS,
) -> tuple[ExecutionContext, PublishingService, Repository, FakeSocialProvider, Factory]:
    ctx = context()
    authority = FinalCreativeAuthority(
        ctx.tenant_id,
        uuid4(),
        uuid4(),
        DIGEST_A,
        uuid4(),
        DIGEST_B,
        "video",
        15_000,
        1080,
        1920,
        uuid4() if approved else None,
        "APPROVED_FOR_PUBLISHING" if approved else None,
    )
    repository = Repository(authority)
    account = SocialAccount(
        ctx.tenant_id,
        SocialPlatform.INSTAGRAM,
        "Fake Instagram",
        "fake-account",
    )
    repository.accounts[account.id] = account
    factory = Factory(repository)
    provider = FakeSocialProvider(behavior)
    return ctx, PublishingService(factory, provider), repository, provider, factory


async def create(service, ctx, repository, **values):
    account = next(iter(repository.accounts.values()))
    return await service.create_draft(
        ctx,
        product_id=repository.authority.product_id,
        final_creative_id=repository.authority.final_creative_id,
        social_account_id=account.id,
        caption="Reviewed caption",
        mode=PublicationMode.POST_NOW,
        **values,
    )


@pytest.mark.asyncio
async def test_approved_fake_publication_is_idempotent_and_reference_only() -> None:
    ctx, service, repository, provider, factory = stack()
    record = await create(service, ctx, repository)
    approved = await service.decide(ctx, record.draft.id, PublicationDecisionState.APPROVED)
    assert approved.job and approved.job.status is PublicationStatus.APPROVED

    first = await service.execute(ctx, record.draft.id)
    second = await service.execute(ctx, record.draft.id)

    assert first.job and first.job.status is PublicationStatus.PUBLISHED
    assert second.job and second.job.status is PublicationStatus.PUBLISHED
    assert provider.submit_count == 1
    assert len(repository.publications) == 1
    assert all("Reviewed caption" not in str(event.payload) for event in factory.outbox.values)
    assert any(item.action == "publishing.submit.started" for item in factory.audit.values)


@pytest.mark.asyncio
async def test_unapproved_final_and_read_only_member_are_rejected() -> None:
    ctx, service, repository, _, _ = stack(approved=False)
    with pytest.raises(FinalCreativeNotApproved):
        await create(service, ctx, repository)
    member = ExecutionContext(
        ctx.tenant_id,
        ctx.actor,
        ctx.user_id,
        MembershipRole.MEMBER,
        ctx.membership_status,
        ctx.environment,
        ctx.authentication,
    )
    with pytest.raises(PublicationPermissionDenied):
        await service.create_account(
            member,
            platform=SocialPlatform.FACEBOOK,
            display_name="Fake Facebook",
            external_account_id="fake-facebook",
        )


@pytest.mark.asyncio
async def test_scheduled_publication_can_be_cancelled_before_submit() -> None:
    ctx, service, repository, provider, _ = stack()
    account = next(iter(repository.accounts.values()))
    record = await service.create_draft(
        ctx,
        product_id=repository.authority.product_id,
        final_creative_id=repository.authority.final_creative_id,
        social_account_id=account.id,
        caption="Scheduled caption",
        mode=PublicationMode.SCHEDULE,
        scheduled_at=datetime.now(UTC) + timedelta(hours=1),
    )
    await service.decide(ctx, record.draft.id, PublicationDecisionState.APPROVED)
    scheduled = await service.execute(ctx, record.draft.id)
    assert scheduled.job and scheduled.job.status is PublicationStatus.SCHEDULED
    cancelled = await service.cancel(ctx, record.draft.id)
    assert cancelled.job and cancelled.job.status is PublicationStatus.CANCELLED
    assert provider.submit_count == 0


@pytest.mark.asyncio
async def test_unknown_outcome_reconciles_without_second_submit() -> None:
    ctx, service, repository, provider, _ = stack(behavior=FakeBehavior.OUTCOME_UNKNOWN)
    record = await create(service, ctx, repository)
    await service.decide(ctx, record.draft.id, PublicationDecisionState.APPROVED)
    unknown = await service.execute(ctx, record.draft.id)
    assert unknown.job and unknown.job.status is PublicationStatus.OUTCOME_UNKNOWN
    settled = await service.reconcile(ctx, record.draft.id)
    assert settled.job and settled.job.status is PublicationStatus.PUBLISHED
    assert provider.submit_count == 1
    with pytest.raises(PublicationOutcomeUnknown):
        await service.reconcile(ctx, record.draft.id)


@pytest.mark.asyncio
async def test_account_draft_and_publication_queries_cover_the_complete_fake_lifecycle() -> None:
    ctx, service, repository, provider, _ = stack()
    created_account = await service.create_account(
        ctx,
        platform=SocialPlatform.FACEBOOK,
        display_name="Fake Facebook",
        external_account_id="fake-facebook",
        username="@fake-facebook",
    )
    assert created_account in await service.list_accounts(ctx)
    record = await create(service, ctx, repository, hashtags=(" launch ", "#reviewed"))
    assert (await service.get_draft(ctx, record.draft.id)).draft.id == record.draft.id
    assert (await service.list_drafts(ctx, record.draft.product_id))[0].draft.id == record.draft.id
    rejected = await service.decide(ctx, record.draft.id, PublicationDecisionState.REJECTED)
    assert rejected.decision and rejected.decision.state is PublicationDecisionState.REJECTED
    assert await service.decide(ctx, record.draft.id, PublicationDecisionState.REJECTED) == rejected

    ctx2, service2, repository2, _, _ = stack()
    record2 = await create(service2, ctx2, repository2)
    await service2.decide(ctx2, record2.draft.id, PublicationDecisionState.APPROVED)
    published = await service2.execute(ctx2, record2.draft.id)
    publications = await service2.list_publications(ctx2, record2.draft.product_id)
    assert provider.submit_count == 0
    assert (
        publications
        and (await service2.get_publication(ctx2, publications[0].id)) == publications[0]
    )
    assert (await service2.execute(ctx2, record2.draft.id)).job == published.job
    with pytest.raises(PublicationNotFound):
        await service2.get_draft(ctx2, uuid4())
    with pytest.raises(PublicationNotFound):
        await service2.get_publication(ctx2, uuid4())


@pytest.mark.asyncio
async def test_execution_and_authority_failures_are_closed_and_stable() -> None:
    ctx, service, repository, _, _ = stack()
    with pytest.raises(PublicationNotFound):
        await service.execute(ctx, uuid4())
    record = await create(service, ctx, repository)
    with pytest.raises(PublicationApprovalRequired):
        await service.execute(ctx, record.draft.id)
    with pytest.raises(PublicationNotFound):
        await service.decide(ctx, uuid4(), PublicationDecisionState.APPROVED)
    with pytest.raises(PublicationOutcomeUnknown):
        await service.reconcile(ctx, record.draft.id)
    with pytest.raises(PublicationNotFound):
        await service.cancel(ctx, record.draft.id)

    approved = await service.decide(ctx, record.draft.id, PublicationDecisionState.APPROVED)
    assert approved.decision and approved.job
    repository.decisions[record.draft.id] = replace(
        approved.decision,
        binding=replace(approved.decision.binding, action_digest=DIGEST_A),
    )
    with pytest.raises(PublicationDraftOutdated):
        await service.execute(ctx, record.draft.id)
    repository.decisions[record.draft.id] = approved.decision
    repository.jobs[record.draft.id] = approved.job.transition(PublicationStatus.CANCELLED)
    with pytest.raises(PublicationCancelled):
        await service.execute(ctx, record.draft.id)
    with pytest.raises(PublicationCancelled):
        await service.cancel(ctx, record.draft.id)

    repository.jobs[record.draft.id] = approved.job
    repository.authority = replace(repository.authority, output_asset_digest=DIGEST_A)
    with pytest.raises(PublicationDraftOutdated):
        await service.execute(ctx, record.draft.id)
    repository.authority = replace(repository.authority, output_asset_digest=DIGEST_B)
    account = next(iter(repository.accounts.values()))
    repository.accounts[account.id] = account.with_status(SocialAccountStatus.DISCONNECTED)
    with pytest.raises(SocialAccountInactive):
        await service.execute(ctx, record.draft.id)


@pytest.mark.asyncio
async def test_delayed_and_failed_provider_results_are_persisted_safely() -> None:
    ctx, service, repository, provider, _ = stack(behavior=FakeBehavior.DELAYED)
    record = await create(service, ctx, repository)
    await service.decide(ctx, record.draft.id, PublicationDecisionState.APPROVED)
    submitted = await service.execute(ctx, record.draft.id)
    assert submitted.job and submitted.job.status is PublicationStatus.SUBMITTED
    reconciled = await service.reconcile(ctx, record.draft.id)
    assert reconciled.job and reconciled.job.status is PublicationStatus.PUBLISHED
    assert provider.submit_count == 1

    ctx2, service2, repository2, _, _ = stack(behavior=FakeBehavior.FAILURE)
    record2 = await create(service2, ctx2, repository2)
    await service2.decide(ctx2, record2.draft.id, PublicationDecisionState.APPROVED)
    failed = await service2.execute(ctx2, record2.draft.id)
    assert failed.job and failed.job.status is PublicationStatus.FAILED
