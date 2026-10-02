"""
Temporary diagnostic: which forwarding headers reach Keel, and from which
peer. Off unless LOG_FORWARDING_HEADERS is set; remove it once the way to
find the real client address on Render is settled.

uvicorn's proxy handling rewrites the request's client address before the
app sees it, so inside app.main the peer that actually connected is already
gone. With the flag on, `python -m app.serve` turns uvicorn's handling off
and serves `app` from this module instead: each request is logged as it
arrived, then handed to the same ProxyHeadersMiddleware, with the same
trusted list, that uvicorn would have used. With the flag off, nothing
imports this module.

A line holds the peer's address and, for each forwarding header present,
its IP-shaped values in order, one list per occurrence of the header.
Anything else in a value becomes "-", so its shape shows and its content
does not. No other header's value is read: no cookie, no authorization,
and no path.

    {"event": "forwarding.headers", "peer": "127.0.0.1",
     "headers": {"x-forwarded-for": [["203.0.113.7", "172.68.147.142"]],
                 "x-forwarded-proto": [["-"]]}}
"""

import ipaddress
import re

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import settings
from app.main import app as keel
from app.observability import log

FORWARDING_HEADERS = (
    b"x-forwarded-for",
    b"x-forwarded-proto",
    b"x-real-ip",
    b"cf-connecting-ip",
    b"true-client-ip",
    b"forwarded",
    b"cf-ray",
)

# Between hops (commas), and between Forwarded's key=value pairs (semicolons).
_SEPARATORS = re.compile(r"[,;\s]+")


def ip_shaped(token: str) -> str | None:
    """
    The address `token` holds, or None. Takes the spellings the headers use:
    1.2.3.4, 1.2.3.4:80, 2001:db8::1, [2001:db8::1]:80, quoted, or as the
    value of a Forwarded pair (for=...).
    """
    token = token.split("=", 1)[-1].strip().strip('"')
    if token.startswith("["):
        token = token[1:].split("]", 1)[0]
    elif token.count(":") == 1:
        token = token.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(token))
    except ValueError:
        return None


def forwarding_fields(scope) -> dict:
    """The peer and the forwarding headers' IP-shaped values, for one request."""
    headers: dict[str, list[list[str]]] = {}
    for name, value in scope["headers"]:
        if name in FORWARDING_HEADERS:
            tokens = [t for t in _SEPARATORS.split(value.decode("latin-1")) if t]
            headers.setdefault(name.decode("latin-1"), []).append(
                [ip_shaped(token) or "-" for token in tokens] or ["-"]
            )
    client = scope.get("client")
    return {"peer": client[0] if client else None, "headers": headers}


class ForwardingHeaderLog:
    """Logs `forwarding.headers` for every HTTP request, then passes it on unchanged."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            log.info("forwarding.headers", extra=forwarding_fields(scope))
        await self.app(scope, receive, send)


app = ForwardingHeaderLog(ProxyHeadersMiddleware(keel, trusted_hosts=settings.forwarded_allow_ips))
