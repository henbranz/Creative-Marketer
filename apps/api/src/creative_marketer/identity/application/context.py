from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Trusted, explicit tenant identity supplied by an authoritative boundary."""

    tenant_id: UUID
