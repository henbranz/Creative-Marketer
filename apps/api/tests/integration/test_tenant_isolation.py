from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from creative_marketer.identity.application.context import TenantContext
from creative_marketer.identity.application.errors import DuplicateEntityError
from creative_marketer.identity.application.identity_resolution import LinkExternalIdentity
from creative_marketer.identity.application.use_cases import (
    AddMembership,
    CreateTenant,
    CreateUser,
    GetCurrentTenantMembership,
    GetTenant,
    ListTenantMemberships,
)
from creative_marketer.identity.domain import MembershipRole
from creative_marketer.infrastructure.database.engine import create_session_factory
from creative_marketer.infrastructure.database.uow import SqlAlchemyUnitOfWorkFactory
from creative_marketer_api.config import Settings
from creative_marketer_api.main import create_app


async def seed(admin_engine: AsyncEngine) -> tuple[UUID, UUID, UUID]:
    tenant_a, tenant_b, user_id = uuid4(), uuid4(), uuid4()
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO identity.tenants (id, name, slug, status) VALUES "
                "(:a, 'Tenant A', :slug_a, 'active'), (:b, 'Tenant B', :slug_b, 'active')"
            ),
            {"a": tenant_a, "b": tenant_b, "slug_a": f"a-{tenant_a}", "slug_b": f"b-{tenant_b}"},
        )
        await connection.execute(
            text(
                "INSERT INTO identity.users "
                "(id, email, normalized_email, status) "
                "VALUES (:id, :email, :email, 'active')"
            ),
            {"id": user_id, "email": f"{user_id}@example.test"},
        )
        await connection.execute(
            text(
                "INSERT INTO identity.memberships (tenant_id, user_id, role, status) "
                "VALUES (:a, :user_id, 'owner', 'active'), "
                "(:b, :user_id, 'member', 'active')"
            ),
            {"a": tenant_a, "b": tenant_b, "user_id": user_id},
        )
    return tenant_a, tenant_b, user_id


async def set_tenant(connection: AsyncConnection, tenant_id: UUID) -> None:
    await connection.execute(
        text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_rls_isolates_reads_writes_and_deletes(
    admin_engine: AsyncEngine, runtime_engine: AsyncEngine
) -> None:
    tenant_a, tenant_b, _ = await seed(admin_engine)
    async with runtime_engine.begin() as connection:
        await set_tenant(connection, tenant_a)
        tenants = (
            (await connection.execute(text("SELECT id FROM identity.tenants"))).scalars().all()
        )
        memberships = (
            (await connection.execute(text("SELECT tenant_id FROM identity.memberships")))
            .scalars()
            .all()
        )
        updated = await connection.execute(
            text("UPDATE identity.memberships SET role='admin' WHERE tenant_id=:tenant"),
            {"tenant": tenant_b},
        )
        deleted = await connection.execute(
            text("DELETE FROM identity.memberships WHERE tenant_id=:tenant"),
            {"tenant": tenant_b},
        )
    assert tenants == [tenant_a]
    assert memberships == [tenant_a]
    assert updated.rowcount == 0
    assert deleted.rowcount == 0

    with pytest.raises(DBAPIError):
        async with runtime_engine.begin() as connection:
            await set_tenant(connection, tenant_a)
            await connection.execute(
                text(
                    "INSERT INTO identity.memberships (tenant_id, user_id, role, status) "
                    "VALUES (:tenant, :user, 'member', 'active')"
                ),
                {"tenant": tenant_b, "user": uuid4()},
            )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_missing_invalid_context_and_pool_reuse_fail_closed(
    admin_engine: AsyncEngine, runtime_engine: AsyncEngine
) -> None:
    tenant_a, tenant_b, _ = await seed(admin_engine)
    async with runtime_engine.begin() as connection:
        await set_tenant(connection, tenant_a)
        assert await connection.scalar(text("SELECT count(*) FROM identity.memberships")) == 1

    async with runtime_engine.begin() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM identity.memberships")) == 0
        await set_tenant(connection, tenant_b)
        assert await connection.scalar(text("SELECT count(*) FROM identity.memberships")) == 1

    async with runtime_engine.begin() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM identity.memberships")) == 0

    with pytest.raises(DBAPIError):
        async with runtime_engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.current_tenant_id', 'invalid', true)")
            )
            await connection.execute(text("SELECT * FROM identity.memberships"))


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_database_constraints_and_multi_tenant_user(
    admin_engine: AsyncEngine, runtime_engine: AsyncEngine
) -> None:
    tenant_a, tenant_b, user_id = await seed(admin_engine)
    for tenant_id in (tenant_a, tenant_b):
        async with runtime_engine.begin() as connection:
            await set_tenant(connection, tenant_id)
            rows = (
                (
                    await connection.execute(
                        text("SELECT tenant_id, user_id FROM identity.memberships")
                    )
                )
                .tuples()
                .all()
            )
            assert rows == [(tenant_id, user_id)]

    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO identity.memberships (tenant_id, user_id, role, status) "
                    "VALUES (:tenant, :user, 'member', 'active')"
                ),
                {"tenant": tenant_a, "user": user_id},
            )

    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO identity.memberships (tenant_id, user_id, role, status) "
                    "VALUES (:tenant, :user, 'member', 'active')"
                ),
                {"tenant": uuid4(), "user": user_id},
            )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_roles_and_failed_transaction_rollback(
    admin_engine: AsyncEngine, runtime_engine: AsyncEngine
) -> None:
    tenant_a, _, _ = await seed(admin_engine)
    async with admin_engine.connect() as connection:
        role = (
            await connection.execute(
                text(
                    "SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls "
                    "FROM pg_roles WHERE rolname='creative_marketer_runtime'"
                )
            )
        ).one()
        owner = await connection.scalar(
            text(
                "SELECT tableowner FROM pg_tables "
                "WHERE schemaname='identity' AND tablename='memberships'"
            )
        )
    assert role == (False, False, False, False, False)
    assert owner != "creative_marketer_runtime"

    new_user = uuid4()
    with pytest.raises(IntegrityError):
        async with runtime_engine.begin() as connection:
            await set_tenant(connection, tenant_a)
            await connection.execute(
                text(
                    "INSERT INTO identity.users (id, email, normalized_email, status) "
                    "VALUES (:id, :email, :email, 'active')"
                ),
                {"id": new_user, "email": f"{new_user}@example.test"},
            )
            await connection.execute(
                text(
                    "INSERT INTO identity.memberships (tenant_id, user_id, role, status) "
                    "VALUES (:tenant, :user, 'invalid-role', 'active')"
                ),
                {"tenant": tenant_a, "user": new_user},
            )

    async with admin_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM identity.users WHERE id=:id"), {"id": new_user}
            )
            == 0
        )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_use_cases_repositories_and_development_delivery(
    admin_engine: AsyncEngine, runtime_database_url: str
) -> None:
    factory = SqlAlchemyUnitOfWorkFactory(create_session_factory(runtime_database_url))
    tenant = await CreateTenant(factory)("Tenant", "tenant")
    user = await CreateUser(factory)("Person@Example.Test")
    context = TenantContext(tenant.id)
    membership = await AddMembership(factory)(context, user.id, MembershipRole.OWNER)
    await LinkExternalIdentity(factory)(user.id, "https://dev.example", "subject-1")

    assert (await GetTenant(factory)(context)).id == tenant.id
    assert await ListTenantMemberships(factory)(context) == [membership]
    assert (await GetCurrentTenantMembership(factory)(context, user.id)).user_id == user.id
    with pytest.raises(DuplicateEntityError, match="membership"):
        await AddMembership(factory)(context, user.id, MembershipRole.MEMBER)
    with pytest.raises(DuplicateEntityError, match="tenant"):
        await CreateTenant(factory)("Duplicate", "tenant")
    with pytest.raises(DuplicateEntityError, match="user"):
        await CreateUser(factory)("person@example.test")

    settings = Settings(
        app_env="test",
        database_url=runtime_database_url,
        dev_identity_enabled=True,
    )
    transport = ASGITransport(app=create_app(settings))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/v1/tenants/{tenant.id}/context",
            headers={
                "Authorization": "Bearer https://dev.example|subject-1",
            },
        )
        me = await client.get(
            "/v1/me",
            headers={"Authorization": "Bearer https://dev.example|subject-1"},
        )
    assert response.status_code == 200
    assert response.json()["membership_role"] == "owner"
    assert response.json()["user_id"] == str(user.id)
    assert me.json() == {"actor_kind": "user", "user_id": str(user.id)}
