import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import create_async_engine

from app import main as main_module
from app.db.schema import accounts, metadata
from app.main import app

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def database(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL page integration tests")
    test_engine = create_async_engine(TEST_DATABASE_URL)
    monkeypatch.setattr("app.main.engine", test_engine)
    async with test_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)
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


def _submission(cash_id, revenue_id, amount="100.00"):
    return {
        "description": "Test sale",
        "submission_key": "test-submission-key",
        "account_id": [str(cash_id), str(revenue_id)],
        "entry_type": ["debit", "credit"],
        "amount": [amount, "100.00"],
        "currency": ["USD", "USD"],
    }


async def test_idempotent_retry_returns_same_transaction_id(database):
    cash_id, revenue_id = database
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        first = await client.post("/post-transaction", data=_submission(cash_id, revenue_id))
        retry = await client.post("/post-transaction", data=_submission(cash_id, revenue_id))
    assert first.status_code == retry.status_code == 302
    assert first.headers["location"] == retry.headers["location"]


async def test_unbalanced_submission_renders_inline_error(database):
    cash_id, revenue_id = database
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/post-transaction", data=_submission(cash_id, revenue_id, "101.00")
        )
    assert response.status_code == 422
    assert "Cannot post transaction" in response.text
    assert "does not balance" in response.text


async def test_created_account_appears_in_overview(database):
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        created = await client.post(
            "/accounts",
            data={"name": "Office rent", "account_type": "expense", "currency": "usd"},
        )
        overview = await client.get("/")
    assert created.status_code == 302
    assert created.headers["location"] == "/"
    assert overview.status_code == 200
    assert "Office rent" in overview.text


async def test_invalid_account_type_renders_inline_error(database):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/accounts",
            data={"name": "Suspense", "account_type": "banana", "currency": "USD"},
        )
        overview = await client.get("/")
    assert response.status_code == 422
    assert "Cannot create account" in response.text
    assert "account_type must be one of" in response.text
    # the submitted values come back so the form isn't retyped from scratch
    assert 'value="Suspense"' in response.text
    # and nothing was written
    assert "Suspense" not in overview.text


async def test_overview_shows_posted_account_balance(database):
    cash_id, revenue_id = database
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        await client.post("/post-transaction", data=_submission(cash_id, revenue_id))
        overview = await client.get("/")
    assert overview.status_code == 200
    assert "Cash" in overview.text
    assert "100.00" in overview.text


@pytest.fixture
async def eur_accounts(database):
    """A EUR account pair, alongside the USD pair `database` already made."""
    bank_id, revenue_id = uuid.uuid4(), uuid.uuid4()
    async with main_module.engine.begin() as conn:
        await conn.execute(
            insert(accounts),
            [
                {"id": bank_id, "name": "EUR bank", "account_type": "asset", "currency": "EUR"},
                {
                    "id": revenue_id,
                    "name": "EUR revenue",
                    "account_type": "revenue",
                    "currency": "EUR",
                },
            ],
        )
    return bank_id, revenue_id


async def test_unknown_account_renders_inline_error(database):
    """A nonexistent account_id is a form error, not a foreign-key 500."""
    cash_id, revenue_id = database
    ghost_id = uuid.uuid4()
    submission = _submission(cash_id, revenue_id)
    submission["account_id"] = [str(cash_id), str(ghost_id)]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/post-transaction", data=submission)
        overview = await client.get("/")
    assert response.status_code == 422
    assert "Cannot post transaction" in response.text
    assert "no account exists with id" in response.text
    # the offending id is named, so the message is actionable
    assert str(ghost_id) in response.text
    # and the transaction was rolled back, not half-written
    assert "Test sale" not in overview.text


async def test_currency_mismatch_renders_inline_error(database):
    """An entry may not carry a currency its account doesn't hold."""
    cash_id, revenue_id = database  # both USD
    submission = _submission(cash_id, revenue_id)
    # balances cleanly in EUR, so this gets past assert_balanced and is
    # caught by the account check rather than the invariant check
    submission["currency"] = ["EUR", "EUR"]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/post-transaction", data=submission)
        overview = await client.get("/")
    assert response.status_code == 422
    assert "Cannot post transaction" in response.text
    # the message names the account, its currency, and what was submitted
    assert "is USD" in response.text
    assert "submitted as EUR" in response.text
    assert "Cash" in response.text
    # crucially: it did not silently succeed
    assert "Test sale" not in overview.text


async def test_multi_currency_transaction_still_posts(database, eur_accounts):
    """
    The check constrains an entry to *its own* account's currency — it does
    not stop one transaction touching several currencies. Each side here
    matches the account it names and balances within its own currency.
    """
    cash_id, usd_revenue_id = database
    eur_bank_id, eur_revenue_id = eur_accounts
    submission = {
        "description": "Mixed-currency settlement",
        "submission_key": "multi-currency-key",
        "account_id": [
            str(cash_id),
            str(usd_revenue_id),
            str(eur_bank_id),
            str(eur_revenue_id),
        ],
        "entry_type": ["debit", "credit", "debit", "credit"],
        "amount": ["100.00", "100.00", "50.00", "50.00"],
        "currency": ["USD", "USD", "EUR", "EUR"],
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        response = await client.post("/post-transaction", data=submission)
        overview = await client.get("/")
    assert response.status_code == 302
    # both currencies get their own totals line rather than one summed figure
    assert "USD" in overview.text
    assert "EUR" in overview.text
    assert "100.00" in overview.text
    assert "50.00" in overview.text
