from types import TracebackType
from typing import Protocol
from uuid import UUID

from creative_marketer.identity.application.context import TenantContext
from creative_marketer.identity.domain import ExternalIdentity, Membership, Tenant, User


class TenantRepository(Protocol):
    async def add(self, tenant: Tenant) -> None: ...
    async def get(self, tenant_id: UUID) -> Tenant | None: ...


class UserRepository(Protocol):
    async def add(self, user: User) -> None: ...
    async def get(self, user_id: UUID) -> User | None: ...


class ExternalIdentityRepository(Protocol):
    async def add(self, identity: ExternalIdentity) -> None: ...
    async def get(self, issuer: str, subject: str) -> ExternalIdentity | None: ...


class MembershipRepository(Protocol):
    async def add(self, membership: Membership) -> None: ...
    async def list_for_tenant(self) -> list[Membership]: ...
    async def get(self, user_id: UUID) -> Membership | None: ...


class UnitOfWork(Protocol):
    tenants: TenantRepository
    users: UserRepository
    external_identities: ExternalIdentityRepository
    memberships: MembershipRepository

    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self, context: TenantContext | None = None) -> UnitOfWork: ...
