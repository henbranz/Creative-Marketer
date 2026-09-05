from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from creative_marketer.identity.application.context import TenantContext
from creative_marketer.identity.domain import MembershipRole, MembershipStatus


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    issuer: str
    subject: str
    authenticated_at: datetime
    authentication_method: str
    assurance_level: str
    session_reference: str | None = None


class AuthenticationPort(Protocol):
    async def authenticate(self, credential: str) -> AuthenticatedPrincipal: ...


@dataclass(frozen=True, slots=True)
class TrustedWorkloadPrincipal:
    issuer: str
    subject: str
    authenticated_at: datetime
    assurance_level: str


class WorkloadAuthenticationPort(Protocol):
    async def authenticate_workload(self, credential: str) -> TrustedWorkloadPrincipal: ...


class ActorKind(StrEnum):
    USER = "user"
    WORKLOAD = "workload"
    SYSTEM = "system"
    AGENT = "agent"


@dataclass(frozen=True, slots=True)
class Actor:
    kind: ActorKind
    id: UUID


@dataclass(frozen=True, slots=True)
class AuthenticationAssurance:
    authenticated_at: datetime
    method: str
    level: str
    session_reference: str | None = None


@dataclass(frozen=True, slots=True)
class TenantSelector:
    """Untrusted tenant selection; it carries no authority by itself."""

    tenant_id: UUID


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    tenant_id: UUID
    actor: Actor
    user_id: UUID
    membership_role: MembershipRole
    membership_status: MembershipStatus
    environment: str
    authentication: AuthenticationAssurance
    correlation_id: UUID = field(default_factory=uuid4)

    def tenant_context(self) -> TenantContext:
        return TenantContext(self.tenant_id)
