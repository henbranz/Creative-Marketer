import base64
import binascii
import json
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from creative_marketer.audit.identity import IdentityAuditService
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
from creative_marketer.knowledge.application import KnowledgeGraphProjector
from creative_marketer.knowledge.domain import KnowledgeNode, KnowledgeNodeRef


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RelationshipResponse(Contract):
    relationship_type: str
    target_node_type: str
    target_canonical_id: str


class KnowledgeNodeResponse(Contract):
    node_type: str
    canonical_id: str
    title: str
    status: str
    semantic_digest: str | None
    created_at: datetime
    updated_at: datetime
    properties: dict[str, Any]
    relationships: list[RelationshipResponse]


class KnowledgeEdgeResponse(Contract):
    source_node_type: str
    source_canonical_id: str
    target_node_type: str
    target_canonical_id: str
    relationship_type: str


class FullProjectionResponse(Contract):
    revision: int
    next_cursor: str
    nodes: list[KnowledgeNodeResponse]
    edges: list[KnowledgeEdgeResponse]


class DeletedNodeResponse(Contract):
    node_type: str
    canonical_id: str


class ProjectionChangeResponse(Contract):
    revision: int
    node: KnowledgeNodeResponse | None
    deleted_node: DeletedNodeResponse | None


class ProjectionChangesResponse(Contract):
    changes: list[ProjectionChangeResponse]
    next_cursor: str
    has_more: bool


def _node(value: KnowledgeNode) -> KnowledgeNodeResponse:
    return KnowledgeNodeResponse.model_validate(value.primitive())


def _deleted(value: KnowledgeNodeRef) -> DeletedNodeResponse:
    return DeletedNodeResponse(node_type=value.node_type.value, canonical_id=value.canonical_id)


def encode_cursor(tenant_id: UUID, revision: int) -> str:
    raw = json.dumps(
        {"v": 1, "tenant": str(tenant_id), "revision": revision},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: str | None, tenant_id: UUID) -> int:
    if value is None:
        return 0
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if payload != {
            "v": 1,
            "tenant": str(tenant_id),
            "revision": payload.get("revision"),
        }:
            raise ValueError
        revision = payload["revision"]
        if not isinstance(revision, int) or revision < 0:
            raise ValueError
        return revision
    except (
        AttributeError,
        binascii.Error,
        UnicodeDecodeError,
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
    ) as error:
        raise HTTPException(status_code=400, detail="invalid projection cursor") from error


def create_knowledge_router(
    authenticator: AuthenticationPort,
    identity_uow: UnitOfWorkFactory,
    projector: KnowledgeGraphProjector,
    environment: str,
    audit: IdentityAuditService,
) -> APIRouter:
    router = APIRouter(prefix="/v1/knowledge/projection", tags=["knowledge"])

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

    @router.get("", response_model=FullProjectionResponse)
    async def full_projection(ctx: Context) -> FullProjectionResponse:
        graph, revision = await projector.full(ctx)
        return FullProjectionResponse(
            revision=revision,
            next_cursor=encode_cursor(ctx.tenant_id, revision),
            nodes=[_node(node) for node in graph.nodes],
            edges=[
                KnowledgeEdgeResponse(
                    source_node_type=edge.source_node.node_type.value,
                    source_canonical_id=edge.source_node.canonical_id,
                    target_node_type=edge.target_node.node_type.value,
                    target_canonical_id=edge.target_node.canonical_id,
                    relationship_type=edge.relationship_type,
                )
                for edge in graph.edges
            ],
        )

    @router.get("/changes", response_model=ProjectionChangesResponse)
    async def projection_changes(
        ctx: Context,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 250,
    ) -> ProjectionChangesResponse:
        revision = decode_cursor(cursor, ctx.tenant_id)
        changes, next_revision, has_more = await projector.changes(
            ctx, cursor=revision, limit=limit
        )
        return ProjectionChangesResponse(
            changes=[
                ProjectionChangeResponse(
                    revision=item.revision,
                    node=_node(item.node) if item.node else None,
                    deleted_node=_deleted(item.deleted_node) if item.deleted_node else None,
                )
                for item in changes
            ],
            next_cursor=encode_cursor(ctx.tenant_id, next_revision),
            has_more=has_more,
        )

    return router
