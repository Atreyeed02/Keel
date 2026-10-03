"""
Running behind a host's proxy: which forwarded headers are believed, and
what that changes in the request log, in redirects the app builds and in
whose allowance a write counts against.

The app is wrapped in app/client_address.py's ClientAddressMiddleware, the
way app.main.served wraps it for `python -m app.serve`, and reached from an
address that stands in for the host's proxy. The Render cases replay the
chain observed on the live service: visitor -> Cloudflare -> Render's load
balancer -> Render's proxy on 127.0.0.1 -> Keel. No database needed: every
request here is answered before one is touched.
"""

import json
import logging

import pytest
from httpx import ASGITransport, AsyncClient

from app import main, serve
from app.client_address import ClientAddressMiddleware, cloudflare_client, is_cloudflare
from app.config import Settings
from app.main import app, write_limiter
from app.observability import JsonFormatter, log
from scripts.check_cloudflare_ranges import differences

PROXY = ("10.1.2.3", 51000)  # a host's router connecting from a private address
FORWARDED = {"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"}
PRIVATE_NETWORKS = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"

# What FORWARDED_ALLOW_IPS is on Render: its proxy, and the 10.x hop its load
# balancer appends.
RENDER_TRUSTED = "127.0.0.1," + PRIVATE_NETWORKS
RENDER_PROXY = ("127.0.0.1", 41000)
HEALTH_CHECK = ("10.237.26.210", 52000)
VISITOR = "203.0.113.50"
EDGE = "172.68.147.142"  # a Cloudflare edge, inside 172.64.0.0/13
RENDER_HOP = "10.204.7.31"


def _via_render(forwarded_for: str = f"{VISITOR}, {EDGE}, {RENDER_HOP}", **extra) -> dict:
    """The headers Render's proxy hands Keel for one visitor's request."""
    return {
        "X-Forwarded-For": forwarded_for,
        "X-Forwarded-Proto": "https",
        "CF-Connecting-IP": VISITOR,
        **extra,
    }


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


def _behind_proxy(trusted: str, peer=PROXY) -> AsyncClient:
    wrapped = ClientAddressMiddleware(app, trusted_hosts=trusted)
    return AsyncClient(
        transport=ASGITransport(app=wrapped, client=peer),
        base_url="http://keel.example.com",
        follow_redirects=False,
    )


def _completed(lines) -> dict:
    (line,) = (entry for entry in lines if entry["event"] == "request.completed")
    return line


async def _seen(headers, peer=RENDER_PROXY, trusted=RENDER_TRUSTED) -> tuple[str, str]:
    """(client, scheme) as the request log recorded them."""
    handler = _Capture()
    log.addHandler(handler)
    previous = log.level
    log.setLevel(logging.INFO)
    try:
        async with _behind_proxy(trusted=trusted, peer=peer) as client:
            await client.get("/openapi.json", headers=headers)
    finally:
        log.removeHandler(handler)
        log.setLevel(previous)
    line = _completed(handler.lines)
    return line["client"], line["scheme"]


# --- X-Forwarded-For and -Proto, from the proxies FORWARDED_ALLOW_IPS names ---------


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


# --- behind Cloudflare and Render: CF-Connecting-IP, and only when it can be believed --


async def test_behind_render_the_client_is_the_visitor_cloudflare_names():
    assert await _seen(_via_render()) == (VISITOR, "https")


async def test_a_forged_forwarded_for_in_front_changes_nothing():
    forged = f"198.51.100.9, 192.0.2.1, {VISITOR}, {EDGE}, {RENDER_HOP}"
    assert await _seen(_via_render(forwarded_for=forged)) == (VISITOR, "https")


async def test_true_client_ip_and_x_real_ip_are_never_read():
    headers = _via_render(**{"True-Client-IP": "192.0.2.44", "X-Real-IP": "192.0.2.55"})
    assert await _seen(headers) == (VISITOR, "https")


async def test_without_cloudflare_in_the_chain_cf_connecting_ip_is_ignored():
    """
    Reaching Render's load balancer around Cloudflare: the hop it records is
    the caller's own address, so a forged CF-Connecting-IP buys nothing.
    """
    attacker = "198.18.0.77"
    headers = _via_render(
        forwarded_for=f"192.0.2.1, {attacker}, {RENDER_HOP}", **{"CF-Connecting-IP": "192.0.2.9"}
    )
    assert await _seen(headers) == (attacker, "https")


@pytest.mark.parametrize("peer", [HEALTH_CHECK, ("10.1.2.3", 51000)])
async def test_a_peer_that_is_not_loopback_cannot_use_cf_connecting_ip(peer):
    """A health check, or another service on Render's private network."""
    headers = _via_render(forwarded_for=f"192.0.2.1, {EDGE}", **{"CF-Connecting-IP": "192.0.2.9"})
    assert await _seen(headers, peer=peer) == (EDGE, "https")


async def test_an_untrusted_loopback_peer_gets_nothing_believed():
    """FORWARDED_ALLOW_IPS without 127.0.0.1: no header from it is believed."""
    assert await _seen(_via_render(), trusted=PRIVATE_NETWORKS) == ("127.0.0.1", "http")


async def test_a_public_peer_gets_nothing_believed():
    assert await _seen(_via_render(), peer=("198.18.0.77", 443)) == ("198.18.0.77", "http")


async def test_a_health_check_is_the_peer_it_came_from():
    """Observed: Render's health checks send only X-Forwarded-Proto."""
    assert await _seen({"X-Forwarded-Proto": "https"}, peer=HEALTH_CHECK) == (
        "10.237.26.210",
        "https",
    )


@pytest.mark.parametrize("value", ["not-an-address", "", f"{VISITOR}, 192.0.2.1", "192.0.2.1:443"])
async def test_a_malformed_cf_connecting_ip_falls_back_to_the_edge(value):
    assert await _seen(_via_render(**{"CF-Connecting-IP": value})) == (EDGE, "https")


async def test_two_cf_connecting_ip_headers_fall_back_to_the_edge():
    headers = [
        ("X-Forwarded-For", f"{VISITOR}, {EDGE}, {RENDER_HOP}"),
        ("X-Forwarded-Proto", "https"),
        ("CF-Connecting-IP", VISITOR),
        ("CF-Connecting-IP", "192.0.2.9"),
    ]
    assert await _seen(headers) == (EDGE, "https")


async def test_an_ipv6_visitor_behind_an_ipv6_edge():
    headers = _via_render(
        forwarded_for=f"2001:db8:1::5, 2400:cb00:2049::1, {RENDER_HOP}",
        **{"CF-Connecting-IP": "2001:db8:1::5"},
    )
    assert await _seen(headers) == ("2001:db8:1::5", "https")


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        (EDGE, True),
        ("173.245.48.1", True),
        ("2a06:98c0:3600::103", True),  # the address Workers on other accounts arrive from
        ("172.63.255.255", False),  # just below 172.64.0.0/13
        ("172.72.0.0", False),  # just above it
        (RENDER_HOP, False),
        ("127.0.0.1", False),
        ("not-an-address", False),
        (None, False),
    ],
)
def test_is_cloudflare(host, expected):
    assert is_cloudflare(host) is expected


def test_cloudflare_client_needs_all_three_conditions():
    headers = [(b"cf-connecting-ip", VISITOR.encode())]
    assert cloudflare_client("127.0.0.1", EDGE, headers) == VISITOR
    assert cloudflare_client("::1", EDGE, headers) == VISITOR
    assert cloudflare_client("10.204.7.31", EDGE, headers) is None  # peer not loopback
    assert cloudflare_client("127.0.0.1", "198.18.0.77", headers) is None  # hop not Cloudflare
    assert cloudflare_client("127.0.0.1", EDGE, []) is None  # no header
    assert cloudflare_client(None, EDGE, headers) is None


# --- the start command --------------------------------------------------------------


def test_the_start_command_serves_the_wrapped_app_with_uvicorns_handling_off(monkeypatch):
    """
    Left on (uvicorn's default), uvicorn's own proxy handling would rewrite
    the peer before ClientAddressMiddleware sees it: the loopback check would
    see the Cloudflare edge instead, and never pass.
    """
    seen = {}
    monkeypatch.setattr(serve.uvicorn, "run", lambda app, **kw: seen.update(app=app, **kw))
    serve.serve()
    assert seen["app"] == "app.main:served"
    assert seen["proxy_headers"] is False


def test_served_is_the_app_behind_the_client_address_middleware():
    assert isinstance(main.served, ClientAddressMiddleware)
    assert main.served.app is app
    # trusting exactly what FORWARDED_ALLOW_IPS says (here, this test run's setting)
    trusted = main.served.forwarded.trusted_hosts
    for entry in main.settings.forwarded_allow_ips.split(","):
        if entry.strip() and "/" not in entry:
            assert entry.strip() in trusted
    assert "198.18.0.77" not in trusted


# --- the write rate limit behind a proxy ---------------------------------------------


def _write(client, headers):
    """A write refused with a 400 before any database is touched; it still counts."""
    return client.post("/api/transactions", json={"entries": []}, headers=headers)


async def test_two_forwarded_clients_get_separate_write_limits(monkeypatch):
    """Every request arrives from the proxy's address; the limit follows the forwarded one."""
    monkeypatch.setattr(write_limiter, "limit", 2)
    async with _behind_proxy(trusted=PRIVATE_NETWORKS) as client:
        one = [
            (await _write(client, {"X-Forwarded-For": "203.0.113.7"})).status_code for _ in range(3)
        ]
        other = [
            (await _write(client, {"X-Forwarded-For": "198.51.100.9"})).status_code
            for _ in range(3)
        ]
    assert one == [400, 400, 429]
    assert other == [400, 400, 429]


async def test_a_forged_forwarded_for_does_not_buy_a_fresh_allowance(monkeypatch):
    """
    A proxy that appends to X-Forwarded-For keeps whatever the client sent in
    front of the address it saw. Reading from the right and stopping at the
    first untrusted address finds the one the proxy wrote.
    """
    monkeypatch.setattr(write_limiter, "limit", 2)
    async with _behind_proxy(trusted=PRIVATE_NETWORKS) as client:
        statuses = [
            (await _write(client, {"X-Forwarded-For": f"{forged}, 203.0.113.7"})).status_code
            for forged in ("192.0.2.1", "192.0.2.2", "10.9.9.9")
        ]
    assert statuses == [400, 400, 429]


async def test_behind_render_two_visitors_get_separate_write_limits(monkeypatch):
    """Before CF-Connecting-IP was read, every visitor behind one edge shared its limit."""
    monkeypatch.setattr(write_limiter, "limit", 2)
    other = "198.51.100.23"
    async with _behind_proxy(trusted=RENDER_TRUSTED, peer=RENDER_PROXY) as client:
        one = [(await _write(client, _via_render())).status_code for _ in range(3)]
        two = [
            (
                await _write(
                    client,
                    _via_render(
                        forwarded_for=f"{other}, {EDGE}, {RENDER_HOP}",
                        **{"CF-Connecting-IP": other},
                    ),
                )
            ).status_code
            for _ in range(3)
        ]
    assert one == [400, 400, 429]
    assert two == [400, 400, 429]


async def test_behind_render_forged_headers_do_not_buy_a_fresh_allowance(monkeypatch):
    """Each write forges a new X-Forwarded-For, True-Client-IP and X-Real-IP; one bucket."""
    monkeypatch.setattr(write_limiter, "limit", 2)
    async with _behind_proxy(trusted=RENDER_TRUSTED, peer=RENDER_PROXY) as client:
        statuses = [
            (
                await _write(
                    client,
                    _via_render(
                        forwarded_for=f"192.0.2.{n}, {VISITOR}, {EDGE}, {RENDER_HOP}",
                        **{"True-Client-IP": f"192.0.2.{n}", "X-Real-IP": f"192.0.2.{n}"},
                    ),
                )
            ).status_code
            for n in (1, 2, 3)
        ]
    assert statuses == [400, 400, 429]


async def test_around_cloudflare_a_forged_cf_connecting_ip_buys_no_allowance(monkeypatch):
    """A caller that bypasses Cloudflare and rotates CF-Connecting-IP stays one client."""
    monkeypatch.setattr(write_limiter, "limit", 2)
    attacker = "198.18.0.77"
    async with _behind_proxy(trusted=RENDER_TRUSTED, peer=RENDER_PROXY) as client:
        statuses = [
            (
                await _write(
                    client,
                    _via_render(
                        forwarded_for=f"{attacker}, {RENDER_HOP}",
                        **{"CF-Connecting-IP": f"192.0.2.{n}"},
                    ),
                )
            ).status_code
            for n in (1, 2, 3)
        ]
    assert statuses == [400, 400, 429]


# --- keeping the copy of Cloudflare's ranges current --------------------------------


def test_the_range_check_reports_what_to_add_and_remove():
    import ipaddress

    ours = {ipaddress.ip_network("172.64.0.0/13"), ipaddress.ip_network("2400:cb00::/32")}
    published = {ipaddress.ip_network("172.64.0.0/13"), ipaddress.ip_network("192.0.2.0/24")}
    assert differences(published, ours) == (
        [ipaddress.ip_network("192.0.2.0/24")],
        [ipaddress.ip_network("2400:cb00::/32")],
    )
    assert differences(ours, ours) == ([], [])
