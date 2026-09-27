"""
Structured logging and request ids (app/observability.py).

The header and formatter tests need no database. They request
/openapi.json, which never touches it (/health would, and waits on a
connection timeout when no database is reachable). The posting test is
Postgres-backed and skips like the others without TEST_DATABASE_URL.
"""

import json
import logging
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.schema import accounts, metadata
from app.main import app
from app.observability import JsonFormatter, log, request_id_var
from tests.support import reset_schema

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _client():
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    )


class _Capture(logging.Handler):
    """Collects the JSON lines the real formatter produces."""

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


async def test_every_response_carries_a_generated_request_id():
    async with _client() as client:
        first = await client.get("/openapi.json")
        second = await client.get("/openapi.json")
    ids = [first.headers["x-request-id"], second.headers["x-request-id"]]
    assert all(len(i) == 32 and int(i, 16) >= 0 for i in ids)
    assert ids[0] != ids[1]


async def test_a_client_request_id_is_propagated():
    async with _client() as client:
        response = await client.get(
            "/openapi.json", headers={"X-Request-ID": "checkout-7f3a.retry-2"}
        )
    assert response.headers["x-request-id"] == "checkout-7f3a.retry-2"


@pytest.mark.parametrize(
    "unsafe",
    ["has space", 'quote"injection', "x" * 129, ""],
    ids=["space", "quote", "long", "empty"],
)
async def test_an_unsafe_client_request_id_is_replaced(unsafe):
    """A supplied id goes into log lines, so one that could break them is not trusted."""
    async with _client() as client:
        response = await client.get("/openapi.json", headers={"X-Request-ID": unsafe})
    replaced = response.headers["x-request-id"]
    assert replaced != unsafe
    assert len(replaced) == 32


async def test_each_request_is_logged_once_with_its_id(captured):
    async with _client() as client:
        response = await client.get("/openapi.json", headers={"X-Request-ID": "req-abc"})
    completed = [line for line in captured if line["event"] == "request.completed"]
    assert len(completed) == 1
    line = completed[0]
    assert line["request_id"] == "req-abc" == response.headers["x-request-id"]
    assert line["method"] == "GET"
    assert line["path"] == "/openapi.json"
    assert line["status"] == 200
    assert line["duration_ms"] >= 0


def test_formatter_emits_one_json_object_with_extra_fields():
    record = logging.LogRecord("keel", logging.INFO, __file__, 1, "transaction.posted", None, None)
    record.transaction_id = "t-1"
    record.idempotency_key = "k-1"
    token = request_id_var.set("req-1")
    try:
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert line["event"] == "transaction.posted"
    assert line["level"] == "INFO"
    assert line["request_id"] == "req-1"
    assert line["transaction_id"] == "t-1"
    assert line["idempotency_key"] == "k-1"
    # none of LogRecord's own bookkeeping leaks into the output
    assert not {"args", "msg", "pathname", "lineno", "levelno"} & line.keys()


@pytest.fixture
async def database(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL page integration tests")
    test_engine = create_async_engine(TEST_DATABASE_URL)
    monkeypatch.setattr("app.main.engine", test_engine)
    async with test_engine.begin() as conn:
        await reset_schema(conn)
        cash_id, revenue_id = uuid.uuid4(), uuid.uuid4()
        await conn.execute(
            insert(accounts),
            [
                {"id": cash_id, "name": "Cash", "account_type": "asset", "currency": "USD"},
                {"id": revenue_id, "name": "Revenue", "account_type": "revenue", "currency": "USD"},
            ],
        )
    yield cash_id, revenue_id
    async with test_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
    await test_engine.dispose()


async def test_posting_and_retry_are_logged_with_ledger_identifiers(database, captured):
    """
    A posting's log line carries what joins it to the ledger and to the
    HTTP request: transaction id, idempotency key, request id. A retry is
    logged as a replay of the same transaction, not as a second posting.
    """
    cash_id, revenue_id = database
    data = {
        "description": "Logged sale",
        "submission_key": "log-key-1",
        "account_id": [str(cash_id), str(revenue_id)],
        "entry_type": ["debit", "credit"],
        "amount": ["10.00", "10.00"],
        "currency": ["USD", "USD"],
    }
    async with _client() as client:
        first = await client.post("/post-transaction", data=data, headers={"X-Request-ID": "r1"})
        await client.post("/post-transaction", data=data, headers={"X-Request-ID": "r2"})
    transaction_id = first.headers["location"].rsplit("/", 1)[1]

    ledger_lines = [
        line
        for line in captured
        if line["event"] in ("transaction.posted", "transaction.replayed")
    ]
    assert [(line["event"], line["request_id"]) for line in ledger_lines] == [
        ("transaction.posted", "r1"),
        ("transaction.replayed", "r2"),
    ]
    for line in ledger_lines:
        assert line["transaction_id"] == transaction_id
        assert line["idempotency_key"] == "log-key-1"
        assert line["entry_count"] == 2
        assert line["account_ids"] == sorted([str(cash_id), str(revenue_id)])
