from dataclasses import dataclass
from uuid import UUID, uuid4

from creative_marketer.audit.builders import platform_audit, tenant_audit
from creative_marketer.audit.domain import AuditActorKind, AuditOutcome
from creative_marketer.audit.identity import IdentityAuditService
from creative_marketer.audit.safety import safe_metadata
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
    DuplicateEntityError,
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
    audit: IdentityAuditService

    async def __call__(self, user_id: UUID, issuer: str, subject: str) -> ExternalIdentity:
        identity = ExternalIdentity(user_id=user_id, issuer=issuer.strip(), subject=subject)
        correlation_id = uuid4()
        try:
            async with self.uow_factory() as uow:
                user = await uow.users.get(user_id)
                if user is None or user.status is not UserStatus.ACTIVE:
                    raise UserDisabled("platform user is unavailable")
                await uow.external_identities.add(identity)
                await uow.audit.append(
                    platform_audit(
                        actor_kind=AuditActorKind.SYSTEM,
                        actor_id="internal-identity-linker",
                        action="identity.external_identity.linked",
                        outcome=AuditOutcome.SUCCESS,
                        correlation_id=correlation_id,
                        environment="internal",
                        resource_type="external_identity",
                        resource_id=str(identity.id),
                    )
                )
                await uow.commit()
        except (UserDisabled, DuplicateEntityError) as error:
            await self.audit.anonymous_event(
                action="identity.external_identity.link_failed",
                outcome=AuditOutcome.DENIED,
                correlation_id=correlation_id,
                environment="internal",
                reason_code=getattr(error, "code", "identity_link_rejected"),
            )
            raise
        return identity


@dataclass(slots=True)
class ResolveAuthenticatedUser:
    uow_factory: UnitOfWorkFactory
    audit: IdentityAuditService

    async def __call__(
        self,
        principal: AuthenticatedPrincipal,
        environment: str = "internal",
        correlation_id: UUID | None = None,
    ) -> User:
        resolved_correlation = correlation_id or uuid4()
        try:
            async with self.uow_factory() as uow:
                user = await _resolve_user(principal, uow)
                await uow.audit.append(
                    platform_audit(
                        actor_kind=AuditActorKind.USER,
                        actor_id=str(user.id),
                        action="authentication.succeeded",
                        outcome=AuditOutcome.SUCCESS,
                        correlation_id=resolved_correlation,
                        environment=environment,
                    )
                )
                await uow.commit()
                return user
        except (UnknownExternalIdentity, UserDisabled) as error:
            await self.audit.principal_event(
                principal,
                action="authentication.failed",
                outcome=AuditOutcome.DENIED,
                correlation_id=resolved_correlation,
                environment=environment,
                reason_code=error.code,
            )
            raise


@dataclass(slots=True)
class ResolveTenantExecutionContext:
    uow_factory: UnitOfWorkFactory
    audit: IdentityAuditService

    async def __call__(
        self,
        principal: AuthenticatedPrincipal,
        selector: TenantSelector,
        environment: str,
        correlation_id: UUID,
    ) -> ExecutionContext:
        tentative_context = TenantContext(selector.tenant_id)
        try:
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
                context = ExecutionContext(
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
                await uow.audit.append(
                    tenant_audit(
                        context,
                        action="identity.tenant_context.resolved",
                        outcome=AuditOutcome.SUCCESS,
                        metadata=safe_metadata({"membership_role": membership.role.value}),
                    )
                )
                await uow.commit()
                return context
        except (
            UnknownExternalIdentity,
            UserDisabled,
            TenantAccessDenied,
            MembershipInactive,
            TenantSuspended,
        ) as error:
            await self.audit.principal_event(
                principal,
                action="identity.tenant_context.denied",
                outcome=AuditOutcome.DENIED,
                correlation_id=correlation_id,
                environment=environment,
                reason_code=error.code,
            )
            raise
