from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from creative_marketer.identity.application.context import TenantContext
from creative_marketer.identity.application.errors import DuplicateEntityError
from creative_marketer.identity.domain import (
    ExternalIdentity,
    ExternalIdentityStatus,
    Membership,
    MembershipRole,
    MembershipStatus,
    Tenant,
    TenantStatus,
    User,
    UserStatus,
)
from creative_marketer.infrastructure.database.schema import (
    external_identities,
    memberships,
    tenants,
    users,
)


def _tenant(row: object) -> Tenant:
    data = row._mapping  # type: ignore[attr-defined]
    return Tenant(
        id=data["id"],
        name=data["name"],
        slug=data["slug"],
        status=TenantStatus(data["status"]),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


def _user(row: object) -> User:
    data = row._mapping  # type: ignore[attr-defined]
    return User(
        id=data["id"],
        email=data["email"],
        normalized_email=data["normalized_email"],
        status=UserStatus(data["status"]),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


def _external_identity(row: object) -> ExternalIdentity:
    data = row._mapping  # type: ignore[attr-defined]
    return ExternalIdentity(
        id=data["id"],
        user_id=data["user_id"],
        issuer=data["issuer"],
        subject=data["subject"],
        status=ExternalIdentityStatus(data["status"]),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


def _membership(row: object) -> Membership:
    data = row._mapping  # type: ignore[attr-defined]
    return Membership(
        tenant_id=data["tenant_id"],
        user_id=data["user_id"],
        role=MembershipRole(data["role"]),
        status=MembershipStatus(data["status"]),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


class SqlAlchemyTenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, tenant: Tenant) -> None:
        try:
            await self._session.execute(
                insert(tenants).values(
                    id=tenant.id,
                    name=tenant.name,
                    slug=tenant.slug,
                    status=tenant.status.value,
                    created_at=tenant.created_at,
                    updated_at=tenant.updated_at,
                )
            )
        except IntegrityError as error:
            raise DuplicateEntityError("tenant already exists") from error

    async def get(self, tenant_id: UUID) -> Tenant | None:
        row = (
            await self._session.execute(select(tenants).where(tenants.c.id == tenant_id))
        ).first()
        return None if row is None else _tenant(row)


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user: User) -> None:
        try:
            await self._session.execute(
                insert(users).values(
                    id=user.id,
                    email=user.email,
                    normalized_email=user.normalized_email,
                    status=user.status.value,
                    created_at=user.created_at,
                    updated_at=user.updated_at,
                )
            )
        except IntegrityError as error:
            raise DuplicateEntityError("user already exists") from error

    async def get(self, user_id: UUID) -> User | None:
        row = (await self._session.execute(select(users).where(users.c.id == user_id))).first()
        return None if row is None else _user(row)


class SqlAlchemyExternalIdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, identity: ExternalIdentity) -> None:
        try:
            await self._session.execute(
                insert(external_identities).values(
                    id=identity.id,
                    user_id=identity.user_id,
                    issuer=identity.issuer,
                    subject=identity.subject,
                    status=identity.status.value,
                    created_at=identity.created_at,
                    updated_at=identity.updated_at,
                )
            )
        except IntegrityError as error:
            raise DuplicateEntityError("external identity already exists or is invalid") from error

    async def get(self, issuer: str, subject: str) -> ExternalIdentity | None:
        row = (
            await self._session.execute(
                select(external_identities).where(
                    external_identities.c.issuer == issuer,
                    external_identities.c.subject == subject,
                )
            )
        ).first()
        return None if row is None else _external_identity(row)


class SqlAlchemyMembershipRepository:
    def __init__(self, session: AsyncSession, context: TenantContext | None) -> None:
        self._session = session
        self._context = context

    async def add(self, membership: Membership) -> None:
        try:
            await self._session.execute(
                insert(memberships).values(
                    tenant_id=membership.tenant_id,
                    user_id=membership.user_id,
                    role=membership.role.value,
                    status=membership.status.value,
                    created_at=membership.created_at,
                    updated_at=membership.updated_at,
                )
            )
        except IntegrityError as error:
            raise DuplicateEntityError("membership already exists or is invalid") from error

    async def list_for_tenant(self) -> list[Membership]:
        result = await self._session.execute(select(memberships).order_by(memberships.c.created_at))
        return [_membership(row) for row in result]

    async def get(self, user_id: UUID) -> Membership | None:
        query = select(memberships).where(memberships.c.user_id == user_id)
        row = (await self._session.execute(query)).first()
        return None if row is None else _membership(row)
