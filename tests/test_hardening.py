"""
HTTP hardening (app/security.py): the request body size limit and the
security headers, including the posting form's CSP nonce.

All but the last test need no database: every request they make is answered,
or refused, before one is touched.
"""

import json
import logging
import os
import re

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.db.schema import metadata
from app.main import app
from app.observability import JsonFormatter, log
from tests.support import reset_schema

LIMIT = settings.max_request_body_bytes


def _client():
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        follow_redirects=False,
    )


class _NoDatabase:
    def begin(self):
        raise AssertionError("this request should have been answered before touching the database")

    connect = begin


@pytest.fixture
def no_database(monkeypatch):
    for target in ("app.main.engine", "app.api.accounts.engine", "app.api.transactions.engine"):
        monkeypatch.setattr(target, _NoDatabase())


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


# --- body size limit ----------------------------------------------------------------


async def test_a_declared_body_over_the_limit_is_a_413_before_the_app_reads_it(
    no_database, captured
):
    body = b"x" * (LIMIT + 1)
    async with _client() as client:
        api = await client.post(
            "/api/transactions",
            content=body,
            headers={"Content-Type": "application/json", "Idempotency-Key": "k"},
        )
        form = await client.post(
            "/post-transaction",
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    assert api.status_code == 413
    assert api.json() == {
        "error": {
            "code": "payload_too_large",
            "message": f"request body exceeds the {LIMIT}-byte limit",
        }
    }
    assert form.status_code == 413
    assert form.text == f"request body exceeds the {LIMIT}-byte limit"
    # A route that never reads its body can only be protected by the declared
    # length: this is refused before the app runs at all.
    async with _client() as client:
        unread = await client.request("GET", "/accounts/new", content=body)
    assert unread.status_code == 413
    # logged as an ordinary response, not a failure with a traceback
    statuses = [line["status"] for line in captured if line["event"] == "request.completed"]
    assert statuses == [413, 413, 413]
    assert not [line for line in captured if line["event"] == "request.failed"]


async def test_a_chunked_body_is_cut_off_once_it_passes_the_limit(no_database):
    """No Content-Length to check up front, so the bytes are counted as they arrive."""
    sent = []

    async def chunks():
        for _ in range(LIMIT // 1024 + 8):
            sent.append(1)
            yield b"x" * 1024

    async with _client() as client:
        response = await client.post(
            "/api/accounts", content=chunks(), headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


async def test_a_body_within_the_limit_is_untouched(no_database):
    """Just under the limit reaches the route, which rejects it for its own reason."""
    body = b" " * (LIMIT - 2) + b"{}"
    async with _client() as client:
        response = await client.post(
            "/api/accounts", content=body, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_a_content_length_that_is_not_a_number_is_a_400(no_database):
    async with _client() as client:
        response = await client.post(
            "/api/accounts", content=b"{}", headers={"Content-Length": "lots"}
        )
    assert response.status_code == 400


# --- security headers -------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/accounts/new", "/openapi.json", "/api/nothing-here", "/static/logo.svg"]
)
async def test_every_response_carries_the_security_headers(no_database, path):
    async with _client() as client:
        response = await client.get(path)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"
    csp = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp


async def test_a_413_carries_them_too(no_database):
    async with _client() as client:
        response = await client.post("/api/accounts", content=b"x" * (LIMIT + 1))
    assert response.status_code == 413
    assert response.headers["x-frame-options"] == "DENY"


async def test_pages_allow_only_their_own_scripts_tailwind_and_the_nonce(no_database):
    async with _client() as client:
        first = await client.get("/accounts/new")
        second = await client.get("/accounts/new")
    csp = first.headers["content-security-policy"]
    (script_src,) = (d for d in csp.split("; ") if d.startswith("script-src"))
    nonce = re.fullmatch(
        r"script-src 'self' 'nonce-([\w-]+)' https://cdn\.tailwindcss\.com", script_src
    ).group(1)
    assert len(nonce) >= 16
    # 'unsafe-inline' for styles only: the Tailwind Play CDN injects <style>
    assert "'unsafe-inline'" not in script_src
    assert "style-src 'self' 'unsafe-inline'" in csp
    # a new nonce per response
    assert second.headers["content-security-policy"] != csp


async def test_the_api_docs_get_their_own_policy(no_database):
    async with _client() as client:
        docs = await client.get("/docs")
        page = await client.get("/accounts/new")
    assert docs.status_code == 200
    assert "https://cdn.jsdelivr.net" in docs.headers["content-security-policy"]
    assert "cdn.jsdelivr.net" not in page.headers["content-security-policy"]


# --- the posting form's script runs under the policy (Postgres-backed) ---------------


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def database(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL page integration tests")
    test_engine = create_async_engine(TEST_DATABASE_URL)
    monkeypatch.setattr("app.main.engine", test_engine)
    async with test_engine.begin() as conn:
        await reset_schema(conn)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
    await test_engine.dispose()


async def test_the_posting_forms_inline_script_carries_this_responses_nonce(database):
    async with _client() as client:
        response = await client.get("/post-transaction")
    assert response.status_code == 200
    nonce = re.search(r"'nonce-([\w-]+)'", response.headers["content-security-policy"]).group(1)
    inline_scripts = re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>", response.text)
    assert inline_scripts == [f' nonce="{nonce}"']
