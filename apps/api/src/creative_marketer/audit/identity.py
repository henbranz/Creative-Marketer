from dataclasses import dataclass
from uuid import UUID

from creative_marketer.audit.application import StandaloneAuditWriter
from creative_marketer.audit.builders import platform_audit
from creative_marketer.audit.domain import AuditActorKind, AuditOutcome
from creative_marketer.audit.safety import principal_fingerprint, safe_metadata
from creative_marketer.identity.application.authentication import AuthenticatedPrincipal


@dataclass(slots=True)
class IdentityAuditService:
    writer: StandaloneAuditWriter
    fingerprint_key: bytes

    async def principal_event(
        self,
        principal: AuthenticatedPrincipal,
        *,
        action: str,
        outcome: AuditOutcome,
        correlation_id: UUID,
        environment: str,
        reason_code: str | None = None,
    ) -> None:
        await self.writer.append(
            platform_audit(
                actor_kind=AuditActorKind.EXTERNAL_PRINCIPAL,
                actor_id=principal_fingerprint(
                    self.fingerprint_key, principal.issuer, principal.subject
                ),
                action=action,
                outcome=outcome,
                reason_code=reason_code,
                correlation_id=correlation_id,
                environment=environment,
                metadata=safe_metadata({"issuer": principal.issuer}),
            )
        )

    async def anonymous_event(
        self,
        *,
        action: str,
        outcome: AuditOutcome,
        correlation_id: UUID,
        environment: str,
        reason_code: str,
    ) -> None:
        await self.writer.append(
            platform_audit(
                actor_kind=AuditActorKind.ANONYMOUS,
                actor_id=None,
                action=action,
                outcome=outcome,
                reason_code=reason_code,
                correlation_id=correlation_id,
                environment=environment,
            )
        )
