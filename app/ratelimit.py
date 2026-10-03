"""
A per-client limit on writes, for a ledger anyone on the internet can post to.

Every request that is not a read (anything but GET, HEAD and OPTIONS), form
or API, counts against its client's allowance: `WRITE_RATE_LIMIT` writes in
any `WRITE_RATE_WINDOW_SECONDS`, 30 a minute by default. Past that the
request is answered `429 Too Many Requests` with a `Retry-After` header
giving the whole seconds until the oldest write in the window expires,
before the app reads the body or opens a connection. Under `/api/` the 429
uses the API's error shape. A refused request does not count, so a client
that waits `Retry-After` seconds is let through. Reads are not limited.

**Who the client is.** `scope["client"]`, which behind a host's proxy is
what app/client_address.py made of `X-Forwarded-For` (trusting only the
proxies `FORWARDED_ALLOW_IPS` names) and, behind Cloudflare, of
`CF-Connecting-IP`. A client cannot choose it by forging either. The log's
`client` field comes from the same place, so the log shows exactly which
address a limit applied to. IPv6 clients are counted per /64, the block one
subscriber is normally given, so rotating through addresses in it does not
buy a fresh allowance.

**One instance only.** The counts live in this process's memory. Two
instances would each allow the full rate, and a restart forgets every
count. Like migrating on start (app/serve.py), this assumes one instance; a
deployment that scales out needs a shared store such as Redis instead.
"""

import ipaddress
import json
import math
import time
from collections import deque
from collections.abc import Callable

from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.errors import API_PREFIX, error_body

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def client_key(host: str | None) -> str:
    """The bucket a client's writes are counted in: its address, or its /64 for IPv6."""
    if not host:
        return "unknown"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


class RateLimiter:
    """
    A sliding window: at most `limit` hits per key in any `window` seconds.

    Each key keeps the times of its last `limit` hits, so memory is bounded
    by the number of clients active in one window. Keys idle for a whole
    window are dropped, at most once a window. `limit` 0 turns it off.
    """

    def __init__(
        self, limit: int, window: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window = window
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._next_sweep = clock() + window

    def reset(self) -> None:
        self._hits.clear()
        self._next_sweep = self.clock() + self.window

    def hit(self, key: str) -> float | None:
        """Count a hit for `key`; None if allowed, else the seconds until one would be."""
        if self.limit <= 0:
            return None
        now = self.clock()
        if now >= self._next_sweep:
            self._sweep(now)
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return hits[0] + self.window - now
        hits.append(now)
        return None

    def _sweep(self, now: float) -> None:
        cutoff = now - self.window
        idle = [key for key, hits in self._hits.items() if not hits or hits[-1] <= cutoff]
        for key in idle:
            del self._hits[key]
        self._next_sweep = now + self.window


class WriteRateLimitMiddleware:
    def __init__(self, app: ASGIApp, limiter: RateLimiter) -> None:
        self.app = app
        self.limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in READ_METHODS:
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        wait = self.limiter.hit(client_key(client[0] if client else None))
        if wait is None:
            await self.app(scope, receive, send)
            return

        retry_after = max(1, math.ceil(wait))
        message = (
            f"too many writes from this address: at most {self.limiter.limit} "
            f"every {self.limiter.window:g} seconds. Try again in {retry_after} seconds."
        )
        if scope["path"].startswith(API_PREFIX):
            body = json.dumps(error_body("rate_limited", message)).encode()
            content_type = b"application/json"
        else:
            body, content_type = message.encode(), b"text/plain; charset=utf-8"
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", content_type),
                    (b"content-length", str(len(body)).encode()),
                    (b"retry-after", str(retry_after).encode()),
                    # the body is never read, so the connection cannot be reused
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
