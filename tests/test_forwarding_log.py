"""
LOG_FORWARDING_HEADERS, the temporary diagnostic in app/forwarding_log.py:
what it logs, what it never logs, that it leaves the proxy handling as it
was, and that the start command uses it only when asked. No database needed.
"""

import json
import logging

import pytest
from httpx import ASGITransport, AsyncClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app import forwarding_log, serve
from app.config import Settings
from app.forwarding_log import ForwardingHeaderLog, ip_shaped
from app.main import app
from app.observability import JsonFormatter, log

# The chain seen on Render: visitor -> Cloudflare -> Render's proxy -> Keel.
RENDER_PROXY = ("127.0.0.1", 41000)
TRUSTED = "127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.setFormatter(JsonFormatter())
        self.raw: list[str] = []

    def emit(self, record):
        self.raw.append(self.format(record))


@pytest.fixture
def captured():
    handler = _Capture()
    log.addHandler(handler)
    previous = log.level
    log.setLevel(logging.INFO)
    yield handler.raw
    log.removeHandler(handler)
    log.setLevel(previous)


def _event(raw, name) -> dict:
    (line,) = (json.loads(entry) for entry in raw if json.loads(entry)["event"] == name)
    return line


async def _get(headers, client=RENDER_PROXY):
    wrapped = ForwardingHeaderLog(ProxyHeadersMiddleware(app, trusted_hosts=TRUSTED))
    async with AsyncClient(
        transport=ASGITransport(app=wrapped, client=client), base_url="http://keel.example.com"
    ) as http:
        return await http.get("/openapi.json", headers=headers)


async def test_the_line_has_the_peer_and_only_ip_shaped_values(captured):
    await _get(
        [
            ("X-Forwarded-For", "198.51.100.9, 103.161.223.14, 172.68.147.142"),
            ("X-Forwarded-Proto", "https"),
            ("CF-Connecting-IP", "203.0.113.7"),
            ("Forwarded", 'for="[2001:db8::1]:4711";proto=https'),
            ("X-Real-IP", "not-an-address"),
            ("CF-Ray", "8c1f2a3b4c5d6e7f-SIN"),
            ("Cookie", "session=s3cret"),
            ("Authorization", "Bearer s3cret"),
        ]
    )
    line = _event(captured, "forwarding.headers")
    assert line["peer"] == "127.0.0.1"
    assert line["headers"] == {
        "x-forwarded-for": [["198.51.100.9", "103.161.223.14", "172.68.147.142"]],
        "x-forwarded-proto": [["-"]],
        "cf-connecting-ip": [["203.0.113.7"]],
        "forwarded": [["2001:db8::1", "-"]],
        "x-real-ip": [["-"]],
        "cf-ray": [["-"]],
    }
    # no other header, no non-address content, no path
    text = json.dumps(line)
    for never in ("s3cret", "cookie", "Bearer", "SIN", "https", "not-an-address", "openapi"):
        assert never not in text, never
    assert not any("s3cret" in entry for entry in captured)


async def test_the_peer_is_logged_as_it_connected_not_as_rewritten(captured):
    """The point of the module: uvicorn's rewriting happens after the line, not before."""
    await _get([("X-Forwarded-For", "103.161.223.14, 172.68.147.142")])
    assert _event(captured, "forwarding.headers")["peer"] == "127.0.0.1"
    # and what the app sees afterwards is exactly what uvicorn alone would give it
    assert _event(captured, "request.completed")["client"] == "172.68.147.142"


async def test_each_occurrence_of_a_header_is_its_own_list(captured):
    await _get([("X-Forwarded-For", "192.0.2.1"), ("X-Forwarded-For", "192.0.2.2:8080")])
    assert _event(captured, "forwarding.headers")["headers"] == {
        "x-forwarded-for": [["192.0.2.1"], ["192.0.2.2"]]
    }


async def test_a_request_with_no_forwarding_headers_logs_an_empty_set(captured):
    await _get([], client=("10.237.26.90", 52000))  # a Render health check
    line = _event(captured, "forwarding.headers")
    assert (line["peer"], line["headers"]) == ("10.237.26.90", {})


@pytest.mark.parametrize(
    ("token", "address"),
    [
        ("103.161.223.14", "103.161.223.14"),
        ("103.161.223.14:443", "103.161.223.14"),
        ("2001:db8::1", "2001:db8::1"),
        ("[2001:db8::1]:4711", "2001:db8::1"),
        ('for="192.0.2.60"', "192.0.2.60"),
        ("unknown", None),
        ("_hidden", None),
        ("8c1f2a3b4c5d6e7f-SIN", None),
        ("", None),
    ],
)
def test_ip_shaped(token, address):
    assert ip_shaped(token) == address


def test_the_module_serves_the_app_behind_the_same_proxy_handling():
    wrapped = forwarding_log.app.app
    assert isinstance(wrapped, ProxyHeadersMiddleware)
    assert wrapped.app is app


@pytest.mark.parametrize(
    ("flag", "target", "proxy_headers"),
    [
        (None, "app.main:app", True),
        ("0", "app.main:app", True),
        ("1", "app.forwarding_log:app", False),
    ],
)
def test_the_start_command_uses_the_diagnostic_only_when_asked(
    monkeypatch, flag, target, proxy_headers
):
    seen = {}
    monkeypatch.setattr(serve.uvicorn, "run", lambda app, **kw: seen.update(app=app, **kw))
    monkeypatch.delenv("LOG_FORWARDING_HEADERS", raising=False)
    if flag is not None:
        monkeypatch.setenv("LOG_FORWARDING_HEADERS", flag)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", TRUSTED)
    monkeypatch.setattr(serve, "settings", Settings(_env_file=None))
    serve.serve()
    assert (seen["app"], seen["proxy_headers"]) == (target, proxy_headers)
    assert seen["forwarded_allow_ips"] == TRUSTED
