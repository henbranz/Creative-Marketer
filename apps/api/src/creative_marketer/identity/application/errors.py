class IdentityError(Exception):
    """Base identity-context application error."""


class EntityNotFoundError(IdentityError):
    pass


class DuplicateEntityError(IdentityError):
    pass


class MissingTenantContextError(IdentityError):
    pass
