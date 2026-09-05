from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.responses import Response

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
from creative_marketer.observability.configuration import (
    ObservabilityConfiguration,
    build_runtime,
)
from creative_marketer.observability.logging import configure_structured_logging, correlation_scope
from creative_marketer.observability.ports import NullTelemetry, OperationalTelemetry
from creative_marketer.observability.runtime import ObservabilityRuntime
from creative_marketer_api.authentication_routes import create_authentication_router
from creative_marketer_api.config import Settings, get_settings


def create_app(
    settings: Settings | None = None,
    identity_audit: IdentityAuditService | None = None,
    observability: ObservabilityRuntime | None = None,
    readiness_probe: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    runtime = observability or build_runtime(
        ObservabilityConfiguration(
            mode=resolved_settings.otel_mode,
            service_name="creative-marketer-api",
            service_version="0.1.0",
            environment=resolved_settings.app_env,
            instance_id=resolved_settings.service_instance_id,
            otlp_endpoint=(
                str(resolved_settings.otel_exporter_otlp_endpoint)
                if resolved_settings.otel_exporter_otlp_endpoint
                else None
            ),
            sample_ratio=resolved_settings.otel_trace_sample_ratio,
        )
    )
    telemetry: OperationalTelemetry = runtime or NullTelemetry()
    configure_structured_logging(
        "creative-marketer-api", resolved_settings.app_env, resolved_settings.log_level
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if runtime is not None:
            runtime.shutdown(timeout_millis=5000)

    application = FastAPI(
        title="Creative Marketer API",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin).rstrip("/") for origin in resolved_settings.cors_origins],
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    session_factory = create_session_factory(str(resolved_settings.database_url))

    async def database_readiness() -> None:
        with telemetry.span("db.health"):
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))

    probe = readiness_probe or database_readiness

    @application.middleware("http")
    async def safe_http_observability(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = uuid4()
        with (
            correlation_scope(correlation_id),
            telemetry.span(
                "http.request",
                {
                    "correlation_id": str(correlation_id),
                    "http.request.method": request.method,
                },
            ) as span,
        ):
            try:
                response = await call_next(request)
            except Exception:
                span.record_error("HTTP_UNHANDLED_ERROR")
                telemetry.count(
                    "http.server.requests",
                    attributes={"http.request.method": request.method, "result": "error"},
                )
                raise
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            span.set_attribute("url.route", route_path)
            span.set_attribute("http.response.status_code", response.status_code)
            if response.status_code >= 500:
                span.record_error("HTTP_SERVER_ERROR")
            telemetry.count(
                "http.server.requests",
                attributes={"http.request.method": request.method, "url.route": route_path},
            )
            response.headers["X-Correlation-ID"] = str(correlation_id)
            return response

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

    @application.get("/health/live", tags=["system"])
    async def liveness() -> dict[str, str]:
        return {"status": "ok", "service": "creative-marketer-api"}

    @application.get("/health/ready", tags=["system"])
    async def readiness() -> dict[str, str]:
        try:
            await probe()
        except Exception as error:
            raise HTTPException(status_code=503, detail="service not ready") from error
        return {"status": "ready", "database": "ok"}

    @application.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return await liveness()

    return application


app = create_app()
