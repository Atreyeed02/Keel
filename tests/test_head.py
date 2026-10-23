"""
HEAD on every route that answers GET (app/head.py): the GET's status and
headers, no body. The first tests need no database; the last one compares
HEAD with GET on every page and API read against PostgreSQL.
"""

import json
import logging
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.main import app
from app.observability import JsonFormatter, log
from tests.support import reset_schema

ENGINE_USERS = (
    "app.main.engine",
    "app.api.accounts.engine",
    "app.api.transactions.engine",
    "app.db.engine.engine",
)
# Different on every response, whatever the method.
PER_RESPONSE = {"date", "x-request-id"}


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
    for target in ENGINE_USERS[:3]:
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


def _headers(response) -> dict[str, str]:
    return {k: v for k, v in response.headers.items() if k not in PER_RESPONSE}


async def _get_and_head(client, path):
    return await client.get(path), await client.head(path)


def _assert_head_is_get_without_body(get, head):
    assert head.status_code == get.status_code
    assert _headers(head) == _headers(get)
    assert head.content == b""
    assert int(head.headers["content-length"]) == len(get.content) > 0


# --- no database -------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/learn", "/accounts/new", "/openapi.json"])
async def test_head_is_the_get_without_its_body(no_database, path):
    async with _client() as client:
        get, head = await _get_and_head(client, path)
    assert get.status_code == 200
    _assert_head_is_get_without_body(get, head)


async def test_the_app_itself_sends_no_body_for_head(no_database):
    """
    Below httpx, which drops a HEAD response's body on its own: what the app
    hands the server. The body goes here, not left to the server to drop.
    """
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "HEAD",
        "scheme": "http",
        "path": "/learn",
        "raw_path": b"/learn",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"test")],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    await app(scope, receive, send)
    [start] = [m for m in sent if m["type"] == "http.response.start"]
    assert start["status"] == 200
    assert int(dict(start["headers"])[b"content-length"]) > 0
    assert b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body") == b""


async def test_head_is_logged_as_head(no_database, captured):
    async with _client() as client:
        await client.head("/learn")
    [line] = [line for line in captured if line["event"] == "request.completed"]
    assert (line["method"], line["path"], line["status"]) == ("HEAD", "/learn", 200)


async def test_head_where_there_is_no_get_is_still_a_405(no_database):
    """POST /accounts has no GET, so HEAD gets what GET would: 405, Allow: POST."""
    async with _client() as client:
        get, head = await _get_and_head(client, "/accounts")
    assert get.status_code == head.status_code == 405
    assert head.headers["allow"] == "POST"
    assert head.content == b""


async def test_static_files_answer_head_as_they_did(no_database):
    async with _client() as client:
        get, head = await _get_and_head(client, "/static/css/keel.css")
    _assert_head_is_get_without_body(get, head)
    assert head.headers["etag"]
    assert head.headers["last-modified"]


async def test_the_api_schema_gains_no_head_operations(no_database):
    async with _client() as client:
        schema = (await client.get("/openapi.json")).json()
    methods = {method for operations in schema["paths"].values() for method in operations}
    assert methods == {"get", "post"}


# --- every page and API read, against PostgreSQL --------------------------------------

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def database(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL page integration tests")
    test_engine = create_async_engine(TEST_DATABASE_URL)
    for target in ENGINE_USERS:
        monkeypatch.setattr(target, test_engine)
    async with test_engine.begin() as conn:
        await reset_schema(conn)
    yield
    async with test_engine.begin() as conn:
        from app.db.schema import metadata

        await conn.run_sync(metadata.drop_all)
    await test_engine.dispose()


def _entry(account_id: str, side: str) -> dict[str, str]:
    return {"account_id": account_id, "entry_type": side, "amount": "10.00", "currency": "USD"}


async def test_every_get_route_answers_head_with_its_status_and_headers(database):
    async with _client() as client:
        ids = []
        for name, account_type in (("Cash", "asset"), ("Sales", "revenue")):
            created = await client.post(
                "/api/accounts",
                json={"name": name, "account_type": account_type, "currency": "USD"},
            )
            ids.append(created.json()["id"])
        posted = await client.post(
            "/api/transactions",
            json={
                "description": "Invoice 7",
                "entries": [_entry(ids[0], "debit"), _entry(ids[1], "credit")],
            },
            headers={"Idempotency-Key": "head-check-1"},
        )
        transaction = posted.json()["id"]
        unknown = uuid.uuid4()
        expected = {
            "/": 200,
            "/transactions": 200,
            "/event-log": 200,
            "/learn": 200,
            "/accounts/new": 200,
            "/post-transaction": 200,
            f"/transaction-detail/{transaction}": 200,
            f"/account-detail/{ids[0]}": 200,
            f"/transaction-detail/{unknown}": 404,
            f"/account-detail/{unknown}": 404,
            "/health": 200,
            "/api/accounts": 200,
            f"/api/transactions/{transaction}": 200,
            f"/api/transactions/{unknown}": 404,
        }
        for path, status in expected.items():
            get, head = await _get_and_head(client, path)
            assert get.status_code == status, path
            _assert_head_is_get_without_body(get, head)
