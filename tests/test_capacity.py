"""
The caps on total accounts and transactions (app/domain/capacity.py): at
the cap, a write that would add one is refused with a clear error, through
the forms and the JSON API, and nothing of it is written. A replay adds
nothing, so it is still answered.

Postgres-backed except the settings test; skipped without TEST_DATABASE_URL.
"""

import json
import logging
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings, settings
from app.db.schema import accounts, events, idempotency_keys, ledger_entries, metadata, transactions
from app.main import app
from app.observability import JsonFormatter, log
from scripts import seed_demo_data
from tests.support import reset_schema

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
ENGINE_USERS = ("app.main.engine", "app.api.accounts.engine", "app.api.transactions.engine")


@pytest.fixture
async def database(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL page integration tests")
    test_engine = create_async_engine(TEST_DATABASE_URL)
    for target in ENGINE_USERS:
        monkeypatch.setattr(target, test_engine)
    async with test_engine.begin() as conn:
        await reset_schema(conn)
    yield test_engine
    async with test_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
    await test_engine.dispose()


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


def _client():
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        follow_redirects=False,
    )


async def _counts(engine) -> dict[str, int]:
    async with engine.connect() as conn:
        return {
            t.name: await conn.scalar(select(func.count()).select_from(t))
            for t in (accounts, transactions, ledger_entries, events, idempotency_keys)
        }


def _account(name):
    return {"name": name, "account_type": "asset", "currency": "USD"}


async def _cash_and_revenue(client):
    ids = []
    for body in (_account("Cash"), {**_account("Revenue"), "account_type": "revenue"}):
        response = await client.post("/api/accounts", json=body)
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    return ids


def _entries(cash_id, revenue_id, amount="10.00"):
    return [
        {"account_id": cash_id, "entry_type": "debit", "amount": amount, "currency": "USD"},
        {"account_id": revenue_id, "entry_type": "credit", "amount": amount, "currency": "USD"},
    ]


def _form(cash_id, revenue_id, key, amount="10.00"):
    return {
        "description": "Invoice",
        "submission_key": key,
        "account_id": [cash_id, revenue_id],
        "entry_type": ["debit", "credit"],
        "amount": [amount, amount],
        "currency": ["USD", "USD"],
    }


# --- accounts ------------------------------------------------------------------------


async def test_the_api_refuses_an_account_past_the_cap(database, monkeypatch, captured):
    monkeypatch.setattr(settings, "max_accounts", 2)
    async with _client() as client:
        created = [
            (await client.post("/api/accounts", json=_account(f"A{n}"))).status_code
            for n in range(2)
        ]
        refused = await client.post("/api/accounts", json=_account("One too many"))

    assert created == [201, 201]
    assert refused.status_code == 409
    assert refused.json() == {
        "error": {
            "code": "ledger_full",
            "message": "the ledger already holds its maximum of 2 accounts",
        }
    }
    counts = await _counts(database)
    assert (counts["accounts"], counts["events"]) == (2, 2)
    (line,) = (entry for entry in captured if entry["event"] == "ledger.full")
    assert (line["level"], line["reason"]) == (
        "WARNING",
        "the ledger already holds its maximum of 2 accounts",
    )


async def test_the_form_refuses_an_account_past_the_cap(database, monkeypatch):
    monkeypatch.setattr(settings, "max_accounts", 1)
    async with _client() as client:
        first = await client.post("/accounts", data=_account("Cash"))
        refused = await client.post("/accounts", data=_account("Petty cash"))

    assert first.status_code == 302
    assert refused.status_code == 409
    assert "Cannot create account:" in refused.text
    assert "the ledger already holds its maximum of 1 account" in refused.text
    # the form comes back as it was submitted
    assert 'value="Petty cash"' in refused.text
    assert (await _counts(database))["accounts"] == 1


# --- transactions --------------------------------------------------------------------


async def test_the_api_refuses_a_transaction_past_the_cap(database, monkeypatch, captured):
    monkeypatch.setattr(settings, "max_transactions", 2)
    async with _client() as client:
        cash_id, revenue_id = await _cash_and_revenue(client)
        posted = [
            (
                await client.post(
                    "/api/transactions",
                    json={"entries": _entries(cash_id, revenue_id)},
                    headers={"Idempotency-Key": f"key-{n}"},
                )
            ).status_code
            for n in range(2)
        ]
        before = await _counts(database)
        refused = await client.post(
            "/api/transactions",
            json={"entries": _entries(cash_id, revenue_id)},
            headers={"Idempotency-Key": "key-full"},
        )

    assert posted == [201, 201]
    assert refused.status_code == 409
    assert refused.json() == {
        "error": {
            "code": "ledger_full",
            "message": "the ledger already holds its maximum of 2 transactions",
        }
    }
    # nothing was written, the idempotency claim included
    assert await _counts(database) == before
    assert any(entry["event"] == "ledger.full" for entry in captured)


async def test_the_form_refuses_a_transaction_past_the_cap(database, monkeypatch):
    monkeypatch.setattr(settings, "max_transactions", 1)
    async with _client() as client:
        cash_id, revenue_id = await _cash_and_revenue(client)
        first = await client.post("/post-transaction", data=_form(cash_id, revenue_id, "k1"))
        refused = await client.post(
            "/post-transaction", data=_form(cash_id, revenue_id, "k2", amount="77.00")
        )

    assert first.status_code == 302
    assert refused.status_code == 409
    assert "The ledger is full: it holds its maximum of 1 transaction." in refused.text
    assert "Nothing was posted" in refused.text
    assert 'value="77.00"' in refused.text
    counts = await _counts(database)
    assert (counts["transactions"], counts["idempotency_keys"]) == (1, 1)


async def test_a_replay_is_answered_when_the_ledger_is_full(database, monkeypatch):
    """A retry of a posting that made it in adds nothing, so the cap does not refuse it."""
    monkeypatch.setattr(settings, "max_transactions", 1)
    async with _client() as client:
        cash_id, revenue_id = await _cash_and_revenue(client)
        body = {"entries": _entries(cash_id, revenue_id)}
        first = await client.post("/api/transactions", json=body, headers={"Idempotency-Key": "k"})
        api_replay = await client.post(
            "/api/transactions", json=body, headers={"Idempotency-Key": "k"}
        )
        form_first = await client.post("/post-transaction", data=_form(cash_id, revenue_id, "f"))
        # the form's own key never made it in, so this is a new posting
        assert form_first.status_code == 409

    assert first.status_code == 201
    assert api_replay.status_code == 200
    assert api_replay.headers["idempotent-replayed"] == "true"
    assert api_replay.json()["id"] == first.json()["id"]
    assert (await _counts(database))["transactions"] == 1


async def test_a_refused_key_can_post_once_there_is_room(database, monkeypatch):
    """The refusal rolls the claim back, so the same key works after a reset or a raised cap."""
    monkeypatch.setattr(settings, "max_transactions", 1)
    async with _client() as client:
        cash_id, revenue_id = await _cash_and_revenue(client)
        body = {"entries": _entries(cash_id, revenue_id)}
        await client.post("/api/transactions", json=body, headers={"Idempotency-Key": "a"})
        refused = await client.post(
            "/api/transactions", json=body, headers={"Idempotency-Key": "b"}
        )
        monkeypatch.setattr(settings, "max_transactions", 2)
        admitted = await client.post(
            "/api/transactions", json=body, headers={"Idempotency-Key": "b"}
        )

    assert refused.status_code == 409
    assert admitted.status_code == 201
    assert admitted.headers["idempotent-replayed"] == "false"


async def test_a_cap_of_zero_means_no_cap(database, monkeypatch):
    monkeypatch.setattr(settings, "max_accounts", 0)
    monkeypatch.setattr(settings, "max_transactions", 0)
    async with _client() as client:
        cash_id, revenue_id = await _cash_and_revenue(client)
        statuses = {
            (
                await client.post(
                    "/api/transactions",
                    json={"entries": _entries(cash_id, revenue_id)},
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                )
            ).status_code
            for _ in range(5)
        }
    assert statuses == {201}


async def test_the_scripts_are_not_capped(database, monkeypatch):
    """Seeding writes through the same domain functions but is never refused."""
    monkeypatch.setattr(settings, "max_accounts", 1)
    monkeypatch.setattr(settings, "max_transactions", 1)
    async with database.begin() as conn:
        account_count, transaction_count, _ = await seed_demo_data.seed(conn)
    assert (account_count, transaction_count) == (8, 10)


def test_the_default_caps_fit_a_small_database():
    defaults = Settings(_env_file=None)
    assert (defaults.max_accounts, defaults.max_transactions) == (200, 2000)
