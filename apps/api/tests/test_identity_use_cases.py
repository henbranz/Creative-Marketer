from types import TracebackType
from uuid import UUID, uuid4

import pytest

from creative_marketer.audit.application import AuditWriter
from creative_marketer.audit.domain import AuditRecord
from creative_marketer.identity.application.context import TenantContext
from creative_marketer.identity.application.errors import EntityNotFoundError
from creative_marketer.identity.application.ports import (
    ExternalIdentityRepository,
    MembershipRepository,
    TenantRepository,
    UnitOfWork,
    UserRepository,
)
from creative_marketer.identity.application.use_cases import (
    AddMembership,
    CreateTenant,
    CreateUser,
    GetCurrentTenantMembership,
    GetTenant,
    ListTenantMemberships,
)
from creative_marketer.identity.domain import (
    ExternalIdentity,
    Membership,
    MembershipRole,
    Tenant,
    User,
)


class TenantRepo:
    def __init__(self, values: dict[UUID, Tenant]) -> None:
        self.values = values

    async def add(self, tenant: Tenant) -> None:
        self.values[tenant.id] = tenant

    async def get(self, tenant_id: UUID) -> Tenant | None:
        return self.values.get(tenant_id)


class UserRepo:
    def __init__(self, values: dict[UUID, User]) -> None:
        self.values = values

    async def add(self, user: User) -> None:
        self.values[user.id] = user

    async def get(self, user_id: UUID) -> User | None:
        return self.values.get(user_id)


class MembershipRepo:
    def __init__(self, values: dict[tuple[UUID, UUID], Membership], tenant_id: UUID | None) -> None:
        self.values = values
        self.tenant_id = tenant_id

    async def add(self, membership: Membership) -> None:
        self.values[(membership.tenant_id, membership.user_id)] = membership

    async def list_for_tenant(self) -> list[Membership]:
        return [
            value for (tenant_id, _), value in self.values.items() if tenant_id == self.tenant_id
        ]

    async def get(self, user_id: UUID) -> Membership | None:
        if self.tenant_id is None:
            return None
        return self.values.get((self.tenant_id, user_id))


class ExternalIdentityRepo:
    async def add(self, identity: ExternalIdentity) -> None:
        del identity

    async def get(self, issuer: str, subject: str) -> ExternalIdentity | None:
        del issuer, subject
        return None


class FakeAuditWriter:
    async def append(self, record: AuditRecord) -> None:
        del record


class FakeUow:
    def __init__(self, factory: "FakeUowFactory", context: TenantContext | None) -> None:
        self.factory = factory
        self.tenants: TenantRepository = TenantRepo(factory.tenants)
        self.users: UserRepository = UserRepo(factory.users)
        self.memberships: MembershipRepository = MembershipRepo(
            factory.memberships, None if context is None else context.tenant_id
        )
        self.external_identities: ExternalIdentityRepository = ExternalIdentityRepo()
        self.audit: AuditWriter = FakeAuditWriter()
        self.committed = False

    async def __aenter__(self) -> "FakeUow":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True


class FakeUowFactory:
    def __init__(self) -> None:
        self.tenants: dict[UUID, Tenant] = {}
        self.users: dict[UUID, User] = {}
        self.memberships: dict[tuple[UUID, UUID], Membership] = {}

    def __call__(self, context: TenantContext | None = None) -> UnitOfWork:
        return FakeUow(self, context)


@pytest.mark.asyncio
async def test_identity_use_case_happy_path_and_scoping() -> None:
    factory = FakeUowFactory()
    tenant = await CreateTenant(factory)(" Example ", "EXAMPLE")
    other = await CreateTenant(factory)("Other", "other")
    user = await CreateUser(factory)("USER@example.com")
    context = TenantContext(tenant.id)
    membership = await AddMembership(factory)(context, user.id, MembershipRole.OWNER)
    factory.memberships[(other.id, user.id)] = Membership(other.id, user.id)

    assert tenant.name == "Example"
    assert tenant.slug == "example"
    assert (await GetTenant(factory)(context)).id == tenant.id
    assert await ListTenantMemberships(factory)(context) == [membership]
    assert (
        await GetCurrentTenantMembership(factory)(context, user.id)
    ).role is MembershipRole.OWNER


@pytest.mark.asyncio
async def test_missing_entities_are_reported() -> None:
    factory = FakeUowFactory()
    context = TenantContext(uuid4())

    with pytest.raises(EntityNotFoundError, match="tenant"):
        await GetTenant(factory)(context)
    with pytest.raises(EntityNotFoundError, match="tenant"):
        await AddMembership(factory)(context, uuid4(), MembershipRole.MEMBER)

    tenant = await CreateTenant(factory)("Tenant", "tenant")
    with pytest.raises(EntityNotFoundError, match="user"):
        await AddMembership(factory)(TenantContext(tenant.id), uuid4(), MembershipRole.MEMBER)
    with pytest.raises(EntityNotFoundError, match="membership"):
        await GetCurrentTenantMembership(factory)(TenantContext(tenant.id), uuid4())
