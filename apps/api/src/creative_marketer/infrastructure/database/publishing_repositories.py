from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.infrastructure.database.assembly_schema import (
    final_creative_decisions,
    final_creatives,
)
from creative_marketer.infrastructure.database.catalog_schema import assets
from creative_marketer.publishing.application import PublicationDraftRecord
from creative_marketer.publishing.domain import (
    FinalCreativeAuthority,
    Publication,
    PublicationApprovalBinding,
    PublicationDecision,
    PublicationDecisionState,
    PublicationDraft,
    PublicationJob,
    PublicationMode,
    PublicationStatus,
    SocialAccount,
    SocialAccountStatus,
    SocialPlatform,
)

from .publishing_schema import (
    publication_decisions,
    publication_drafts,
    publication_jobs,
    publications,
    social_accounts,
)


def _account(row: Mapping[str, Any]) -> SocialAccount:
    return SocialAccount(
        row["tenant_id"],
        SocialPlatform(row["platform"]),
        row["display_name"],
        row["external_account_id"],
        row["provider"],
        row["username"],
        row["account_type"],
        SocialAccountStatus(row["status"]),
        row["capabilities"] or {},
        row["id"],
        row["created_at"],
        row["updated_at"],
    )


def _draft(row: Mapping[str, Any]) -> PublicationDraft:
    return PublicationDraft(
        row["tenant_id"],
        row["product_id"],
        row["final_creative_id"],
        row["final_creative_digest"],
        row["output_asset_id"],
        row["output_asset_digest"],
        SocialPlatform(row["platform"]),
        row["social_account_id"],
        row["external_destination_id"],
        row["caption"],
        PublicationMode(row["mode"]),
        row["created_by_user_id"],
        row["semantic_digest"],
        row["title"],
        tuple(row["hashtags"] or ()),
        row["destination_url"],
        row["scheduled_at"],
        row["platform_settings"] or {},
        row["id"],
        row["schema_version"],
        row["created_at"],
    )


def _decision(row: Mapping[str, Any]) -> PublicationDecision:
    binding = PublicationApprovalBinding(
        row["tenant_id"],
        row["publication_draft_id"],
        row["publication_draft_digest"],
        row["final_creative_digest"],
        row["output_asset_digest"],
        SocialPlatform(row["platform"]),
        row["social_account_id"],
        row["external_destination_id"],
        row["caption_digest"],
        row["platform_settings_digest"],
        row["scheduled_at"],
        row["action_digest"],
    )
    return PublicationDecision(
        row["tenant_id"],
        binding,
        PublicationDecisionState(row["state"]),
        row["decided_by_user_id"],
        row["id"],
        row["created_at"],
    )


def _job(row: Mapping[str, Any]) -> PublicationJob:
    return PublicationJob(
        row["tenant_id"],
        row["publication_draft_id"],
        row["operation_id"],
        PublicationStatus(row["status"]),
        row["created_by_user_id"],
        row["id"],
        row["failure_code"],
        row["external_operation_id"],
        row["created_at"],
        row["updated_at"],
    )


def _publication(row: Mapping[str, Any]) -> Publication:
    return Publication(
        row["tenant_id"],
        row["product_id"],
        row["publication_draft_id"],
        row["final_creative_id"],
        row["output_asset_id"],
        SocialPlatform(row["platform"]),
        row["social_account_id"],
        row["external_post_id"],
        row["provider"],
        row["connector_version"],
        row["request_digest"],
        PublicationStatus(row["status"]),
        row["semantic_digest"],
        row["submitted_at"],
        row["external_operation_id"],
        row["canonical_permalink"],
        row["published_at"],
        row["response_metadata_digest"],
        row["id"],
        row["created_at"],
    )


class SqlAlchemyPublishingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def final_creative_authority(
        self, final_creative_id: UUID
    ) -> FinalCreativeAuthority | None:
        creative = (
            (
                await self._session.execute(
                    select(final_creatives).where(final_creatives.c.id == final_creative_id)
                )
            )
            .mappings()
            .first()
        )
        if creative is None:
            return None
        decision = (
            (
                await self._session.execute(
                    select(final_creative_decisions)
                    .where(final_creative_decisions.c.final_creative_id == final_creative_id)
                    .order_by(
                        final_creative_decisions.c.created_at.desc(),
                        final_creative_decisions.c.id.desc(),
                    )
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        asset_kind = await self._session.scalar(
            select(assets.c.kind).where(assets.c.id == creative["output_asset_id"])
        )
        return FinalCreativeAuthority(
            creative["tenant_id"],
            creative["product_id"],
            creative["id"],
            creative["semantic_digest"],
            creative["output_asset_id"],
            creative["output_asset_digest"],
            asset_kind or "video",
            creative["duration_ms"],
            creative["width"],
            creative["height"],
            decision["id"] if decision else None,
            decision["state"] if decision else None,
        )

    async def add_account(self, account: SocialAccount) -> None:
        await self._session.execute(
            insert(social_accounts).values(
                id=account.id,
                tenant_id=account.tenant_id,
                platform=account.platform.value,
                display_name=account.display_name,
                external_account_id=account.external_account_id,
                username=account.username,
                account_type=account.account_type,
                status=account.status.value,
                capabilities=dict(account.capabilities),
                provider=account.provider,
                created_at=account.created_at,
                updated_at=account.updated_at,
            )
        )

    async def list_accounts(self) -> tuple[SocialAccount, ...]:
        rows = (
            await self._session.execute(
                select(social_accounts).order_by(
                    social_accounts.c.platform, social_accounts.c.display_name
                )
            )
        ).mappings()
        return tuple(_account(cast(Mapping[str, Any], row)) for row in rows)

    async def get_account(
        self, account_id: UUID, *, for_update: bool = False
    ) -> SocialAccount | None:
        query = select(social_accounts).where(social_accounts.c.id == account_id)
        if for_update:
            query = query.with_for_update()
        row = (await self._session.execute(query)).mappings().first()
        return _account(cast(Mapping[str, Any], row)) if row else None

    async def add_draft(self, draft: PublicationDraft) -> None:
        await self._session.execute(
            insert(publication_drafts).values(
                id=draft.id,
                tenant_id=draft.tenant_id,
                product_id=draft.product_id,
                final_creative_id=draft.final_creative_id,
                final_creative_digest=draft.final_creative_digest,
                output_asset_id=draft.output_asset_id,
                output_asset_digest=draft.output_asset_digest,
                platform=draft.platform.value,
                social_account_id=draft.social_account_id,
                external_destination_id=draft.external_destination_id,
                caption=draft.caption,
                title=draft.title,
                hashtags=list(draft.hashtags),
                destination_url=draft.destination_url,
                mode=draft.mode.value,
                scheduled_at=draft.scheduled_at,
                platform_settings=dict(draft.platform_settings),
                schema_version=draft.schema_version,
                semantic_digest=draft.semantic_digest,
                created_by_user_id=draft.created_by_user_id,
                created_at=draft.created_at,
            )
        )

    async def get_draft(
        self, draft_id: UUID, *, for_update: bool = False
    ) -> PublicationDraftRecord | None:
        if for_update:
            # PublicationDraft is immutable and the runtime deliberately has no UPDATE
            # grant, so PostgreSQL rejects SELECT FOR UPDATE. A transaction-scoped lock
            # serializes approval/job decisions without weakening that privilege boundary.
            await self._session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:draft_id, 0))"),
                {"draft_id": str(draft_id)},
            )
        query = select(publication_drafts).where(publication_drafts.c.id == draft_id)
        row = (await self._session.execute(query)).mappings().first()
        return None if row is None else await self._record(cast(Mapping[str, Any], row))

    async def list_drafts(self, product_id: UUID) -> tuple[PublicationDraftRecord, ...]:
        rows = list(
            (
                await self._session.execute(
                    select(publication_drafts)
                    .where(publication_drafts.c.product_id == product_id)
                    .order_by(publication_drafts.c.created_at.desc())
                )
            ).mappings()
        )
        return tuple([await self._record(cast(Mapping[str, Any], row)) for row in rows])

    async def _record(self, row: Mapping[str, Any]) -> PublicationDraftRecord:
        decision_row = (
            (
                await self._session.execute(
                    select(publication_decisions)
                    .where(publication_decisions.c.publication_draft_id == row["id"])
                    .order_by(
                        publication_decisions.c.created_at.desc(), publication_decisions.c.id.desc()
                    )
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        job_row = (
            (
                await self._session.execute(
                    select(publication_jobs).where(
                        publication_jobs.c.publication_draft_id == row["id"]
                    )
                )
            )
            .mappings()
            .first()
        )
        return PublicationDraftRecord(
            _draft(row),
            _decision(cast(Mapping[str, Any], decision_row)) if decision_row else None,
            _job(cast(Mapping[str, Any], job_row)) if job_row else None,
        )

    async def add_decision(self, decision: PublicationDecision) -> None:
        b = decision.binding
        await self._session.execute(
            insert(publication_decisions).values(
                id=decision.id,
                tenant_id=decision.tenant_id,
                publication_draft_id=b.publication_draft_id,
                publication_draft_digest=b.publication_draft_digest,
                final_creative_digest=b.final_creative_digest,
                output_asset_digest=b.output_asset_digest,
                platform=b.platform.value,
                social_account_id=b.social_account_id,
                external_destination_id=b.external_destination_id,
                caption_digest=b.caption_digest,
                platform_settings_digest=b.platform_settings_digest,
                scheduled_at=b.scheduled_at,
                action_digest=b.action_digest,
                state=decision.state.value,
                decided_by_user_id=decision.decided_by_user_id,
                created_at=decision.created_at,
            )
        )

    async def add_job(self, job: PublicationJob) -> None:
        await self._session.execute(
            insert(publication_jobs).values(
                id=job.id,
                tenant_id=job.tenant_id,
                publication_draft_id=job.publication_draft_id,
                operation_id=job.operation_id,
                status=job.status.value,
                failure_code=job.failure_code,
                external_operation_id=job.external_operation_id,
                created_by_user_id=job.created_by_user_id,
                created_at=job.created_at,
                updated_at=job.updated_at,
            )
        )

    async def update_job(self, job: PublicationJob) -> None:
        await self._session.execute(
            update(publication_jobs)
            .where(publication_jobs.c.id == job.id)
            .values(
                status=job.status.value,
                failure_code=job.failure_code,
                external_operation_id=job.external_operation_id,
                updated_at=job.updated_at,
            )
        )

    async def add_publication(self, publication: Publication) -> None:
        await self._session.execute(
            insert(publications).values(
                id=publication.id,
                tenant_id=publication.tenant_id,
                product_id=publication.product_id,
                publication_draft_id=publication.publication_draft_id,
                final_creative_id=publication.final_creative_id,
                output_asset_id=publication.output_asset_id,
                platform=publication.platform.value,
                social_account_id=publication.social_account_id,
                external_post_id=publication.external_post_id,
                external_operation_id=publication.external_operation_id,
                canonical_permalink=publication.canonical_permalink,
                provider=publication.provider,
                connector_version=publication.connector_version,
                request_digest=publication.request_digest,
                response_metadata_digest=publication.response_metadata_digest,
                status=publication.status.value,
                submitted_at=publication.submitted_at,
                published_at=publication.published_at,
                semantic_digest=publication.semantic_digest,
                created_at=publication.created_at,
            )
        )

    async def publication_for_draft(self, draft_id: UUID) -> Publication | None:
        row = (
            (
                await self._session.execute(
                    select(publications).where(publications.c.publication_draft_id == draft_id)
                )
            )
            .mappings()
            .first()
        )
        return _publication(cast(Mapping[str, Any], row)) if row else None

    async def list_publications(self, product_id: UUID) -> tuple[Publication, ...]:
        rows = (
            await self._session.execute(
                select(publications)
                .where(publications.c.product_id == product_id)
                .order_by(publications.c.created_at.desc())
            )
        ).mappings()
        return tuple(_publication(cast(Mapping[str, Any], row)) for row in rows)

    async def get_publication(self, publication_id: UUID) -> Publication | None:
        row = (
            (
                await self._session.execute(
                    select(publications).where(publications.c.id == publication_id)
                )
            )
            .mappings()
            .first()
        )
        return _publication(cast(Mapping[str, Any], row)) if row else None
