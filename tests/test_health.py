from httpx import ASGITransport, AsyncClient

from app.main import app


async def test_health_endpoint_reachable():
    """Endpoint should always respond, even if the DB is unreachable
    (ping() catches exceptions and returns False rather than raising)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("ok", "degraded")
    assert body["db"] in ("up", "down")
