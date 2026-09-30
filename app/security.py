"""
HTTP hardening that has nothing to do with the ledger: a request body size
limit and security headers. Both are plain ASGI middleware.

**Body size limit.** Nothing Keel accepts is large: a posting with dozens of
lines is a few kilobytes. A request declaring a body over the limit is
answered 413 before the app reads a byte, and one that does not declare its
length (chunked) is cut off with a 413 as soon as it passes the limit. Under
`/api/` the 413 uses the API's error shape.

**Security headers**, on every response:

- `X-Content-Type-Options: nosniff`, so a browser never guesses a type;
- `X-Frame-Options: DENY` and CSP `frame-ancestors 'none'`: no page can be
  framed, so none can be used for clickjacking;
- `Referrer-Policy: same-origin`: another site never learns which Keel URL
  a visitor came from;
- a `Content-Security-Policy`. Pages may load scripts only from Keel itself,
  from the Tailwind CDN they use, and inline when the script carries this
  request's nonce (`request.state.csp_nonce`), which the posting form's
  script does. Styles allow `'unsafe-inline'`, because the Tailwind Play CDN
  builds its CSS in the browser and injects it as `<style>` elements. FastAPI's
  `/docs` and `/redoc` pages load their UI from a CDN and bootstrap it with an
  inline script they generate, so they get a policy of their own.
"""

import json
import secrets

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import API_PREFIX, error_body

PAGE_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'nonce-{nonce}' https://cdn.tailwindcss.com; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

# FastAPI's generated docs: Swagger UI and ReDoc from jsDelivr, bootstrapped by
# an inline script FastAPI writes, ReDoc's fonts from Google and its search in
# a blob: worker, FastAPI's favicon from its own site.
DOCS_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.redoc.ly; "
    "worker-src 'self' blob:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)
DOCS_PATHS = ("/docs", "/redoc")

STATIC_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
]


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        nonce = secrets.token_urlsafe(16)
        # Starlette's request.state is this dict, so templates can read it.
        scope.setdefault("state", {})["csp_nonce"] = nonce
        path = scope["path"]
        policy = DOCS_CSP if path.startswith(DOCS_PATHS) else PAGE_CSP.format(nonce=nonce)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in {h for h, _ in STATIC_HEADERS}
                    and name.lower() != b"content-security-policy"
                ]
                headers += STATIC_HEADERS
                headers.append((b"content-security-policy", policy.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _BodyTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def _reject(self, scope: Scope, send: Send, status: int, message: str) -> None:
        if scope["path"].startswith(API_PREFIX):
            code = "payload_too_large" if status == 413 else "bad_request"
            body, content_type = json.dumps(error_body(code, message)).encode(), b"application/json"
        else:
            body, content_type = message.encode(), b"text/plain; charset=utf-8"
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", content_type),
                    (b"content-length", str(len(body)).encode()),
                    # the rest of the body is never read, so the connection
                    # cannot be reused
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        too_large = f"request body exceeds the {self.max_bytes}-byte limit"

        declared = dict(scope.get("headers", [])).get(b"content-length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                await self._reject(scope, send, 400, "Content-Length is not a number")
                return
            if length > self.max_bytes:
                await self._reject(scope, send, 413, too_large)
                return

        # Chunked, or a client sending more than it declared: count as it arrives.
        received = 0
        response_started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLarge:
            if response_started:
                raise
            await self._reject(scope, send, 413, too_large)
