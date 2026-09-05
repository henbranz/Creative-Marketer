import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from creative_marketer.identity.application.authentication import (
    AuthenticatedPrincipal,
    TenantSelector,
)
from creative_marketer.identity.application.context import TenantContext
from creative_marketer.identity.application.errors import (
    DuplicateEntityError,
    MembershipInactive,
    TenantAccessDenied,
    TenantSuspended,
    UnknownExternalIdentity,
    UserDisabled,
)
from creative_marketer.identity.application.identity_resolution import (
    LinkExternalIdentity,
    ResolveAuthenticatedUser,
    ResolveTenantExecutionContext,
)
from creative_marketer.identity.application.use_cases import (
    AddMembership,
    CreateTenant,
    CreateUser,
    GetTenant,
)
from creative_marketer.identity.domain import MembershipRole
from creative_marketer_api.config import Settings
from creative_marketer_api.main import create_app
from tests.integration.support import IdentityStack


def principal(issuer: str, subject: str) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(issuer, subject, datetime.now(UTC), "test", "verified")


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_external_identity_resolution_is_exact_and_never_email_based(
    admin_engine: AsyncEngine, identity_stack: IdentityStack
) -> None:
    factory, audit = identity_stack.uow_factory, identity_stack.audit
    first = await CreateUser(factory)("same@example.test")
    second = await CreateUser(factory)("other@example.test")
    first_identity = await LinkExternalIdentity(factory, audit)(
        first.id, "https://idp-a", "CaseSensitive"
    )
    await LinkExternalIdentity(factory, audit)(first.id, "https://idp-b", "second")

    assert (
        await ResolveAuthenticatedUser(factory, audit)(principal("https://idp-a", "CaseSensitive"))
    ).id == first.id
    assert (
        await ResolveAuthenticatedUser(factory, audit)(principal("https://idp-b", "second"))
    ).id == first.id
    with pytest.raises(UnknownExternalIdentity):
        await ResolveAuthenticatedUser(factory, audit)(principal("https://idp-a", "casesensitive"))
    with pytest.raises(UnknownExternalIdentity):
        await ResolveAuthenticatedUser(factory, audit)(principal("unknown", "same@example.test"))
    with pytest.raises(DuplicateEntityError):
        await LinkExternalIdentity(factory, audit)(
            second.id, first_identity.issuer, first_identity.subject
        )
    with pytest.raises(UserDisabled):
        await LinkExternalIdentity(factory, audit)(uuid4(), "https://idp", "missing-user")

    async with admin_engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM identity.users")) == 2
        assert (
            await connection.scalar(text("SELECT count(*) FROM identity.external_identities")) == 2
        )

    async with admin_engine.begin() as connection:
        await connection.execute(
            text("UPDATE identity.external_identities SET status='disabled' WHERE id=:id"),
            {"id": first_identity.id},
        )
    with pytest.raises(UnknownExternalIdentity):
        await ResolveAuthenticatedUser(factory, audit)(
            principal(first_identity.issuer, first_identity.subject)
        )

    async with admin_engine.connect() as connection:
        records = (
            (
                await connection.execute(
                    text(
                        "SELECT action, outcome, actor_kind, actor_id, reason_code, "
                        "safe_metadata::text AS metadata FROM audit.audit_records"
                    )
                )
            )
            .mappings()
            .all()
        )
    actions = [record["action"] for record in records]
    assert actions.count("identity.external_identity.linked") == 2
    assert actions.count("identity.external_identity.link_failed") == 2
    assert actions.count("authentication.succeeded") == 2
    assert actions.count("authentication.failed") == 3
    failed_auth = [record for record in records if record["action"] == "authentication.failed"]
    assert all(record["outcome"] == "denied" for record in failed_auth)
    assert all(record["actor_kind"] == "external_principal" for record in failed_auth)
    assert all(str(record["actor_id"]).startswith("hmac-sha256:") for record in failed_auth)
    serialized = json.dumps(records, default=str).lower()
    for sensitive_value in (
        "same@example.test",
        "other@example.test",
        "casesensitive",
        "missing-user",
    ):
        assert sensitive_value not in serialized


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_statuses_membership_and_tenant_selection_are_authoritative(
    admin_engine: AsyncEngine, identity_stack: IdentityStack
) -> None:
    factory, audit = identity_stack.uow_factory, identity_stack.audit
    tenant_a = await CreateTenant(factory)("Tenant A", "tenant-a")
    tenant_b = await CreateTenant(factory)("Tenant B", "tenant-b")
    user = await CreateUser(factory)("user@example.test")
    identity = await LinkExternalIdentity(factory, audit)(user.id, "https://idp", "subject")
    await AddMembership(factory)(TenantContext(tenant_a.id), user.id, MembershipRole.MEMBER)
    resolver = ResolveTenantExecutionContext(factory, audit)
    correlation = uuid4()

    context_a = await resolver(
        principal(identity.issuer, identity.subject),
        TenantSelector(tenant_a.id),
        "test",
        correlation,
    )
    assert context_a.tenant_id == tenant_a.id
    assert context_a.user_id == user.id
    assert context_a.membership_role is MembershipRole.MEMBER
    assert context_a.correlation_id == correlation
    assert (await GetTenant(factory)(context_a.tenant_context())).id == tenant_a.id
    with pytest.raises(TenantAccessDenied):
        await resolver(
            principal(identity.issuer, identity.subject),
            TenantSelector(tenant_b.id),
            "test",
            uuid4(),
        )

    await AddMembership(factory)(TenantContext(tenant_b.id), user.id, MembershipRole.ADMIN)
    context_b = await resolver(
        principal(identity.issuer, identity.subject), TenantSelector(tenant_b.id), "test", uuid4()
    )
    assert context_b.tenant_id == tenant_b.id
    assert context_b.membership_role is MembershipRole.ADMIN

    async with admin_engine.begin() as connection:
        await connection.execute(
            text("UPDATE identity.memberships SET status='inactive' WHERE tenant_id=:tenant"),
            {"tenant": tenant_a.id},
        )
        await connection.execute(
            text("UPDATE identity.tenants SET status='suspended' WHERE id=:tenant"),
            {"tenant": tenant_b.id},
        )
    with pytest.raises(MembershipInactive):
        await resolver(
            principal(identity.issuer, identity.subject),
            TenantSelector(tenant_a.id),
            "test",
            uuid4(),
        )
    with pytest.raises(TenantSuspended):
        await resolver(
            principal(identity.issuer, identity.subject),
            TenantSelector(tenant_b.id),
            "test",
            uuid4(),
        )

    async with admin_engine.begin() as connection:
        await connection.execute(
            text("UPDATE identity.users SET status='disabled' WHERE id=:user"), {"user": user.id}
        )
    with pytest.raises(UserDisabled):
        await ResolveAuthenticatedUser(factory, audit)(principal(identity.issuer, identity.subject))

    async with admin_engine.connect() as connection:
        records = (
            (
                await connection.execute(
                    text(
                        "SELECT scope_kind, tenant_id, actor_id, action, outcome, reason_code, "
                        "correlation_id, safe_metadata FROM audit.audit_records"
                    )
                )
            )
            .mappings()
            .all()
        )
    resolved = [
        record for record in records if record["action"] == "identity.tenant_context.resolved"
    ]
    assert {record["tenant_id"] for record in resolved} == {tenant_a.id, tenant_b.id}
    assert all(record["scope_kind"] == "tenant" for record in resolved)
    assert all(record["actor_id"] == str(user.id) for record in resolved)
    assert any(record["correlation_id"] == correlation for record in resolved)
    assert {record["safe_metadata"]["membership_role"] for record in resolved} == {
        "member",
        "admin",
    }
    denied_reasons = {
        record["reason_code"]
        for record in records
        if record["action"] == "identity.tenant_context.denied"
    }
    assert denied_reasons == {"tenant_access_denied", "membership_inactive", "tenant_suspended"}
    assert all(
        record["scope_kind"] == "platform"
        for record in records
        if record["action"] == "identity.tenant_context.denied"
    )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_http_input_cannot_forge_actor_role_tenant_or_agent_identity(
    admin_engine: AsyncEngine, runtime_database_url: str, identity_stack: IdentityStack
) -> None:
    factory, audit = identity_stack.uow_factory, identity_stack.audit
    allowed = await CreateTenant(factory)("Allowed", "allowed")
    denied = await CreateTenant(factory)("Denied", "denied")
    user = await CreateUser(factory)("member@example.test")
    other_id = uuid4()
    await LinkExternalIdentity(factory, audit)(user.id, "https://dev", "opaque")
    await AddMembership(factory)(TenantContext(allowed.id), user.id, MembershipRole.MEMBER)
    app = create_app(
        Settings(app_env="test", database_url=runtime_database_url, dev_identity_enabled=True)
    )
    transport = ASGITransport(app=app)
    forged = {
        "Authorization": "Bearer https://dev|opaque",
        "X-User-ID": str(other_id),
        "X-Actor-ID": str(other_id),
        "X-Role": "owner",
        "X-Agent-ID": str(uuid4()),
        "X-Agent-Version-ID": str(uuid4()),
        "X-Run-ID": str(uuid4()),
        "X-Tenant-ID": str(denied.id),
    }
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        valid = await client.get(
            f"/v1/tenants/{allowed.id}/context?user_id={other_id}&role=owner",
            headers=forged,
        )
        attack = await client.get(f"/v1/tenants/{denied.id}/context", headers=forged)
        unknown = await client.get(
            "/v1/me",
            headers={
                "Authorization": "Bearer https://dev|unknown",
                "X-Email": "member@example.test",
            },
        )
        missing = await client.get("/v1/me")
    assert valid.status_code == 200
    assert valid.json()["user_id"] == str(user.id)
    assert valid.json()["membership_role"] == "member"
    assert not ({"agent_id", "agent_version_id", "run_id"} & valid.json().keys())
    assert attack.status_code == 403
    assert unknown.status_code == 401
    assert missing.status_code == 401
