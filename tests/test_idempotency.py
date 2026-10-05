"""
One submission key, one committed ledger effect: checked sequentially,
under a conflicting payload, after a rejected attempt, and under
concurrency. Plus retention: pruning old keys, and what that gives up.

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
import html
import os
import re
import uuid
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import create_async_engine

from app import main as main_module
from app.db.schema import accounts, events, idempotency_keys, ledger_entries, metadata, transactions
from app.domain.idempotency import (
    count_expired_idempotency_keys,
    prune_idempotency_keys,
    request_fingerprint,
)
from scripts import prune_idempotency_keys as prune_script
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
    # each retry lands on the same transaction, which says it was already posted
    assert {r.headers["location"] for r in retries} == {first.headers["location"] + "?already=1"}
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
    # the form, saying so, with a link to what the key already posted
    page = html.unescape(reused.text)
    assert "This form already posted a transaction, and you've changed it since." in page
    assert f'href="{first.headers["location"]}"' in reused.text
    assert "it will be recorded as a separate, second transaction" in page
    # and a new key, so posting it again is a second transaction, not a conflict
    (key,) = re.findall(r'name="submission_key" value="([^"]+)"', reused.text)
    assert key != "same-key"
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
    # one transaction: the first posted it, the rest were told it was already posted
    locations = [r.headers["location"] for r in responses]
    assert len({location.removesuffix("?already=1") for location in locations}) == 1
    assert sorted(location.endswith("?already=1") for location in locations) == [
        False,
        *[True] * (parties - 1),
    ]
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


# --- retention ----------------------------------------------------------------


async def _age_key(key: str, days: int) -> None:
    """Backdate a key's claim, as if it had been stored `days` ago."""
    async with main_module.engine.begin() as conn:
        await conn.execute(
            update(idempotency_keys)
            .where(idempotency_keys.c.key == key)
            .values(created_at=func.now() - timedelta(days=days))
        )


async def _keys() -> set[str]:
    async with main_module.engine.connect() as conn:
        return set((await conn.scalars(select(idempotency_keys.c.key))).all())


async def test_pruning_deletes_only_expired_keys_and_keeps_the_ledger(database):
    cash_id, revenue_id = database
    async with _client() as client:
        for key in ("old", "recent"):
            response = await client.post(
                "/post-transaction", data=_submission(cash_id, revenue_id, key=key)
            )
            assert response.status_code == 302
    await _age_key("old", days=31)

    async with main_module.engine.begin() as conn:
        assert await count_expired_idempotency_keys(conn, timedelta(days=30)) == 1
        assert await prune_idempotency_keys(conn, timedelta(days=30)) == 1

    assert await _keys() == {"recent"}
    counts = await _ledger_counts()
    # the postings themselves are history and stay
    assert (counts["transactions"], counts["events"]) == (2, 2)


async def test_a_pruned_key_no_longer_recognises_its_retry(database):
    """The trade-off retention makes, pinned down: past the window a retry posts again."""
    cash_id, revenue_id = database
    async with _client() as client:
        first = await client.post("/post-transaction", data=_submission(cash_id, revenue_id))
        await _age_key("same-key", days=31)
        async with main_module.engine.begin() as conn:
            await prune_idempotency_keys(conn, timedelta(days=30))
        late_retry = await client.post("/post-transaction", data=_submission(cash_id, revenue_id))
    assert late_retry.status_code == 302
    assert late_retry.headers["location"] != first.headers["location"]
    assert (await _ledger_counts())["transactions"] == 2


async def test_pruning_refuses_a_window_under_a_day():
    # refused before the connection is used, so no database is needed
    with pytest.raises(ValueError, match="at least 1 day"):
        await prune_idempotency_keys(None, timedelta(hours=23))


async def test_prune_script_counts_then_deletes(database, monkeypatch, capsys):
    cash_id, revenue_id = database
    monkeypatch.setattr("scripts.prune_idempotency_keys.engine", main_module.engine)
    async with _client() as client:
        await client.post("/post-transaction", data=_submission(cash_id, revenue_id))
    await _age_key("same-key", days=45)

    assert await prune_script.main(older_than_days=30, confirmed=False) == 1
    assert "1 idempotency key(s) are older than 30 days" in capsys.readouterr().out
    assert await _keys() == {"same-key"}

    assert await prune_script.main(older_than_days=60, confirmed=True) == 0
    assert "Deleted 0" in capsys.readouterr().out
    assert await prune_script.main(older_than_days=30, confirmed=True) == 0
    assert "Deleted 1" in capsys.readouterr().out
    assert await _keys() == set()
