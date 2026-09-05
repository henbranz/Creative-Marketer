import asyncio

from httpx import ASGITransport, AsyncClient, Response

from creative_marketer_api.config import Settings
from creative_marketer_api.main import create_app


def test_health_endpoint(settings: Settings) -> None:
    async def request_health() -> tuple[int, object]:
        transport = ASGITransport(app=create_app(settings))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        return response.status_code, response.json()

    status_code, body = asyncio.run(request_health())

    assert status_code == 200
    assert body == {"status": "ok", "service": "creative-marketer-api"}


def test_development_identity_routes_are_not_mounted_by_default(settings: Settings) -> None:
    async def request_identity_route() -> int:
        transport = ASGITransport(app=create_app(settings))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/development/tenant")
        return response.status_code

    assert asyncio.run(request_identity_route()) == 404


def test_liveness_and_readiness_are_distinct(settings: Settings) -> None:
    async def ready() -> None:
        return None

    async def unavailable() -> None:
        raise RuntimeError("database details must not escape")

    async def requests() -> tuple[Response, Response, Response]:
        healthy = ASGITransport(app=create_app(settings, readiness_probe=ready))
        unhealthy = ASGITransport(app=create_app(settings, readiness_probe=unavailable))
        async with AsyncClient(transport=healthy, base_url="http://test") as client:
            live = await client.get("/health/live")
            ready_response = await client.get("/health/ready")
        async with AsyncClient(transport=unhealthy, base_url="http://test") as client:
            failed = await client.get("/health/ready")
        return live, ready_response, failed

    live, ready_response, failed = asyncio.run(requests())
    assert live.status_code == 200
    assert ready_response.status_code == 200
    assert failed.status_code == 503
    assert failed.json() == {"detail": "service not ready"}
