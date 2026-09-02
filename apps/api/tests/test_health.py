import asyncio

from httpx import ASGITransport, AsyncClient

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
