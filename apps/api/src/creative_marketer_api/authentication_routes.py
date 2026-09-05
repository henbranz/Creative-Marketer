from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from creative_marketer.audit.domain import AuditOutcome
from creative_marketer.audit.identity import IdentityAuditService
from creative_marketer.identity.application.authentication import (
    AuthenticatedPrincipal,
    AuthenticationPort,
    TenantSelector,
)
from creative_marketer.identity.application.errors import (
    AuthenticationUnavailable,
    MembershipInactive,
    TenantAccessDenied,
    TenantSuspended,
    Unauthenticated,
    UnknownExternalIdentity,
    UserDisabled,
)
from creative_marketer.identity.application.identity_resolution import (
    ResolveAuthenticatedUser,
    ResolveTenantExecutionContext,
)
from creative_marketer.identity.application.ports import UnitOfWorkFactory


class CurrentActorResponse(BaseModel):
    actor_kind: str
    user_id: UUID


class ExecutionContextResponse(BaseModel):
    tenant_id: UUID
    actor_kind: str
    user_id: UUID
    membership_role: str
    correlation_id: UUID


def create_authentication_router(
    authenticator: AuthenticationPort,
    uow_factory: UnitOfWorkFactory,
    environment: str,
    audit: IdentityAuditService,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["identity"])

    async def authenticate(
        authorization: Annotated[str | None, Header()] = None,
    ) -> AuthenticatedPrincipal:
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="authentication required")
        try:
            return await authenticator.authenticate(authorization.removeprefix("Bearer "))
        except AuthenticationUnavailable as error:
            await audit.anonymous_event(
                action="authentication.failed",
                outcome=AuditOutcome.ERROR,
                correlation_id=uuid4(),
                environment=environment,
                reason_code=error.code,
            )
            raise HTTPException(status_code=503, detail=error.code) from error
        except Unauthenticated as error:
            await audit.anonymous_event(
                action="authentication.failed",
                outcome=AuditOutcome.DENIED,
                correlation_id=uuid4(),
                environment=environment,
                reason_code=error.code,
            )
            raise HTTPException(status_code=401, detail=error.code) from error

    @router.get("/me", response_model=CurrentActorResponse)
    async def get_current_actor(
        principal: Annotated[AuthenticatedPrincipal, Depends(authenticate)],
    ) -> CurrentActorResponse:
        try:
            user = await ResolveAuthenticatedUser(uow_factory, audit)(principal, environment)
        except (UnknownExternalIdentity, UserDisabled) as error:
            raise HTTPException(status_code=401, detail="identity_not_recognized") from error
        return CurrentActorResponse(actor_kind="user", user_id=user.id)

    @router.get(
        "/tenants/{tenant_id}/context",
        response_model=ExecutionContextResponse,
    )
    async def get_tenant_context(
        tenant_id: UUID,
        principal: Annotated[AuthenticatedPrincipal, Depends(authenticate)],
        correlation_header: Annotated[UUID | None, Header(alias="X-Correlation-ID")] = None,
    ) -> ExecutionContextResponse:
        try:
            context = await ResolveTenantExecutionContext(uow_factory, audit)(
                principal,
                TenantSelector(tenant_id),
                environment,
                correlation_header or uuid4(),
            )
        except (UnknownExternalIdentity, UserDisabled) as error:
            raise HTTPException(status_code=401, detail="identity_not_recognized") from error
        except (TenantAccessDenied, MembershipInactive) as error:
            raise HTTPException(status_code=403, detail="tenant_access_denied") from error
        except TenantSuspended as error:
            raise HTTPException(status_code=403, detail=error.code) from error
        return ExecutionContextResponse(
            tenant_id=context.tenant_id,
            actor_kind=context.actor.kind.value,
            user_id=context.user_id,
            membership_role=context.membership_role.value,
            correlation_id=context.correlation_id,
        )

    return router
