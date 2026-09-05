from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from creative_marketer.infrastructure.database import (
    SqlAlchemyUnitOfWorkFactory,
    create_session_factory,
)
from creative_marketer_api.config import Settings, get_settings
from creative_marketer_api.identity_routes import create_identity_router


def create_app(settings: Settings | None = None) -> FastAPI:
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

    if resolved_settings.dev_identity_enabled:
        session_factory = create_session_factory(str(resolved_settings.database_url))
        application.include_router(
            create_identity_router(SqlAlchemyUnitOfWorkFactory(session_factory))
        )

    @application.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "creative-marketer-api"}

    return application


app = create_app()
