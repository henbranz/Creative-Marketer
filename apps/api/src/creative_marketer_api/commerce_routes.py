from datetime import datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from creative_marketer.agent_runtime.application import AgentRunService
from creative_marketer.agent_runtime.domain import AgentRuntimeError
from creative_marketer.approval_governance.application import (
    ApprovalUnitOfWorkFactory,
    DecideApproval,
)
from creative_marketer.approval_governance.domain import (
    ApprovalError,
    HumanDecision,
)
from creative_marketer.audit.identity import IdentityAuditService
from creative_marketer.commerce.application import CommerceService
from creative_marketer.commerce.domain import CommerceError, SyncType
from creative_marketer.commerce.provider import FakeCommerceProvider
from creative_marketer.commerce.workflow_execution import (
    CommerceActionView,
    CommerceGovernedExecutionService,
)
from creative_marketer.identity.application.authentication import (
    AuthenticatedPrincipal,
    AuthenticationPort,
    ExecutionContext,
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
from creative_marketer.identity.application.identity_resolution import ResolveTenantExecutionContext
from creative_marketer.identity.application.ports import UnitOfWorkFactory

from .research_routes import AgentRunResponse, _agent_run


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FakeConnectionCreate(Contract):
    display_name: str = Field(default="Fake Store", min_length=1, max_length=200)


class MappingCreate(Contract):
    product_id: UUID
    connection_id: UUID
    external_product_id: str = Field(min_length=1, max_length=255)
    external_variant_id: str | None = Field(default=None, max_length=255)


class SyncRequest(Contract):
    sync_type: SyncType | None = None
    idempotency_key: str = Field(min_length=1, max_length=128)


class ApprovalDecisionInput(Contract):
    decision: HumanDecision
    reason_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]{0,99}$")


class AnalyzeRequest(Contract):
    idempotency_key: str = Field(min_length=1, max_length=128)


class ConnectionResponse(Contract):
    id: UUID
    provider: str
    display_name: str
    safe_store_identifier: str
    status: str
    capabilities: list[str]
    is_fake: bool
    created_at: datetime


def _connection(value: Any) -> ConnectionResponse:
    return ConnectionResponse(
        id=value.id,
        provider=value.provider,
        display_name=value.display_name,
        safe_store_identifier=value.safe_store_identifier,
        status=value.status.value,
        capabilities=list(value.capabilities),
        is_fake=value.provider == "fake",
        created_at=value.created_at,
    )


def create_commerce_router(
    authenticator: AuthenticationPort,
    identity_uow: UnitOfWorkFactory,
    runtime: AgentRunService,
    service: CommerceService,
    provider: FakeCommerceProvider,
    environment: str,
    audit: IdentityAuditService,
    governance: CommerceGovernedExecutionService | None = None,
    approval_uow: ApprovalUnitOfWorkFactory | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["commerce"])

    async def execution_context(
        authorization: Annotated[str | None, Header()] = None,
        tenant_id: Annotated[UUID | None, Header(alias="X-Tenant-ID")] = None,
        correlation_id: Annotated[UUID | None, Header(alias="X-Correlation-ID")] = None,
    ) -> ExecutionContext:
        if authorization is None or not authorization.startswith("Bearer ") or tenant_id is None:
            raise HTTPException(
                status_code=401, detail="authentication and tenant selection required"
            )
        try:
            principal: AuthenticatedPrincipal = await authenticator.authenticate(
                authorization.removeprefix("Bearer ")
            )
            return await ResolveTenantExecutionContext(identity_uow, audit)(
                principal, TenantSelector(tenant_id), environment, correlation_id or uuid4()
            )
        except AuthenticationUnavailable as error:
            raise HTTPException(status_code=503, detail=error.code) from error
        except (Unauthenticated, UnknownExternalIdentity, UserDisabled) as error:
            raise HTTPException(status_code=401, detail="identity_not_recognized") from error
        except (TenantAccessDenied, MembershipInactive, TenantSuspended) as error:
            raise HTTPException(status_code=403, detail="tenant_access_denied") from error

    Context = Annotated[ExecutionContext, Depends(execution_context)]

    @router.get("/commerce/connections", response_model=list[ConnectionResponse])
    async def connections(ctx: Context) -> list[ConnectionResponse]:
        return [_connection(v) for v in await service.list_connections(ctx)]

    @router.post(
        "/commerce/connections/fake",
        response_model=ConnectionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_fake(value: FakeConnectionCreate, ctx: Context) -> ConnectionResponse:
        try:
            result = await service.create_fake_connection(ctx, value.display_name)
            provider.seed_store(result.external_store_id)
            return _connection(result)
        except CommerceError as error:
            raise HTTPException(status_code=409, detail=error.code.lower()) from error

    @router.post("/commerce/mappings", status_code=status.HTTP_201_CREATED)
    async def create_mapping(value: MappingCreate, ctx: Context) -> dict[str, Any]:
        try:
            result = await service.map_product(
                ctx,
                product_id=value.product_id,
                connection_id=value.connection_id,
                external_product_id=value.external_product_id,
                external_variant_id=value.external_variant_id,
            )
            return {
                "id": result.id,
                "product_id": result.product_id,
                "connection_id": result.connection_id,
                "external_product_id": result.external_product_id,
                "external_variant_id": result.external_variant_id,
                "status": result.status.value,
                "created_at": result.created_at,
            }
        except CommerceError as error:
            raise HTTPException(status_code=409, detail=error.code.lower()) from error

    @router.post("/commerce/connections/{connection_id}/sync", status_code=status.HTTP_202_ACCEPTED)
    async def sync(connection_id: UUID, value: SyncRequest, ctx: Context) -> dict[str, Any]:
        try:
            if governance is None:
                raise CommerceError("commerce workflow starter unavailable")
            sync_types: tuple[SyncType, ...]
            if value.sync_type in {None, SyncType.ALL}:
                sync_types = (SyncType.CATALOG, SyncType.INVENTORY, SyncType.ORDERS)
            else:
                assert value.sync_type is not None
                sync_types = (value.sync_type,)
            summary = await governance.request_sync(
                ctx, connection_id, sync_types, value.idempotency_key
            )
            return {
                "sync_request_id": summary.request_id,
                "status": summary.status,
                "safe_failure_code": summary.safe_failure_code,
            }
        except (CommerceError, ValueError) as error:
            raise HTTPException(
                status_code=409, detail=getattr(error, "code", "commerce_sync_failed").lower()
            ) from error

    @router.get("/products/{product_id}/commerce")
    async def workspace(product_id: UUID, ctx: Context) -> dict[str, Any]:
        value = await service.workspace(ctx, product_id)
        mapping = value["mapping"]
        sync_view = (
            await governance.store.latest_sync_view(ctx.tenant_id, mapping.connection_id)
            if governance is not None and mapping is not None
            else None
        )
        action_views: dict[UUID, CommerceActionView] = {}
        if governance is not None:
            for proposal in value["proposals"]:
                action_views[proposal.id] = await governance.store.action_view(
                    ctx.tenant_id, proposal.id
                )
        return {
            "mapping": None
            if mapping is None
            else {
                "id": mapping.id,
                "connection_id": mapping.connection_id,
                "external_product_id": mapping.external_product_id,
                "external_variant_id": mapping.external_variant_id,
                "status": mapping.status.value,
            },
            "sync_status": None
            if sync_view is None
            else {
                "request_id": sync_view.request_id,
                "status": sync_view.status,
                "safe_failure_code": sync_view.safe_failure_code,
            },
            "inventory": [
                {
                    "id": v.id,
                    "external_variant_id": v.external_variant_id,
                    "sku": v.sku,
                    "available_quantity": v.available_quantity,
                    "indicator": v.indicator().value,
                    "captured_at": v.captured_at,
                    "source": "Observed",
                }
                for v in value["inventory"]
            ],
            "orders": [
                {
                    "id": v.id,
                    "order_reference": v.order_reference,
                    "external_order_id": v.external_order_id,
                    "currency": v.currency,
                    "total": str(v.total),
                    "payment_state": v.financial_status.value,
                    "fulfillment_state": v.fulfillment_status.value,
                    "captured_at": v.captured_at,
                    "attributed": bool(v.attribution_code),
                    "source": "Observed",
                }
                for v in value["orders"]
            ],
            "proposals": [
                {
                    "id": v.id,
                    "action_type": v.action_type.value,
                    "external_product_id": v.external_product_id,
                    "external_variant_id": v.external_variant_id,
                    "external_order_id": v.external_order_id,
                    "exact_quantity": v.exact_quantity,
                    "exact_amount": str(v.exact_amount) if v.exact_amount is not None else None,
                    "currency": v.currency,
                    "reason": v.reason,
                    "risk_level": v.risk_level.value,
                    "semantic_digest": v.semantic_digest,
                    "approval_state": (
                        action_views[v.id].state if v.id in action_views else "NEEDS_APPROVAL"
                    ),
                    "job_status": (action_views[v.id].state if v.id in action_views else None),
                    "approval_request_id": (
                        action_views[v.id].approval_request_id if v.id in action_views else None
                    ),
                    "store": (action_views[v.id].store if v.id in action_views else "Fake Store"),
                    "sku_or_variant": (
                        action_views[v.id].sku_or_variant if v.id in action_views else None
                    ),
                    "current_quantity": (
                        action_views[v.id].current_quantity if v.id in action_views else None
                    ),
                    "order_reference": (
                        action_views[v.id].order_reference if v.id in action_views else None
                    ),
                    "result_ref": (action_views[v.id].result_ref if v.id in action_views else None),
                    "safe_failure_code": (
                        action_views[v.id].safe_failure_code if v.id in action_views else None
                    ),
                    "source": "AI Proposal",
                    "created_at": v.created_at,
                }
                for v in value["proposals"]
            ],
            "inventory_exceptions": list(value["inventory_exceptions"]),
            "order_exceptions": list(value["order_exceptions"]),
            "is_fake": True,
        }

    @router.post("/commerce/actions/{proposal_id}/request", status_code=status.HTTP_202_ACCEPTED)
    async def request_action(proposal_id: UUID, ctx: Context) -> dict[str, Any]:
        if governance is None:
            raise HTTPException(status_code=503, detail="commerce_worker_unavailable")
        try:
            value = await governance.request_action(ctx, proposal_id)
            return _action_view(value)
        except CommerceError as error:
            raise HTTPException(status_code=409, detail=error.code.lower()) from error

    @router.get("/commerce/actions/{proposal_id}")
    async def action_status(proposal_id: UUID, ctx: Context) -> dict[str, Any]:
        if governance is None:
            raise HTTPException(status_code=503, detail="commerce_worker_unavailable")
        try:
            return _action_view(await governance.store.action_view(ctx.tenant_id, proposal_id))
        except CommerceError as error:
            raise HTTPException(status_code=404, detail=error.code.lower()) from error

    @router.post("/approvals/{approval_id}/decision")
    async def decide_approval(
        approval_id: UUID, value: ApprovalDecisionInput, ctx: Context
    ) -> dict[str, Any]:
        if governance is None or approval_uow is None:
            raise HTTPException(status_code=503, detail="approval_service_unavailable")
        try:
            decision = await DecideApproval(approval_uow)(
                ctx, approval_id, value.decision, reason_code=value.reason_code
            )
            proposal_id = await governance.store.proposal_for_approval(ctx.tenant_id, approval_id)
            await governance.wake_action(ctx, proposal_id)
            return {
                "approval_request_id": approval_id,
                "decision": decision.decision.value,
                "proposal_id": proposal_id,
            }
        except (ApprovalError, CommerceError) as error:
            raise HTTPException(
                status_code=409, detail=getattr(error, "code", "approval_failed").lower()
            ) from error

    @router.post(
        "/products/{product_id}/commerce/analyze",
        response_model=AgentRunResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def analyze(product_id: UUID, value: AnalyzeRequest, ctx: Context) -> AgentRunResponse:
        try:
            return _agent_run(
                await runtime.request_commerce_operations(
                    ctx, product_id=product_id, idempotency_key=value.idempotency_key
                )
            )
        except (AgentRuntimeError, CommerceError, ValueError) as error:
            raise HTTPException(
                status_code=409,
                detail=getattr(error, "code", "commerce_analysis_not_ready").lower(),
            ) from error

    return router


def _action_view(value: CommerceActionView) -> dict[str, Any]:
    return {
        "proposal_id": value.proposal_id,
        "tool_key": value.tool_key,
        "risk_level": value.risk_level,
        "state": value.state,
        "operation_id": value.operation_id,
        "approval_request_id": value.approval_request_id,
        "result_ref": value.result_ref,
        "safe_failure_code": value.safe_failure_code,
        "store": value.store,
        "sku_or_variant": value.sku_or_variant,
        "current_quantity": value.current_quantity,
        "exact_quantity": value.exact_quantity,
        "order_reference": value.order_reference,
        "exact_amount": value.exact_amount,
        "currency": value.currency,
        "reason": value.reason,
    }
