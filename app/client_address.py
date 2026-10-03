"""
Who the client is: the one place Keel decides it, for the request log, the
write rate limit and the scheme of any absolute URL it builds.

On Render a visitor's request goes visitor -> Cloudflare -> Render's load
balancer -> Render's proxy inside the container, on 127.0.0.1 -> Keel.
Observed on the live service (2026-10-03):

- the peer of every public request is 127.0.0.1, and the peer of Render's
  health checks is a 10.x address;
- X-Forwarded-For arrives as [whatever the client sent..., the client,
  the Cloudflare edge, a Render 10.x hop]: each hop appends;
- CF-Connecting-IP arrives as the client's address. Cloudflare sets it, and
  answers a client that sends its own with a 403;
- True-Client-IP and X-Real-IP pass through or are rewritten by layers this
  code cannot see, so neither is read.

Two steps, in order:

1. uvicorn's own ProxyHeadersMiddleware, unchanged: for a peer that
   FORWARDED_ALLOW_IPS trusts, X-Forwarded-Proto becomes the scheme, and
   X-Forwarded-For is read from the right, skipping trusted hops (on Render,
   127.0.0.1 and the 10.x hop). The first untrusted hop becomes the client.
   On Render that is the Cloudflare edge, which every visitor behind that
   edge shares.
2. If that hop is a Cloudflare edge (CLOUDFLARE_NETWORKS) and the request
   reached Keel through a proxy on this machine (the peer was loopback),
   the client is the CF-Connecting-IP that the edge set. Otherwise step 1's
   answer stands.

Why a client cannot choose its own address:

- Through Cloudflare, it cannot set CF-Connecting-IP: Cloudflare refuses a
  client-set one, and sets it itself on every request. A Worker on another
  Cloudflare account always arrives with Cloudflare's fixed Worker address
  (2a06:98c0:3600::103), so every such Worker shares one allowance.
- Anything written into X-Forwarded-For sits to the left of the hops the
  proxies appended, and step 1 never reads past the first untrusted one.
- Around Cloudflare, by reaching Render's load balancer directly: the hop
  that balancer records is the caller's own address, not a Cloudflare one,
  so step 2 does not apply and that address is the client. None of
  Render's public addresses offer such a route (each is announced through
  Cloudflare's network), but this does not rely on that.
- From another service on Render's private network, or from a health check:
  the peer is a 10.x address, not loopback, so step 2 does not apply.

What is left: someone who connects to Render's load balancer from inside
Cloudflare's own address space without going through Cloudflare's proxy
(a Worker's raw TCP socket, say), knowing an address of that balancer
that Render does not publish. Their forged CF-Connecting-IP would be
believed: a fresh write allowance, nothing more. The caps (MAX_ACCOUNTS,
MAX_TRANSACTIONS) still bound the database.

CLOUDFLARE_NETWORKS is a copy of Cloudflare's published list. A scheduled
workflow (.github/workflows/cloudflare-ranges.yml) compares it with the
live list. A stale copy fails safe: an edge in a new range is not
recognised, so its visitors share the edge's address, as before this
module existed. It never lets a client choose its address.
"""

import ipaddress

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

# https://www.cloudflare.com/ips-v4 and /ips-v6, fetched 2026-10-03.
# scripts/check_cloudflare_ranges.py compares this with the live lists.
CLOUDFLARE_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "173.245.48.0/20",
        "103.21.244.0/22",
        "103.22.200.0/22",
        "103.31.4.0/22",
        "141.101.64.0/18",
        "108.162.192.0/18",
        "190.93.240.0/20",
        "188.114.96.0/20",
        "197.234.240.0/22",
        "198.41.128.0/17",
        "162.158.0.0/15",
        "104.16.0.0/13",
        "104.24.0.0/14",
        "172.64.0.0/13",
        "131.0.72.0/22",
        "2400:cb00::/32",
        "2606:4700::/32",
        "2803:f800::/32",
        "2405:b500::/32",
        "2405:8100::/32",
        "2a06:98c0::/29",
        "2c0f:f248::/32",
    )
)

# Where step 1 leaves the peer as it connected, for step 2 to read.
_PEER = "keel.peer"


def _address(value: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None


def is_cloudflare(host: str | None) -> bool:
    address = _address(host)
    return address is not None and any(address in network for network in CLOUDFLARE_NETWORKS)


def cloudflare_client(peer: str | None, chosen: str | None, headers) -> str | None:
    """
    The client Cloudflare names in CF-Connecting-IP, when it can be believed:
    the connection came from loopback (Render's proxy in the container), the
    hop uvicorn chose from X-Forwarded-For is a Cloudflare edge, and there is
    exactly one CF-Connecting-IP holding exactly one address. Otherwise None.
    """
    peer_address = _address(peer)
    if peer_address is None or not peer_address.is_loopback or not is_cloudflare(chosen):
        return None
    values = [value for name, value in headers if name == b"cf-connecting-ip"]
    if len(values) != 1:
        return None
    address = _address(values[0].decode("latin-1"))
    return str(address) if address is not None else None


class ClientAddressMiddleware:
    """
    uvicorn's ProxyHeadersMiddleware, then the Cloudflare rule above. Wraps
    the whole app (app.main.served); uvicorn's own proxy handling is off
    (app/serve.py), so this is the only place headers become the client.
    """

    def __init__(self, app, trusted_hosts: str) -> None:
        self.app = app
        self.forwarded = ProxyHeadersMiddleware(self._from_cloudflare, trusted_hosts=trusted_hosts)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            scope[_PEER] = client[0] if client else None
        await self.forwarded(scope, receive, send)

    async def _from_cloudflare(self, scope, receive, send) -> None:
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            believed = cloudflare_client(
                scope.pop(_PEER, None), client[0] if client else None, scope["headers"]
            )
            if believed is not None:
                scope["client"] = (believed, 0)
        await self.app(scope, receive, send)
