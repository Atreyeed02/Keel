"""
One submission key, one committed ledger effect: checked sequentially,
under a conflicting payload, after a rejected attempt, and under
concurrency.

The concurrent tests are deterministic, not a hopeful `gather`. Each
request is held at an `asyncio.Barrier` placed just before
`post_transaction_once`, so none of them touches the key until all of
them are ready, and then they reach it together. That is exactly the
window the old SELECT-then-INSERT design lost: every request saw "no such
key", one committed, and the rest failed with a 500.

Postgres-backed, and skipped without TEST_DATABASE_URL like the other
integration tests.
"""

import asyncio
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import create_async_engine

from app import main as main_module
from app.db.schema import accounts, events, idempotency_keys, ledger_entries, metadata, transactions
from app.domain.idempotency import request_fingerprint
from tests.support import reset_schema

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


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


def _submission(cash_id, revenue_id, *, key="same-key", amount="100.00"):
    return {
        "description": "Invoice 42",
        "submission_key": key,
        "account_id": [str(cash_id), str(revenue_id)],
        "entry_type": ["debit", "credit"],
        "amount": [amount, amount],
        "currency": ["USD", "USD"],
    }


async def _ledger_counts() -> dict[str, int]:
    """How many rows each table the posting flow writes to holds."""
    async with main_module.engine.connect() as conn:
        return {
            t.name: await conn.scalar(select(func.count()).select_from(t))
            for t in (transactions, ledger_entries, events, idempotency_keys)
        }


def _client():
    # raise_app_exceptions=False: an unhandled error must surface as the
    # 500 a real client would see, so a test can assert it did not happen.
    return AsyncClient(
        transport=ASGITransport(app=main_module.app, raise_app_exceptions=False),
        base_url="http://test",
        follow_redirects=False,
    )


def _gate_before_posting(monkeypatch, parties: int) -> None:
    """Hold `parties` requests just before the idempotent post, then release them together."""
    barrier = asyncio.Barrier(parties)
    real = main_module.post_transaction_once

    async def gated(*args, **kwargs):
        await barrier.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr("app.main.post_transaction_once", gated)


ONE_POSTING = {"transactions": 1, "ledger_entries": 2, "events": 1, "idempotency_keys": 1}


async def test_retry_writes_the_ledger_exactly_once(database):
    cash_id, revenue_id = database
    async with _client() as client:
        first = await client.post("/post-transaction", data=_submission(cash_id, revenue_id))
        retries = [
            await client.post("/post-transaction", data=_submission(cash_id, revenue_id))
            for _ in range(3)
        ]
    assert first.status_code == 302
    assert {r.status_code for r in retries} == {302}
    assert {r.headers["location"] for r in retries} == {first.headers["location"]}
    assert await _ledger_counts() == ONE_POSTING


async def test_same_key_with_a_different_payload_is_a_409(database):
    cash_id, revenue_id = database
    async with _client() as client:
        first = await client.post("/post-transaction", data=_submission(cash_id, revenue_id))
        # still balanced, so this is not a validation failure — only the key is wrong
        reused = await client.post(
            "/post-transaction", data=_submission(cash_id, revenue_id, amount="50.00")
        )
    assert first.status_code == 302
    assert reused.status_code == 409
    assert reused.json() == {"detail": "submission key was used for another request"}
    assert await _ledger_counts() == ONE_POSTING
    # and the stored result is still the first request's, untouched
    async with main_module.engine.connect() as conn:
        amounts = (await conn.scalars(select(ledger_entries.c.amount))).all()
    assert {str(a) for a in amounts} == {"100.00"}


async def test_a_rejected_attempt_does_not_consume_the_key(database):
    """
    An attempt that fails validation rolls back its claim on the key with
    everything else, so the client can fix the submission and resend it
    under the same key instead of being told the key is taken.
    """
    cash_id, revenue_id = database
    ghost_id = uuid.uuid4()
    async with _client() as client:
        rejected = await client.post("/post-transaction", data=_submission(ghost_id, revenue_id))
        assert rejected.status_code == 422
        assert await _ledger_counts() == {
            "transactions": 0,
            "ledger_entries": 0,
            "events": 0,
            "idempotency_keys": 0,
        }
        fixed = await client.post("/post-transaction", data=_submission(cash_id, revenue_id))
    assert fixed.status_code == 302
    assert await _ledger_counts() == ONE_POSTING


async def test_concurrent_duplicates_commit_one_effect(database, monkeypatch):
    cash_id, revenue_id = database
    parties = 5
    _gate_before_posting(monkeypatch, parties)
    async with _client() as client, asyncio.timeout(20):
        responses = await asyncio.gather(
            *[
                client.post("/post-transaction", data=_submission(cash_id, revenue_id))
                for _ in range(parties)
            ]
        )
    # every duplicate gets the original result — no 500s, no second posting
    assert [r.status_code for r in responses] == [302] * parties
    assert len({r.headers["location"] for r in responses}) == 1
    assert await _ledger_counts() == ONE_POSTING


async def test_concurrent_conflicting_payloads_post_one_and_reject_the_other(
    database, monkeypatch
):
    cash_id, revenue_id = database
    _gate_before_posting(monkeypatch, 2)
    async with _client() as client, asyncio.timeout(20):
        responses = await asyncio.gather(
            client.post("/post-transaction", data=_submission(cash_id, revenue_id)),
            client.post("/post-transaction", data=_submission(cash_id, revenue_id, amount="7.00")),
        )
    assert sorted(r.status_code for r in responses) == [302, 409]
    assert await _ledger_counts() == ONE_POSTING

    # the transaction that committed is the one the 302 points at, and it
    # carries exactly one of the two payloads — never a mix
    winner = next(r for r in responses if r.status_code == 302)
    committed_id = uuid.UUID(winner.headers["location"].rsplit("/", 1)[1])
    async with main_module.engine.connect() as conn:
        rows = (
            await conn.execute(select(ledger_entries.c.transaction_id, ledger_entries.c.amount))
        ).all()
    assert {txn for txn, _ in rows} == {committed_id}
    assert {str(amount) for _, amount in rows} in ({"100.00"}, {"7.00"})


def test_fingerprint_is_deterministic_and_strict():
    entries = [
        {"account_id": "a", "entry_type": "debit", "amount": "100.00", "currency": "USD"},
        {"account_id": "b", "entry_type": "credit", "amount": "100.00", "currency": "USD"},
    ]
    # key order inside each entry does not matter
    reordered = [dict(reversed(list(e.items()))) for e in entries]
    assert request_fingerprint("x", entries) == request_fingerprint("x", reordered)
    # any change to what was submitted does
    assert request_fingerprint("x", entries) != request_fingerprint("y", entries)
    changed = [dict(entries[0], amount="100"), entries[1]]
    assert request_fingerprint("x", entries) != request_fingerprint("x", changed)
