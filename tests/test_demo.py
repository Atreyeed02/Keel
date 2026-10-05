"""
The public demo: the notice on every page when ENVIRONMENT=demo, and
scripts/reset_demo_data.py, which restores the demo data and refuses to run
anywhere else or without --yes.

The notice's absence and the reset's refusals need no database; the rest
are Postgres-backed and skip without TEST_DATABASE_URL.
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.db.schema import accounts, events, idempotency_keys, metadata, transactions
from app.domain.reads import account_balances
from app.main import app
from scripts import reset_demo_data, seed_demo_data
from tests.support import reset_schema

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
ENGINE_USERS = (
    "app.main.engine",
    "app.api.accounts.engine",
    "app.api.transactions.engine",
    "scripts.reset_demo_data.engine",
)
NOTICE = "<strong>Public demo.</strong>"


def _client():
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        follow_redirects=False,
    )


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setattr(settings, "environment", "demo")


class _NoDatabase:
    def begin(self):
        raise AssertionError("the reset must refuse before connecting")

    connect = begin

    async def dispose(self):
        raise AssertionError("the reset must refuse before connecting")


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


async def _seeded(engine):
    async with engine.begin() as conn:
        await seed_demo_data.seed(conn)


async def _counts(engine) -> dict[str, int]:
    async with engine.connect() as conn:
        return {
            t.name: await conn.scalar(select(func.count()).select_from(t))
            for t in (accounts, transactions, events, idempotency_keys)
        }


async def _balances(engine) -> dict[str, tuple]:
    """Every account's balance by name: what the overview shows, without the ids."""
    async with engine.connect() as conn:
        rows = await account_balances(conn)
    return {row["name"]: (row["debits"], row["credits"], row["balance"]) for row in rows}


async def _visitor_writes():
    """What a demo visitor might leave behind: an account and a posting."""
    async with _client() as client:
        response = await client.post(
            "/api/accounts", json={"name": "Visitor", "account_type": "asset", "currency": "USD"}
        )
        visitor = response.json()["id"]
        async with reset_demo_data.engine.connect() as conn:
            capital = await conn.scalar(
                select(accounts.c.id).where(accounts.c.name == "Owner's capital")
            )
        response = await client.post(
            "/api/transactions",
            headers={"Idempotency-Key": "visitor-1"},
            json={
                "entries": [
                    {
                        "account_id": visitor,
                        "entry_type": "debit",
                        "amount": "5.00",
                        "currency": "USD",
                    },
                    {
                        "account_id": str(capital),
                        "entry_type": "credit",
                        "amount": "5.00",
                        "currency": "USD",
                    },
                ]
            },
        )
        assert response.status_code == 201, response.text


# --- the notice ----------------------------------------------------------------------


async def test_the_notice_is_only_on_the_demo(monkeypatch):
    async with _client() as client:
        for environment in ("development", "production"):
            monkeypatch.setattr(settings, "environment", environment)
            assert NOTICE not in (await client.get("/accounts/new")).text
        monkeypatch.setattr(settings, "environment", "Demo")
        page = (await client.get("/accounts/new")).text
    assert NOTICE in page
    assert "the data resets nightly" in page


async def test_every_page_carries_the_notice_on_the_demo(database, demo):
    await _seeded(database)
    async with database.connect() as conn:
        transaction_id = await conn.scalar(select(transactions.c.id).limit(1))
    async with _client() as client:
        pages = {
            path: await client.get(path)
            for path in (
                "/",
                "/transactions",
                "/event-log",
                "/post-transaction",
                "/accounts/new",
                f"/transaction-detail/{transaction_id}",
            )
        }
        # a form re-rendered with an error is a page too
        pages["POST /accounts"] = await client.post("/accounts", data={"name": ""})

    assert {path: response.status_code for path, response in pages.items()} == {
        **{path: 200 for path in pages},
        "POST /accounts": 422,
    }
    assert [path for path, response in pages.items() if NOTICE not in response.text] == []


# --- the reset script ----------------------------------------------------------------


@pytest.mark.parametrize("environment", ["development", "production", "", "demo-staging"])
@pytest.mark.parametrize("confirmed", [False, True])
async def test_the_reset_refuses_outside_the_demo_before_connecting(
    monkeypatch, capsys, environment, confirmed
):
    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr("scripts.reset_demo_data.engine", _NoDatabase())
    assert await reset_demo_data.main(confirmed=confirmed) == 1
    out = capsys.readouterr().out
    assert f"Refusing to reset: ENVIRONMENT is {environment!r}, not 'demo'" in out
    assert "Nothing changed." in out


async def test_the_reset_without_yes_only_counts(database, demo, capsys):
    await _seeded(database)
    await _visitor_writes()
    before = await _counts(database)

    assert await reset_demo_data.main(confirmed=False) == 1
    out = capsys.readouterr().out
    assert "This would delete 9 account(s), 11 transaction(s) and 20 event(s)" in out
    assert "Re-run with --yes" in out
    assert await _counts(database) == before


async def test_the_reset_restores_exactly_what_a_fresh_seed_writes(database, demo, capsys):
    await _seeded(database)
    fresh = await _balances(database)
    await _visitor_writes()
    assert await _balances(database) != fresh

    assert await reset_demo_data.main(confirmed=True) == 0
    out = capsys.readouterr().out
    assert "Deleted 9 account(s), 11 transaction(s) and 20 event(s)." in out
    assert "Restored the demo data: 8 accounts and 10 transactions." in out

    assert await _balances(database) == fresh
    assert await _counts(database) == {
        "accounts": 8,
        "transactions": 10,
        # one account.created per account, one transaction.posted per transaction
        "events": 18,
        # the visitor's key went with the rest
        "idempotency_keys": 0,
    }
    async with database.connect() as conn:
        # the log and the transaction list start again from 1
        assert await conn.scalar(select(func.min(events.c.sequence))) == 1
        assert await conn.scalar(select(func.min(transactions.c.sequence))) == 1


async def test_the_event_log_is_append_only_again_after_a_reset(database, demo):
    await _seeded(database)
    assert await reset_demo_data.main(confirmed=True) == 0
    for statement in ("DELETE FROM events", "TRUNCATE events CASCADE"):
        with pytest.raises(DBAPIError, match="append-only"):
            async with database.begin() as conn:
                await conn.execute(text(statement))
    assert (await _counts(database))["events"] == 18


async def test_a_failed_reset_leaves_the_ledger_as_it_was(database, demo, monkeypatch):
    """A failure after the truncate, while reseeding, rolls the truncate back, trigger and all."""
    await _seeded(database)
    await _visitor_writes()
    before, balances = await _counts(database), await _balances(database)

    async def failing_seed(conn):
        raise RuntimeError("seeding failed")

    monkeypatch.setattr("scripts.reset_demo_data.seed", failing_seed)
    with pytest.raises(RuntimeError, match="seeding failed"):
        await reset_demo_data.main(confirmed=True)

    assert await _counts(database) == before
    assert await _balances(database) == balances
    with pytest.raises(DBAPIError, match="append-only"):
        async with database.begin() as conn:
            await conn.execute(text("DELETE FROM events"))


async def test_the_demo_reset_ignores_the_caps(database, demo, monkeypatch):
    """Caps are for visitors; the reset reseeds whatever they are set to."""
    monkeypatch.setattr(settings, "max_accounts", 1)
    monkeypatch.setattr(settings, "max_transactions", 1)
    assert await reset_demo_data.main(confirmed=True) == 0
    assert (await _counts(database))["transactions"] == 10
