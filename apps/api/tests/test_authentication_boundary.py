from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticatedPrincipal,
    AuthenticationAssurance,
    ExecutionContext,
    TenantSelector,
    TrustedWorkloadPrincipal,
)
from creative_marketer.identity.application.errors import (
    AuthenticationUnavailable,
    Unauthenticated,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus
from creative_marketer.infrastructure.authentication import (
    DevelopmentAuthenticationAdapter,
    UnavailableAuthenticationAdapter,
)
from creative_marketer_api.config import Settings
from creative_marketer_api.main import create_app


@pytest.mark.asyncio
async def test_development_authenticator_returns_minimal_principal() -> None:
    principal = await DevelopmentAuthenticationAdapter().authenticate("https://idp|OpaqueSubject")
    assert principal.issuer == "https://idp"
    assert principal.subject == "OpaqueSubject"
    assert principal.authentication_method == "development"
    assert not hasattr(principal, "token")
    with pytest.raises(Unauthenticated):
        await DevelopmentAuthenticationAdapter().authenticate("invalid")


@pytest.mark.asyncio
async def test_unavailable_authenticator_fails_closed() -> None:
    with pytest.raises(AuthenticationUnavailable):
        await UnavailableAuthenticationAdapter().authenticate("anything")
    app = create_app(
        Settings(
            app_env="production",
            database_url="postgresql+psycopg://unused:unused@localhost/unused",
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/me", headers={"Authorization": "Bearer opaque"})
    assert response.status_code == 503


def test_trusted_identity_types_are_immutable_and_future_safe() -> None:
    now = datetime.now(UTC)
    user_id, tenant_id = uuid4(), uuid4()
    principal = AuthenticatedPrincipal("issuer", "Subject", now, "mfa", "high")
    workload = TrustedWorkloadPrincipal("workload-issuer", "service", now, "high")
    selector = TenantSelector(tenant_id)
    context = ExecutionContext(
        tenant_id=tenant_id,
        actor=Actor(ActorKind.USER, user_id),
        user_id=user_id,
        membership_role=MembershipRole.MEMBER,
        membership_status=MembershipStatus.ACTIVE,
        environment="test",
        authentication=AuthenticationAssurance(now, "mfa", "high"),
    )
    with pytest.raises(FrozenInstanceError):
        principal.subject = "forged"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        workload.subject = "browser"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        selector.tenant_id = uuid4()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        context.user_id = uuid4()  # type: ignore[misc]
    assert context.tenant_context().tenant_id == tenant_id
    assert {kind.value for kind in ActorKind} == {"user", "workload", "system", "agent"}
