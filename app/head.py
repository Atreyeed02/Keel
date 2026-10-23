"""
HEAD on every route that answers GET.

Starlette's own routes answer HEAD wherever they answer GET, but FastAPI's
do not: `@app.get` is GET only, so `HEAD /` was a 405, and a host's first
request to a new instance is often exactly that. Adding HEAD to each route
would also add a `head` operation to the OpenAPI schema for every one.

Instead, a HEAD request runs as the GET it stands for, and its response
keeps the GET's status and headers, Content-Length included, with the body
dropped here rather than left to the server. Outside the app nothing
changes: the request log shows HEAD, and the write rate limit counts it
as the read it is (app/ratelimit.py). /static is left to Starlette's
StaticFiles, which answers HEAD itself without opening the file.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

STATIC_PREFIX = "/static/"


class HeadAsGetMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "HEAD"
            or scope["path"].startswith(STATIC_PREFIX)
        ):
            await self.app(scope, receive, send)
            return

        async def without_body(message: Message) -> None:
            if message["type"] == "http.response.body":
                message = {**message, "body": b""}
            await send(message)

        await self.app({**scope, "method": "GET"}, receive, without_body)
