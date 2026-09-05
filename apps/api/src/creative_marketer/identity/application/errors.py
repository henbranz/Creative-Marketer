class IdentityError(Exception):
    """Base identity-context application error."""


class EntityNotFoundError(IdentityError):
    pass


class DuplicateEntityError(IdentityError):
    pass


class MissingTenantContextError(IdentityError):
    pass


class Unauthenticated(IdentityError):
    code = "unauthenticated"


class UnknownExternalIdentity(IdentityError):
    code = "unknown_external_identity"


class UserDisabled(IdentityError):
    code = "user_disabled"


class TenantAccessDenied(IdentityError):
    code = "tenant_access_denied"


class TenantSuspended(IdentityError):
    code = "tenant_suspended"


class MembershipInactive(IdentityError):
    code = "membership_inactive"


class AuthenticationUnavailable(IdentityError):
    code = "authentication_unavailable"
