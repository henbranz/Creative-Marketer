from dataclasses import dataclass

from creative_marketer.audit.identity import IdentityAuditService
from creative_marketer.identity.application.ports import UnitOfWorkFactory


@dataclass(frozen=True)
class IdentityStack:
    uow_factory: UnitOfWorkFactory
    audit: IdentityAuditService
