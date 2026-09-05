from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4


class TenantStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class ExternalIdentityStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class MembershipRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class MembershipStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or "@" not in normalized:
        raise ValueError("email must contain a local part and domain")
    return normalized


@dataclass(frozen=True, slots=True)
class Tenant:
    name: str
    slug: str
    id: UUID = field(default_factory=uuid4)
    status: TenantStatus = TenantStatus.ACTIVE
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class User:
    email: str
    normalized_email: str
    id: UUID = field(default_factory=uuid4)
    status: UserStatus = UserStatus.ACTIVE
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @classmethod
    def create(cls, email: str) -> "User":
        stripped = email.strip()
        return cls(email=stripped, normalized_email=normalize_email(stripped), id=uuid4())


@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    user_id: UUID
    issuer: str
    subject: str
    id: UUID = field(default_factory=uuid4)
    status: ExternalIdentityStatus = ExternalIdentityStatus.ACTIVE
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class Membership:
    tenant_id: UUID
    user_id: UUID
    role: MembershipRole = MembershipRole.MEMBER
    status: MembershipStatus = MembershipStatus.ACTIVE
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
