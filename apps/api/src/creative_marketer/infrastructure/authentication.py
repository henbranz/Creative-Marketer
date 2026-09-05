from datetime import UTC, datetime

from creative_marketer.identity.application.authentication import AuthenticatedPrincipal
from creative_marketer.identity.application.errors import (
    AuthenticationUnavailable,
    Unauthenticated,
)


class DevelopmentAuthenticationAdapter:
    """Explicitly enabled local adapter; credentials are `issuer|opaque-subject`."""

    async def authenticate(self, credential: str) -> AuthenticatedPrincipal:
        issuer, separator, subject = credential.partition("|")
        if not separator or not issuer.strip() or not subject:
            raise Unauthenticated("invalid development credential")
        return AuthenticatedPrincipal(
            issuer=issuer.strip(),
            subject=subject,
            authenticated_at=datetime.now(UTC),
            authentication_method="development",
            assurance_level="development-only",
        )


class UnavailableAuthenticationAdapter:
    async def authenticate(self, credential: str) -> AuthenticatedPrincipal:
        del credential
        raise AuthenticationUnavailable("no production authenticator is configured")
