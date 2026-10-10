"""
HTTP hardening (app/security.py): the request body size limit and the
security headers, and the pages' side of the page policy: nothing loaded
from another origin, no inline scripts or styles.

All but the last test need no database: every request they make is
answered, or refused, before one is touched.
"""

import json
import logging
import os
import re
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

import app.main as main_module
from app.config import settings
from app.db.schema import accounts, metadata
from app.main import BASE_DIR, app
from app.observability import JsonFormatter, log
from tests.support import reset_schema

LIMIT = settings.max_request_body_bytes
STATIC = BASE_DIR / "static"


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
    # a form gets a page that says so in words; the API keeps its JSON
    assert form.headers["content-type"] == "text/html; charset=utf-8"
    assert f"That&#39;s more than Keel accepts in one request ({LIMIT} bytes)." in form.text
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


async def test_pages_allow_only_their_own_scripts_and_styles(no_database):
    async with _client() as client:
        page = await client.get("/accounts/new")
    csp = page.headers["content-security-policy"]
    directives = dict(d.split(" ", 1) for d in csp.split("; "))
    # every script is a file under /static/js: no nonce, nothing inline
    assert directives["script-src"] == "'self'"
    # keel.css is the only stylesheet: no style attributes, no <style> elements
    assert directives["style-src"] == "'self'"
    # nothing from another origin
    assert "https:" not in csp
    assert "'unsafe-inline'" not in csp
    assert "nonce" not in csp


@pytest.mark.parametrize(
    "path, content_type",
    [
        ("/static/fonts/plus-jakarta-sans-latin.woff2", "font/woff2"),
        ("/static/css/keel.css", "text/css; charset=utf-8"),
        ("/static/js/post-transaction.js", "text/javascript; charset=utf-8"),
        ("/static/js/terms.js", "text/javascript; charset=utf-8"),
    ],
)
async def test_static_files_are_served_with_their_types(no_database, path, content_type):
    """Under nosniff a browser refuses a stylesheet or script served as anything else."""
    async with _client() as client:
        response = await client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"] == content_type


def test_the_stylesheet_points_only_at_files_keel_ships():
    stylesheet = STATIC / "css" / "keel.css"
    css = stylesheet.read_text(encoding="utf-8")
    urls = re.findall(r"url\(([^)]*)\)", css)
    assert urls, "keel.css should load the self-hosted fonts"
    for url in urls:
        url = url.strip("'\"")
        assert "//" not in url and not url.startswith("data:"), url
        assert (stylesheet.parent / url).resolve().is_file(), url
    assert "@import" not in css


async def test_the_api_docs_get_their_own_policy(no_database):
    async with _client() as client:
        docs = await client.get("/docs")
        page = await client.get("/accounts/new")
    assert docs.status_code == 200
    assert "https://cdn.jsdelivr.net" in docs.headers["content-security-policy"]
    assert "cdn.jsdelivr.net" not in page.headers["content-security-policy"]


# --- the pages under the policy (Postgres-backed) -----------------------------------


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


async def test_pages_load_nothing_from_elsewhere_and_have_nothing_inline(database):
    """
    The pages' half of the policy above. Every URL a page names is Keel's
    own, and none has an inline script, a style attribute or a <style>
    element, which script-src and style-src 'self' would refuse. Every
    /static file a page names exists, down to the icon's id in the sprite,
    so a typo cannot ship as a blank.
    """
    async with _client() as client:
        for name, account_type in (("Cash", "asset"), ("Sales", "revenue")):
            await client.post(
                "/accounts", data={"name": name, "account_type": account_type, "currency": "USD"}
            )
        async with main_module.engine.connect() as conn:
            ids = dict((await conn.execute(select(accounts.c.name, accounts.c.id))).all())
        sale = {
            "description": "Cash sale",
            "account_id": [str(ids["Cash"]), str(ids["Sales"])],
            "entry_type": ["debit", "credit"],
            "amount": ["10.00", "10.00"],
            "currency": ["USD", "USD"],
        }
        posted = await client.post("/post-transaction", data=sale)
        assert posted.status_code == 302
        pages = {
            path: await client.get(path)
            for path in (
                "/",
                "/transactions",
                "/event-log",
                "/post-transaction",
                "/accounts/new",
                "/learn",
                posted.headers["location"],
                f"/account-detail/{ids['Cash']}",
            )
        }
        # and so is the page for an account that isn't there
        pages["missing account"] = await client.get(f"/account-detail/{uuid.uuid4()}")
        # forms re-rendered with an error are pages too
        pages["POST /accounts"] = await client.post("/accounts", data={"name": ""})
        pages["POST /post-transaction"] = await client.post(
            "/post-transaction", data={**sale, "amount": ["10.00", "9.00"]}
        )
        # and so is the form drawn again for a line more, without JavaScript
        pages["GET add_line"] = await client.get("/post-transaction", params={"add_line": "1"})

    assert {path: r.status_code for path, r in pages.items()} == {
        **{path: 200 for path in pages},
        "POST /accounts": 422,
        "POST /post-transaction": 422,
        "missing account": 404,
    }
    for path, response in pages.items():
        html = response.text
        assert not re.search(r"<style\b", html, re.IGNORECASE), path
        assert not re.search(r"\sstyle\s*=", html, re.IGNORECASE), path
        assert not re.search(r"<script(?![^>]*\bsrc=)", html, re.IGNORECASE), path
        for url in re.findall(r"\s(?:src|href)=\"([^\"]*)\"", html):
            assert url.startswith(("/", "#")) and not url.startswith("//"), (path, url)
            if url.startswith("/static/"):
                file, _, fragment = url.removeprefix("/static/").partition("#")
                assert (STATIC / file).is_file(), (path, url)
                if fragment:
                    sprite = (STATIC / file).read_text(encoding="utf-8")
                    assert f'id="{fragment}"' in sprite, (path, url)
