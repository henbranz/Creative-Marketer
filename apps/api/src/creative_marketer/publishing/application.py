from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from creative_marketer.audit.application import AuditWriter
from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditOutcome
from creative_marketer.audit.safety import safe_metadata
from creative_marketer.events.application import OutboxWriter
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import DomainEvent, tenant_event
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.identity.domain import MembershipRole, MembershipStatus

from .domain import (
    FAKE_PLATFORM_CAPABILITIES,
    FinalCreativeAuthority,
    FinalCreativeNotApproved,
    PlatformCapabilityUnsupported,
    ProviderPublicationState,
    Publication,
    PublicationApprovalBinding,
    PublicationApprovalRequired,
    PublicationCancelled,
    PublicationDecision,
    PublicationDecisionState,
    PublicationDraft,
    PublicationDraftOutdated,
    PublicationJob,
    PublicationMode,
    PublicationNotFound,
    PublicationOutcomeUnknown,
    PublicationPermissionDenied,
    PublicationStatus,
    SocialAccount,
    SocialAccountInactive,
    SocialAccountStatus,
    SocialPlatform,
    build_publication,
    build_publication_draft,
)
from .provider import SocialPublishingProvider


@dataclass(frozen=True, slots=True)
class PublicationDraftRecord:
    draft: PublicationDraft
    decision: PublicationDecision | None
    job: PublicationJob | None


class PublishingRepository(Protocol):
    async def final_creative_authority(
        self, final_creative_id: UUID
    ) -> FinalCreativeAuthority | None: ...
    async def add_account(self, account: SocialAccount) -> None: ...
    async def list_accounts(self) -> tuple[SocialAccount, ...]: ...
    async def get_account(
        self, account_id: UUID, *, for_update: bool = False
    ) -> SocialAccount | None: ...
    async def add_draft(self, draft: PublicationDraft) -> None: ...
    async def get_draft(
        self, draft_id: UUID, *, for_update: bool = False
    ) -> PublicationDraftRecord | None: ...
    async def list_drafts(self, product_id: UUID) -> tuple[PublicationDraftRecord, ...]: ...
    async def add_decision(self, decision: PublicationDecision) -> None: ...
    async def add_job(self, job: PublicationJob) -> None: ...
    async def update_job(self, job: PublicationJob) -> None: ...
    async def add_publication(self, publication: Publication) -> None: ...
    async def publication_for_draft(self, draft_id: UUID) -> Publication | None: ...
    async def list_publications(self, product_id: UUID) -> tuple[Publication, ...]: ...
    async def get_publication(self, publication_id: UUID) -> Publication | None: ...


class PublishingUnitOfWork(Protocol):
    publishing: PublishingRepository
    audit: AuditWriter
    outbox: OutboxWriter

    async def __aenter__(self) -> PublishingUnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class PublishingUnitOfWorkFactory(Protocol):
    def __call__(self, tenant_id: UUID) -> PublishingUnitOfWork: ...


@dataclass(slots=True)
class PublishingService:
    uow_factory: PublishingUnitOfWorkFactory
    provider: SocialPublishingProvider
    contracts: EventContractRegistry = field(default_factory=EventContractRegistry)

    @staticmethod
    def _mutate(context: ExecutionContext) -> None:
        if (
            context.membership_status is not MembershipStatus.ACTIVE
            or context.membership_role
            not in {
                MembershipRole.OWNER,
                MembershipRole.ADMIN,
            }
        ):
            raise PublicationPermissionDenied("OWNER or ADMIN membership required")

    async def create_account(
        self,
        context: ExecutionContext,
        *,
        platform: SocialPlatform,
        display_name: str,
        external_account_id: str,
        username: str | None = None,
    ) -> SocialAccount:
        self._mutate(context)
        account = SocialAccount(
            context.tenant_id,
            platform,
            display_name,
            external_account_id,
            username=username,
            capabilities={
                "media_kinds": sorted(FAKE_PLATFORM_CAPABILITIES[platform].media_kinds),
                "supports_schedule": FAKE_PLATFORM_CAPABILITIES[platform].supports_schedule,
            },
        )
        async with self.uow_factory(context.tenant_id) as uow:
            await uow.publishing.add_account(account)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="publishing.social_account.created",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="social_account",
                    resource_id=str(account.id),
                    metadata=safe_metadata({"platform": platform.value, "provider": "fake"}),
                )
            )
            await uow.commit()
        return account

    async def list_accounts(self, context: ExecutionContext) -> tuple[SocialAccount, ...]:
        async with self.uow_factory(context.tenant_id) as uow:
            return await uow.publishing.list_accounts()

    async def create_draft(
        self,
        context: ExecutionContext,
        *,
        product_id: UUID,
        final_creative_id: UUID,
        social_account_id: UUID,
        caption: str,
        mode: PublicationMode,
        title: str | None = None,
        hashtags: tuple[str, ...] = (),
        destination_url: str | None = None,
        scheduled_at: datetime | None = None,
        platform_settings: Mapping[str, object] | None = None,
    ) -> PublicationDraftRecord:
        self._mutate(context)
        async with self.uow_factory(context.tenant_id) as uow:
            authority = await uow.publishing.final_creative_authority(final_creative_id)
            account = await uow.publishing.get_account(social_account_id)
            if authority is None or authority.product_id != product_id or not authority.approved:
                raise FinalCreativeNotApproved("current FinalCreative approval is required")
            if account is None:
                raise PublicationNotFound("SocialAccount not found")
            if account.status is not SocialAccountStatus.ACTIVE:
                raise SocialAccountInactive("SocialAccount is not active")
            capabilities = FAKE_PLATFORM_CAPABILITIES.get(account.platform)
            if capabilities is None:
                raise PlatformCapabilityUnsupported("platform is not implemented")
            capabilities.validate(
                media_kind=authority.media_kind,
                duration_ms=authority.duration_ms,
                width=authority.width,
                height=authority.height,
                caption=caption,
                mode=mode,
            )
            draft = build_publication_draft(
                tenant_id=context.tenant_id,
                product_id=product_id,
                final_creative_id=authority.final_creative_id,
                final_creative_digest=authority.final_creative_digest,
                output_asset_id=authority.output_asset_id,
                output_asset_digest=authority.output_asset_digest,
                platform=account.platform,
                social_account_id=account.id,
                external_destination_id=account.external_account_id,
                caption=caption.strip(),
                mode=mode,
                created_by_user_id=context.user_id,
                title=title.strip() if title else None,
                hashtags=tuple(value.strip().lstrip("#") for value in hashtags if value.strip()),
                destination_url=destination_url,
                scheduled_at=scheduled_at.astimezone(UTC) if scheduled_at else None,
                platform_settings=dict(platform_settings or {}),
            )
            await uow.publishing.add_draft(draft)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="publishing.draft.created",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="publication_draft",
                    resource_id=str(draft.id),
                    after_digest=draft.semantic_digest,
                    metadata=safe_metadata(
                        {"platform": draft.platform.value, "mode": draft.mode.value}
                    ),
                )
            )
            await uow.outbox.append(
                self._event(context, "publishing.draft.created.v1", draft, draft.created_at)
            )
            await uow.commit()
            return PublicationDraftRecord(draft, None, None)

    async def get_draft(self, context: ExecutionContext, draft_id: UUID) -> PublicationDraftRecord:
        async with self.uow_factory(context.tenant_id) as uow:
            record = await uow.publishing.get_draft(draft_id)
            if record is None:
                raise PublicationNotFound("PublicationDraft not found")
            return record

    async def list_drafts(
        self, context: ExecutionContext, product_id: UUID
    ) -> tuple[PublicationDraftRecord, ...]:
        async with self.uow_factory(context.tenant_id) as uow:
            return await uow.publishing.list_drafts(product_id)

    async def decide(
        self, context: ExecutionContext, draft_id: UUID, state: PublicationDecisionState
    ) -> PublicationDraftRecord:
        self._mutate(context)
        async with self.uow_factory(context.tenant_id) as uow:
            record = await uow.publishing.get_draft(draft_id, for_update=True)
            if record is None:
                raise PublicationNotFound("PublicationDraft not found")
            if record.decision and record.decision.state is state:
                return record
            await self._validate_current(uow.publishing, record.draft)
            binding = PublicationApprovalBinding.from_draft(record.draft)
            decision = PublicationDecision(context.tenant_id, binding, state, context.user_id)
            await uow.publishing.add_decision(decision)
            job = None
            if state is PublicationDecisionState.APPROVED:
                operation_id = (
                    "op_"
                    + hashlib.sha256(
                        f"{context.tenant_id}:{draft_id}:social.publish.submit".encode()
                    ).hexdigest()[:32]
                )
                job = PublicationJob(
                    context.tenant_id,
                    draft_id,
                    operation_id,
                    PublicationStatus.APPROVED,
                    context.user_id,
                )
                await uow.publishing.add_job(job)
            action = (
                "publishing.draft.approved"
                if state is PublicationDecisionState.APPROVED
                else "publishing.draft.rejected"
            )
            await uow.audit.append(
                tenant_audit(
                    context,
                    action=action,
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="publication_draft",
                    resource_id=str(draft_id),
                    after_digest=binding.action_digest,
                    metadata=safe_metadata(
                        {
                            "platform": record.draft.platform.value,
                            "account_id": str(record.draft.social_account_id),
                        }
                    ),
                )
            )
            if state is PublicationDecisionState.APPROVED:
                await uow.outbox.append(
                    self._event(
                        context, "publishing.draft.approved.v1", record.draft, decision.created_at
                    )
                )
            await uow.commit()
            return PublicationDraftRecord(record.draft, decision, job)

    async def execute(self, context: ExecutionContext, draft_id: UUID) -> PublicationDraftRecord:
        """Execute the already-approved exact draft through the provider port.

        Production composition calls this from a Temporal Activity behind Tool Gateway. The
        development API exposes it only for the fake provider walkthrough.
        """
        self._mutate(context)
        async with self.uow_factory(context.tenant_id) as uow:
            record = await uow.publishing.get_draft(draft_id, for_update=True)
            if record is None:
                raise PublicationNotFound("PublicationDraft not found")
            if (
                record.decision is None
                or record.decision.state is not PublicationDecisionState.APPROVED
            ):
                raise PublicationApprovalRequired("exact PublicationDraft approval is required")
            if record.decision.binding != PublicationApprovalBinding.from_draft(record.draft):
                raise PublicationDraftOutdated("PublicationDraft no longer matches approval")
            await self._validate_current(uow.publishing, record.draft)
            if await uow.publishing.publication_for_draft(draft_id):
                return record
            if record.job is None:
                raise PublicationApprovalRequired("PublicationJob is missing")
            if record.job.status in {PublicationStatus.CANCELLED, PublicationStatus.FAILED}:
                raise PublicationCancelled("PublicationJob cannot execute")
            job = record.job
            if (
                job.status is PublicationStatus.APPROVED
                and record.draft.mode is PublicationMode.SCHEDULE
            ):
                job = job.transition(PublicationStatus.SCHEDULED)
                await uow.publishing.update_job(job)
                await uow.commit()
                return PublicationDraftRecord(record.draft, record.decision, job)
            if job.status in {PublicationStatus.APPROVED, PublicationStatus.SCHEDULED}:
                job = job.transition(PublicationStatus.SUBMITTING)
                await uow.publishing.update_job(job)
                await uow.audit.append(
                    tenant_audit(
                        context,
                        action="publishing.submit.started",
                        outcome=AuditOutcome.SUCCESS,
                        resource_type="publication_draft",
                        resource_id=str(draft_id),
                        after_digest=record.draft.semantic_digest,
                    )
                )
                await uow.commit()
        result = await self.provider.submit_publication(record.draft)
        async with self.uow_factory(context.tenant_id) as uow:
            current = await uow.publishing.get_draft(draft_id, for_update=True)
            if current is None or current.job is None:
                raise PublicationNotFound("PublicationJob not found")
            job = current.job
            if result.state is ProviderPublicationState.PUBLISHED:
                job = job.transition(
                    PublicationStatus.PUBLISHED, external_operation_id=result.external_operation_id
                )
                assert result.external_post_id and result.published_at
                publication = build_publication(
                    current.draft,
                    external_post_id=result.external_post_id,
                    external_operation_id=result.external_operation_id,
                    canonical_permalink=result.permalink,
                    provider=self.provider.provider_key,
                    connector_version=self.provider.connector_version,
                    submitted_at=datetime.now(UTC),
                    published_at=result.published_at,
                )
                await uow.publishing.add_publication(publication)
                await uow.outbox.append(
                    self._event(
                        context,
                        "publishing.publication.published.v1",
                        current.draft,
                        result.published_at,
                        publication.id,
                    )
                )
            elif result.state is ProviderPublicationState.SUBMITTED:
                job = job.transition(
                    PublicationStatus.SUBMITTED, external_operation_id=result.external_operation_id
                )
            elif result.state is ProviderPublicationState.OUTCOME_UNKNOWN:
                job = job.transition(
                    PublicationStatus.OUTCOME_UNKNOWN,
                    failure_code="PUBLICATION_OUTCOME_UNKNOWN",
                    external_operation_id=result.external_operation_id,
                )
            else:
                job = job.transition(
                    PublicationStatus.FAILED,
                    failure_code=result.failure_code or "PUBLICATION_PROVIDER_FAILED",
                    external_operation_id=result.external_operation_id,
                )
            await uow.publishing.update_job(job)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="publishing.submit.completed",
                    outcome=AuditOutcome.SUCCESS
                    if job.status is PublicationStatus.PUBLISHED
                    else AuditOutcome.FAILED,
                    reason_code=job.failure_code,
                    resource_type="publication_draft",
                    resource_id=str(draft_id),
                    after_digest=current.draft.semantic_digest,
                    metadata=safe_metadata({"status": job.status.value, "provider": "fake"}),
                )
            )
            await uow.commit()
            return PublicationDraftRecord(current.draft, current.decision, job)

    async def cancel(self, context: ExecutionContext, draft_id: UUID) -> PublicationDraftRecord:
        self._mutate(context)
        async with self.uow_factory(context.tenant_id) as uow:
            record = await uow.publishing.get_draft(draft_id, for_update=True)
            if record is None or record.job is None:
                raise PublicationNotFound("PublicationJob not found")
            if record.job.status not in {PublicationStatus.APPROVED, PublicationStatus.SCHEDULED}:
                raise PublicationCancelled("only not-yet-submitted publication can be cancelled")
            job = record.job.transition(PublicationStatus.CANCELLED)
            await uow.publishing.update_job(job)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="publishing.schedule.cancelled",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="publication_draft",
                    resource_id=str(draft_id),
                    after_digest=record.draft.semantic_digest,
                )
            )
            await uow.commit()
            return PublicationDraftRecord(record.draft, record.decision, job)

    async def reconcile(self, context: ExecutionContext, draft_id: UUID) -> PublicationDraftRecord:
        self._mutate(context)
        async with self.uow_factory(context.tenant_id) as uow:
            record = await uow.publishing.get_draft(draft_id)
            if (
                record is None
                or record.job is None
                or record.job.status
                not in {PublicationStatus.SUBMITTED, PublicationStatus.OUTCOME_UNKNOWN}
                or not record.job.external_operation_id
            ):
                raise PublicationOutcomeUnknown("PublicationJob is not reconcilable")
        result = await self.provider.get_publication_status(record.job.external_operation_id)
        if result.state is not ProviderPublicationState.PUBLISHED:
            raise PublicationOutcomeUnknown("provider outcome remains unresolved")
        async with self.uow_factory(context.tenant_id) as uow:
            current = await uow.publishing.get_draft(draft_id, for_update=True)
            assert current and current.job and result.external_post_id and result.published_at
            if await uow.publishing.publication_for_draft(draft_id):
                return current
            job = current.job.transition(
                PublicationStatus.PUBLISHED, external_operation_id=result.external_operation_id
            )
            publication = build_publication(
                current.draft,
                external_post_id=result.external_post_id,
                external_operation_id=result.external_operation_id,
                canonical_permalink=result.permalink,
                provider=self.provider.provider_key,
                connector_version=self.provider.connector_version,
                submitted_at=current.job.updated_at,
                published_at=result.published_at,
            )
            await uow.publishing.update_job(job)
            await uow.publishing.add_publication(publication)
            await uow.audit.append(
                tenant_audit(
                    context,
                    action="publishing.reconciliation.completed",
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="publication_draft",
                    resource_id=str(draft_id),
                    after_digest=publication.semantic_digest,
                )
            )
            await uow.outbox.append(
                self._event(
                    context,
                    "publishing.publication.published.v1",
                    current.draft,
                    result.published_at,
                    publication.id,
                )
            )
            await uow.commit()
            return PublicationDraftRecord(current.draft, current.decision, job)

    async def list_publications(
        self, context: ExecutionContext, product_id: UUID
    ) -> tuple[Publication, ...]:
        async with self.uow_factory(context.tenant_id) as uow:
            return await uow.publishing.list_publications(product_id)

    async def get_publication(self, context: ExecutionContext, publication_id: UUID) -> Publication:
        async with self.uow_factory(context.tenant_id) as uow:
            value = await uow.publishing.get_publication(publication_id)
            if value is None:
                raise PublicationNotFound("Publication not found")
            return value

    @staticmethod
    async def _validate_current(repository: PublishingRepository, draft: PublicationDraft) -> None:
        authority = await repository.final_creative_authority(draft.final_creative_id)
        account = await repository.get_account(draft.social_account_id)
        if authority is None or not authority.approved:
            raise FinalCreativeNotApproved("current FinalCreative approval is required")
        if (
            authority.final_creative_digest != draft.final_creative_digest
            or authority.output_asset_digest != draft.output_asset_digest
            or authority.output_asset_id != draft.output_asset_id
        ):
            raise PublicationDraftOutdated("FinalCreative or output Asset changed")
        if account is None or account.status is not SocialAccountStatus.ACTIVE:
            raise SocialAccountInactive("SocialAccount is not active")
        if (
            account.platform is not draft.platform
            or account.external_account_id != draft.external_destination_id
        ):
            raise PublicationDraftOutdated("destination no longer matches draft")

    def _event(
        self,
        context: ExecutionContext,
        event_type: str,
        draft: PublicationDraft,
        occurred_at: datetime,
        publication_id: UUID | None = None,
    ) -> DomainEvent:
        payload: dict[str, object] = {
            "publication_draft_id": str(draft.id),
            "publication_draft_digest": draft.semantic_digest,
            "product_id": str(draft.product_id),
            "final_creative_id": str(draft.final_creative_id),
            "social_account_id": str(draft.social_account_id),
            "platform": draft.platform.value,
        }
        if publication_id:
            payload["publication_id"] = str(publication_id)
        return tenant_event(
            context,
            event_type=event_type,
            schema_version=1,
            aggregate_type="publication_draft",
            aggregate_id=draft.id,
            payload=payload,
            payload_schema_digest=self.contracts.schema_digest(event_type),
            occurred_at=occurred_at,
        )
