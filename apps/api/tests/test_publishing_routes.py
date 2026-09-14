# mypy: disable-error-code="arg-type,no-untyped-def,no-untyped-call"

from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from creative_marketer.identity.domain import MembershipRole
from creative_marketer.publishing.provider import FakeBehavior
from creative_marketer_api.publishing_routes import (
    PublicationDraftWrite,
    SocialAccountWrite,
    create_publishing_router,
)
from tests.test_publishing_application import stack


def endpoint(router, path: str, method: str):
    return next(
        route.endpoint for route in router.routes if route.path == path and method in route.methods
    )


@pytest.mark.asyncio
async def test_publishing_routes_expose_fake_lifecycle() -> None:
    context, service, repository, provider, _ = stack()
    router = create_publishing_router(None, None, service, "test", None)
    account = next(iter(repository.accounts.values()))
    accounts = await endpoint(router, "/v1/social/accounts", "GET")(context)
    assert accounts[0].provider == "fake"
    extra_account = await endpoint(router, "/v1/social/accounts", "POST")(
        SocialAccountWrite(
            platform="facebook",
            display_name="Fake Facebook",
            external_account_id="fake-facebook",
            username="@fake-facebook",
        ),
        context,
    )
    assert extra_account.platform == "facebook"
    created = await endpoint(router, "/v1/products/{product_id}/publication-drafts", "POST")(
        repository.authority.product_id,
        PublicationDraftWrite(
            final_creative_id=repository.authority.final_creative_id,
            social_account_id=account.id,
            caption="Exact reviewed caption",
            mode="POST_NOW",
        ),
        context,
    )
    approved = await endpoint(router, "/v1/publication-drafts/{draft_id}/approve", "POST")(
        created.id, context
    )
    listed_drafts = await endpoint(router, "/v1/products/{product_id}/publication-drafts", "GET")(
        repository.authority.product_id, context
    )
    fetched_draft = await endpoint(router, "/v1/publication-drafts/{draft_id}", "GET")(
        created.id, context
    )
    published = await endpoint(router, "/v1/publication-drafts/{draft_id}/execute", "POST")(
        created.id, context
    )
    publications = await endpoint(router, "/v1/products/{product_id}/publications", "GET")(
        repository.authority.product_id, context
    )

    assert approved.decision_state == "APPROVED"
    assert listed_drafts[0].id == fetched_draft.id == created.id
    assert published.status == "PUBLISHED"
    assert publications[0].canonical_permalink.startswith("https://social.invalid/")
    fetched_publication = await endpoint(router, "/v1/publications/{publication_id}", "GET")(
        publications[0].id, context
    )
    assert fetched_publication.id == publications[0].id
    assert provider.submit_count == 1


@pytest.mark.asyncio
async def test_real_provider_activation_and_execution_are_unavailable() -> None:
    context, service, _, _, _ = stack()
    router = create_publishing_router(None, None, service, "production", None)
    with pytest.raises(HTTPException) as account_error:
        await endpoint(router, "/v1/social/accounts", "POST")(
            SocialAccountWrite(
                platform="instagram",
                display_name="No real account",
                external_account_id="not-connected",
            ),
            context,
        )
    with pytest.raises(HTTPException) as execution_error:
        await endpoint(router, "/v1/publication-drafts/{draft_id}/execute", "POST")(
            uuid4(), context
        )
    assert account_error.value.status_code == 404
    assert execution_error.value.status_code == 404


@pytest.mark.asyncio
async def test_route_errors_use_stable_safe_codes() -> None:
    context, service, _, _, _ = stack()
    context = replace(context, membership_role=MembershipRole.MEMBER)
    router = create_publishing_router(None, None, service, "test", None)
    with pytest.raises(HTTPException) as caught:
        await endpoint(router, "/v1/social/accounts", "POST")(
            SocialAccountWrite(
                platform="instagram",
                display_name="Fake",
                external_account_id="fake",
            ),
            context,
        )
    assert caught.value.status_code == 403
    assert caught.value.detail == "publishing_permission_denied"
    assert "sensitive" not in caught.value.detail


@pytest.mark.asyncio
async def test_scheduled_cancel_reject_and_reconcile_routes() -> None:
    context, service, repository, _, _ = stack()
    router = create_publishing_router(None, None, service, "development", None)
    account = next(iter(repository.accounts.values()))
    scheduled = await endpoint(router, "/v1/products/{product_id}/publication-drafts", "POST")(
        repository.authority.product_id,
        PublicationDraftWrite(
            final_creative_id=repository.authority.final_creative_id,
            social_account_id=account.id,
            caption="Scheduled exact caption",
            mode="SCHEDULE",
            scheduled_at="2099-01-01T00:00:00Z",
            destination_url="https://example.test/product",
        ),
        context,
    )
    await endpoint(router, "/v1/publication-drafts/{draft_id}/approve", "POST")(
        scheduled.id, context
    )
    await endpoint(router, "/v1/publication-drafts/{draft_id}/execute", "POST")(
        scheduled.id, context
    )
    cancelled = await endpoint(router, "/v1/publication-drafts/{draft_id}/cancel", "POST")(
        scheduled.id, context
    )
    assert cancelled.status == "CANCELLED"

    context2, service2, repository2, _, _ = stack(behavior=FakeBehavior.OUTCOME_UNKNOWN)
    router2 = create_publishing_router(None, None, service2, "test", None)
    account2 = next(iter(repository2.accounts.values()))
    draft = await endpoint(router2, "/v1/products/{product_id}/publication-drafts", "POST")(
        repository2.authority.product_id,
        PublicationDraftWrite(
            final_creative_id=repository2.authority.final_creative_id,
            social_account_id=account2.id,
            caption="Unknown then reconciled",
            mode="POST_NOW",
        ),
        context2,
    )
    await endpoint(router2, "/v1/publication-drafts/{draft_id}/approve", "POST")(draft.id, context2)
    await endpoint(router2, "/v1/publication-drafts/{draft_id}/execute", "POST")(draft.id, context2)
    reconciled = await endpoint(router2, "/v1/publication-drafts/{draft_id}/reconcile", "POST")(
        draft.id, context2
    )
    assert reconciled.status == "PUBLISHED"

    context3, service3, repository3, _, _ = stack()
    router3 = create_publishing_router(None, None, service3, "test", None)
    account3 = next(iter(repository3.accounts.values()))
    rejected_draft = await endpoint(
        router3, "/v1/products/{product_id}/publication-drafts", "POST"
    )(
        repository3.authority.product_id,
        PublicationDraftWrite(
            final_creative_id=repository3.authority.final_creative_id,
            social_account_id=account3.id,
            caption="Rejected caption",
            mode="POST_NOW",
        ),
        context3,
    )
    rejected = await endpoint(router3, "/v1/publication-drafts/{draft_id}/reject", "POST")(
        rejected_draft.id, context3
    )
    assert rejected.decision_state == "REJECTED"


def test_schedule_request_validation_is_exact() -> None:
    with pytest.raises(ValueError):
        PublicationDraftWrite(
            final_creative_id=uuid4(),
            social_account_id=uuid4(),
            caption="Invalid schedule",
            mode="SCHEDULE",
        )


@pytest.mark.asyncio
async def test_route_failures_map_to_safe_not_found_and_conflict_codes() -> None:
    context, service, repository, _, _ = stack()
    router = create_publishing_router(None, None, service, "test", None)
    account = next(iter(repository.accounts.values()))
    with pytest.raises(HTTPException) as create_error:
        await endpoint(router, "/v1/products/{product_id}/publication-drafts", "POST")(
            uuid4(),
            PublicationDraftWrite(
                final_creative_id=repository.authority.final_creative_id,
                social_account_id=account.id,
                caption="Wrong product",
                mode="POST_NOW",
            ),
            context,
        )
    assert create_error.value.status_code == 409

    for path, method in (
        ("/v1/publication-drafts/{draft_id}", "GET"),
        ("/v1/publication-drafts/{draft_id}/reject", "POST"),
        ("/v1/publication-drafts/{draft_id}/execute", "POST"),
        ("/v1/publication-drafts/{draft_id}/cancel", "POST"),
        ("/v1/publication-drafts/{draft_id}/reconcile", "POST"),
        ("/v1/publications/{publication_id}", "GET"),
    ):
        with pytest.raises(HTTPException) as caught:
            await endpoint(router, path, method)(uuid4(), context)
        assert caught.value.status_code in {404, 409}
        assert isinstance(caught.value.detail, str)
