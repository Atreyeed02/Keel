import json
import logging

from httpx import ASGITransport, AsyncClient

import app.db.engine as engine_module
from app.config import Settings
from app.main import app
from app.observability import JsonFormatter, log


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


class _Unreachable:
    """An engine whose every connection fails, with the password in the error."""

    def connect(self):
        raise TypeError("connect() got an unexpected keyword argument 'channel_binding' s3cr@t")


async def test_a_failed_ping_is_logged_without_the_password(monkeypatch):
    """A bare "degraded" says nothing; the log line says why, password masked."""
    lines: list[str] = []
    handler = logging.Handler()
    handler.setFormatter(JsonFormatter())
    handler.emit = lambda record: lines.append(handler.format(record))
    log.addHandler(handler)
    monkeypatch.setattr(engine_module, "engine", _Unreachable())
    monkeypatch.setattr(
        engine_module,
        "settings",
        Settings(_env_file=None, database_url="postgres://u:s3cr%40t@h/k"),
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")
    finally:
        log.removeHandler(handler)

    assert response.json() == {"status": "degraded", "db": "down"}
    [failed] = [json.loads(line) for line in lines if '"db.ping_failed"' in line]
    assert failed["level"] == "WARNING"
    assert failed["error"] == "TypeError"
    assert "channel_binding" in failed["detail"]
    assert "***" in failed["detail"]
    assert not any("s3cr" in line for line in lines)
