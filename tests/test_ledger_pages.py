import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import create_async_engine

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
