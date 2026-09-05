from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from creative_marketer.identity.application.context import TenantContext
from creative_marketer.identity.application.errors import EntityNotFoundError
from creative_marketer.identity.application.ports import UnitOfWorkFactory
from creative_marketer.identity.application.use_cases import (
    GetCurrentTenantMembership,
    GetTenant,
    ListTenantMemberships,
)
from creative_marketer.identity.domain import MembershipRole, MembershipStatus, TenantStatus


class TenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    slug: str
    status: TenantStatus


class MembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    tenant_id: UUID
    user_id: UUID
    role: MembershipRole
    status: MembershipStatus


def create_identity_router(uow_factory: UnitOfWorkFactory) -> APIRouter:
    """Development-only proof adapter; TASK-003 will replace its identity source."""
    router = APIRouter(prefix="/development", tags=["development-identity"])

    def trusted_context(
        tenant_id: Annotated[UUID, Header(alias="X-Development-Tenant-ID")],
    ) -> TenantContext:
        return TenantContext(tenant_id)

    @router.get("/tenant", response_model=TenantResponse)
    async def get_tenant(
        context: Annotated[TenantContext, Depends(trusted_context)],
    ) -> TenantResponse:
        try:
            return TenantResponse.model_validate(await GetTenant(uow_factory)(context))
        except EntityNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/memberships", response_model=list[MembershipResponse])
    async def list_memberships(
        context: Annotated[TenantContext, Depends(trusted_context)],
    ) -> list[MembershipResponse]:
        values = await ListTenantMemberships(uow_factory)(context)
        return [MembershipResponse.model_validate(value) for value in values]

    @router.get("/membership", response_model=MembershipResponse)
    async def get_membership(
        context: Annotated[TenantContext, Depends(trusted_context)],
        user_id: Annotated[UUID, Header(alias="X-Development-User-ID")],
    ) -> MembershipResponse:
        try:
            value = await GetCurrentTenantMembership(uow_factory)(context, user_id)
            return MembershipResponse.model_validate(value)
        except EntityNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return router
