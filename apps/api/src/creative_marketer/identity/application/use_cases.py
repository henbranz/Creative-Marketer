from dataclasses import dataclass
from uuid import UUID, uuid4

from creative_marketer.identity.application.context import TenantContext
from creative_marketer.identity.application.errors import EntityNotFoundError
from creative_marketer.identity.application.ports import UnitOfWorkFactory
from creative_marketer.identity.domain import Membership, MembershipRole, Tenant, User


@dataclass(slots=True)
class CreateTenant:
    uow_factory: UnitOfWorkFactory

    async def __call__(self, name: str, slug: str) -> Tenant:
        tenant = Tenant(id=uuid4(), name=name.strip(), slug=slug.strip().lower())
        async with self.uow_factory(TenantContext(tenant.id)) as uow:
            await uow.tenants.add(tenant)
            await uow.commit()
        return tenant


@dataclass(slots=True)
class CreateUser:
    uow_factory: UnitOfWorkFactory

    async def __call__(self, email: str) -> User:
        user = User.create(email)
        async with self.uow_factory() as uow:
            await uow.users.add(user)
            await uow.commit()
        return user


@dataclass(slots=True)
class AddMembership:
    uow_factory: UnitOfWorkFactory

    async def __call__(
        self, context: TenantContext, user_id: UUID, role: MembershipRole
    ) -> Membership:
        membership = Membership(tenant_id=context.tenant_id, user_id=user_id, role=role)
        async with self.uow_factory(context) as uow:
            if await uow.tenants.get(context.tenant_id) is None:
                raise EntityNotFoundError("tenant not found")
            if await uow.users.get(user_id) is None:
                raise EntityNotFoundError("user not found")
            await uow.memberships.add(membership)
            await uow.commit()
        return membership


@dataclass(slots=True)
class GetTenant:
    uow_factory: UnitOfWorkFactory

    async def __call__(self, context: TenantContext) -> Tenant:
        async with self.uow_factory(context) as uow:
            tenant = await uow.tenants.get(context.tenant_id)
            if tenant is None:
                raise EntityNotFoundError("tenant not found")
            return tenant


@dataclass(slots=True)
class ListTenantMemberships:
    uow_factory: UnitOfWorkFactory

    async def __call__(self, context: TenantContext) -> list[Membership]:
        async with self.uow_factory(context) as uow:
            return await uow.memberships.list_for_tenant()


@dataclass(slots=True)
class GetCurrentTenantMembership:
    uow_factory: UnitOfWorkFactory

    async def __call__(self, context: TenantContext, user_id: UUID) -> Membership:
        async with self.uow_factory(context) as uow:
            membership = await uow.memberships.get(user_id)
            if membership is None:
                raise EntityNotFoundError("membership not found")
            return membership
