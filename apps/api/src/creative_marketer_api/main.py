from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from creative_marketer.audit.identity import IdentityAuditService
from creative_marketer.infrastructure.authentication import (
    DevelopmentAuthenticationAdapter,
    UnavailableAuthenticationAdapter,
)
from creative_marketer.infrastructure.database import (
    SqlAlchemyUnitOfWorkFactory,
    create_session_factory,
)
from creative_marketer.infrastructure.database.audit import PostgresStandaloneAuditWriter
from creative_marketer_api.authentication_routes import create_authentication_router
from creative_marketer_api.config import Settings, get_settings


def create_app(
    settings: Settings | None = None,
    identity_audit: IdentityAuditService | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    application = FastAPI(
        title="Creative Marketer API",
        version="0.1.0",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin).rstrip("/") for origin in resolved_settings.cors_origins],
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    session_factory = create_session_factory(str(resolved_settings.database_url))
    resolved_identity_audit = identity_audit or IdentityAuditService(
        PostgresStandaloneAuditWriter(session_factory),
        resolved_settings.audit_fingerprint_key.get_secret_value().encode(),
    )
    authenticator = (
        DevelopmentAuthenticationAdapter()
        if resolved_settings.dev_identity_enabled
        else UnavailableAuthenticationAdapter()
    )
    application.include_router(
        create_authentication_router(
            authenticator,
            SqlAlchemyUnitOfWorkFactory(session_factory),
            resolved_settings.app_env,
            resolved_identity_audit,
        )
    )

    @application.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "creative-marketer-api"}

    return application


app = create_app()
