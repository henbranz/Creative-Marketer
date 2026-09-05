from dataclasses import dataclass
from uuid import UUID

from creative_marketer.identity.application.authentication import (
    Actor,
    ActorKind,
    AuthenticatedPrincipal,
    AuthenticationAssurance,
    ExecutionContext,
    TenantSelector,
)
from creative_marketer.identity.application.context import TenantContext
from creative_marketer.identity.application.errors import (
    MembershipInactive,
    TenantAccessDenied,
    TenantSuspended,
    UnknownExternalIdentity,
    UserDisabled,
)
from creative_marketer.identity.application.ports import UnitOfWork, UnitOfWorkFactory
from creative_marketer.identity.domain import (
    ExternalIdentity,
    ExternalIdentityStatus,
    MembershipStatus,
    TenantStatus,
    User,
    UserStatus,
)


async def _resolve_user(principal: AuthenticatedPrincipal, uow: UnitOfWork) -> User:
    identity = await uow.external_identities.get(principal.issuer, principal.subject)
    if identity is None or identity.status is not ExternalIdentityStatus.ACTIVE:
        raise UnknownExternalIdentity("external identity is unknown or inactive")
    user = await uow.users.get(identity.user_id)
    if user is None or user.status is not UserStatus.ACTIVE:
        raise UserDisabled("platform user is unavailable")
    return user


@dataclass(slots=True)
class LinkExternalIdentity:
    uow_factory: UnitOfWorkFactory

    async def __call__(self, user_id: UUID, issuer: str, subject: str) -> ExternalIdentity:
        identity = ExternalIdentity(user_id=user_id, issuer=issuer.strip(), subject=subject)
        async with self.uow_factory() as uow:
            user = await uow.users.get(user_id)
            if user is None or user.status is not UserStatus.ACTIVE:
                raise UserDisabled("platform user is unavailable")
            await uow.external_identities.add(identity)
            await uow.commit()
        return identity


@dataclass(slots=True)
class ResolveAuthenticatedUser:
    uow_factory: UnitOfWorkFactory

    async def __call__(self, principal: AuthenticatedPrincipal) -> User:
        async with self.uow_factory() as uow:
            return await _resolve_user(principal, uow)


@dataclass(slots=True)
class ResolveTenantExecutionContext:
    uow_factory: UnitOfWorkFactory

    async def __call__(
        self,
        principal: AuthenticatedPrincipal,
        selector: TenantSelector,
        environment: str,
        correlation_id: UUID,
    ) -> ExecutionContext:
        tentative_context = TenantContext(selector.tenant_id)
        async with self.uow_factory(tentative_context) as uow:
            user = await _resolve_user(principal, uow)
            membership = await uow.memberships.get(user.id)
            if membership is None:
                raise TenantAccessDenied("tenant access denied")
            if membership.status is not MembershipStatus.ACTIVE:
                raise MembershipInactive("tenant access denied")
            tenant = await uow.tenants.get(selector.tenant_id)
            if tenant is None:
                raise TenantAccessDenied("tenant access denied")
            if tenant.status is not TenantStatus.ACTIVE:
                raise TenantSuspended("tenant is suspended")
            return ExecutionContext(
                tenant_id=tenant.id,
                actor=Actor(ActorKind.USER, user.id),
                user_id=user.id,
                membership_role=membership.role,
                membership_status=membership.status,
                environment=environment,
                correlation_id=correlation_id,
                authentication=AuthenticationAssurance(
                    authenticated_at=principal.authenticated_at,
                    method=principal.authentication_method,
                    level=principal.assurance_level,
                    session_reference=principal.session_reference,
                ),
            )
