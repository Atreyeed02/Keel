"""
One error shape for the JSON API.

Every error under `/api/` is answered with

    {"error": {"code": "<machine-readable>", "message": "<one readable sentence>"}}

whether a route raised it deliberately (`ApiError`), FastAPI raised it while
matching the request (an unknown path, a wrong method), or the request body
could not be read at all. `code` is what a client branches on; `message` is
for the person reading the log.

The handlers are scoped to `/api/`. The HTML routes keep FastAPI's default
error responses, which is what they have always returned and what their
tests pin down.
"""

import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.errors import describe_validation_error

API_PREFIX = "/api/"

# For errors raised by the framework rather than by a route.
_CODES_BY_STATUS = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    415: "unsupported_media_type",
    422: "validation_error",
}


class ApiError(Exception):
    """An error a JSON route answers with, in the shared shape."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_body(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


def _is_api(request: Request) -> bool:
    return request.url.path.startswith(API_PREFIX)


async def read_json_object(request: Request) -> dict[str, Any]:
    """
    The request body as a JSON object, or a 422 in the shared shape.

    Parsed here rather than by a FastAPI body model so that every
    validation message comes from `describe_validation_error` and the
    domain's own validators, the same wording the HTML forms show.
    """
    raw = await request.body()
    if not raw:
        raise ApiError(422, "validation_error", "request body is required")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError(422, "validation_error", "request body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ApiError(422, "validation_error", "request body must be a JSON object")
    return payload


def register_api_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return JSONResponse(error_body(exc.code, exc.message), status_code=exc.status_code)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if not _is_api(request):
            return await http_exception_handler(request, exc)
        return JSONResponse(
            error_body(_CODES_BY_STATUS.get(exc.status_code, "error"), str(exc.detail)),
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # The API routes read their own bodies, so this only fires for a
        # malformed header or query value. Kept so that even those answer in
        # the shared shape. FastAPI's error has the same `.errors()` as
        # pydantic's ValidationError, which is all the flattener reads.
        if not _is_api(request):
            return await request_validation_exception_handler(request, exc)
        return JSONResponse(
            error_body("validation_error", describe_validation_error(exc)), status_code=422
        )
