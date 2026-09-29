"""
Running behind a host's proxy: which forwarded headers are believed, and
what that changes in the request log, in redirects the app builds and in
whose allowance a write counts against.

The app is wrapped in uvicorn's own ProxyHeadersMiddleware, configured the
way `python -m app.serve` configures it, and reached from an address that
stands in for the host's proxy. No database needed: every request here is
answered before one is touched.
"""

import json
import logging

import pytest
from httpx import ASGITransport, AsyncClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app import serve
from app.config import Settings
from app.main import app, write_limiter
from app.observability import JsonFormatter, log

PROXY = ("10.1.2.3", 51000)  # where a host's router connects from
FORWARDED = {"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"}
# What the README gives FORWARDED_ALLOW_IPS on Render: the private networks,
# which the platform's router connects from and no internet client can.
PRIVATE_NETWORKS = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.setFormatter(JsonFormatter())
        self.lines: list[dict] = []

    def emit(self, record):
        self.lines.append(json.loads(self.format(record)))


@pytest.fixture
def captured():
    handler = _Capture()
    log.addHandler(handler)
    previous = log.level
    log.setLevel(logging.INFO)
    yield handler.lines
    log.removeHandler(handler)
    log.setLevel(previous)


def _behind_proxy(trusted: str) -> AsyncClient:
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=trusted)
    return AsyncClient(
        transport=ASGITransport(app=wrapped, client=PROXY),
        base_url="http://keel.example.com",
        follow_redirects=False,
    )


def _completed(lines) -> dict:
    (line,) = (entry for entry in lines if entry["event"] == "request.completed")
    return line


async def test_a_trusted_proxy_gives_the_log_the_real_client_and_scheme(captured):
    async with _behind_proxy(trusted=PRIVATE_NETWORKS) as client:
        await client.get("/openapi.json", headers=FORWARDED)
    line = _completed(captured)
    assert (line["client"], line["scheme"]) == ("203.0.113.7", "https")


async def test_an_untrusted_peers_forwarded_headers_are_ignored(captured):
    """The default trusts only 127.0.0.1, so a direct caller cannot claim another address."""
    async with _behind_proxy(trusted=Settings(_env_file=None).forwarded_allow_ips) as client:
        await client.get("/openapi.json", headers=FORWARDED)
    line = _completed(captured)
    assert (line["client"], line["scheme"]) == ("10.1.2.3", "http")


async def test_redirects_the_app_builds_use_the_forwarded_scheme(captured):
    """FastAPI's trailing-slash redirect is absolute, so it carries the scheme it saw."""
    async with _behind_proxy(trusted=PRIVATE_NETWORKS) as client:
        trusted = await client.get("/api/accounts/", headers=FORWARDED)
    # the default setting, which trusts only a proxy on the same machine
    async with _behind_proxy(trusted=Settings(_env_file=None).forwarded_allow_ips) as client:
        untrusted = await client.get("/api/accounts/", headers=FORWARDED)
    assert trusted.status_code == 307
    assert trusted.headers["location"] == "https://keel.example.com/api/accounts"
    assert untrusted.headers["location"] == "http://keel.example.com/api/accounts"


def test_the_start_command_trusts_the_configured_proxies(monkeypatch):
    seen = {}
    monkeypatch.setattr(serve.uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", PRIVATE_NETWORKS)
    monkeypatch.setattr(serve, "settings", Settings(_env_file=None))
    serve.serve()
    assert seen["proxy_headers"] is True
    assert seen["forwarded_allow_ips"] == PRIVATE_NETWORKS


# --- the write rate limit behind a proxy ---------------------------------------------


def _write(client, forwarded_for: str):
    """A write refused with a 400 before any database is touched; it still counts."""
    return client.post(
        "/api/transactions", json={"entries": []}, headers={"X-Forwarded-For": forwarded_for}
    )


async def test_two_forwarded_clients_get_separate_write_limits(monkeypatch):
    """Every request arrives from the proxy's address; the limit follows the forwarded one."""
    monkeypatch.setattr(write_limiter, "limit", 2)
    async with _behind_proxy(trusted=PRIVATE_NETWORKS) as client:
        one = [(await _write(client, "203.0.113.7")).status_code for _ in range(3)]
        other = [(await _write(client, "198.51.100.9")).status_code for _ in range(3)]
    assert one == [400, 400, 429]
    assert other == [400, 400, 429]


async def test_a_forged_forwarded_for_does_not_buy_a_fresh_allowance(monkeypatch):
    """
    A proxy that appends to X-Forwarded-For keeps whatever the client sent in
    front of the address it saw. Trusting only the proxy's networks, uvicorn
    reads from the right and stops at the first address that is not one of
    them: the one the proxy wrote.
    """
    monkeypatch.setattr(write_limiter, "limit", 2)
    async with _behind_proxy(trusted=PRIVATE_NETWORKS) as client:
        statuses = [
            (await _write(client, f"{forged}, 203.0.113.7")).status_code
            for forged in ("192.0.2.1", "192.0.2.2", "10.9.9.9")
        ]
    assert statuses == [400, 400, 429]
