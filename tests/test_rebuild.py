"""
The read model is rebuildable from the event log — checked, not claimed.

Postgres-backed like the page tests, and skipped the same way without
TEST_DATABASE_URL. The fixture here starts from an empty schema rather
than reusing `database` from test_ledger_pages.py: that fixture inserts
its accounts directly, with no account.created events, and a log missing
the accounts its entries point at is exactly what a rebuild must refuse.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import case, delete, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.schema import accounts, events, ledger_entries, metadata, transactions
from app.domain.accounts import create_account_record, validate_account
from app.domain.ledger import EntryInput, post_transaction
from app.domain.rebuild import (
    UnknownEventError,
    accounts_missing_from_log,
    accounts_without_events,
    backfill_account_events,
    rebuild_read_model,
)
from app.main import app
from scripts import backfill_account_events as backfill_script
from scripts import rebuild_read_model as rebuild_script
from scripts.seed_demo_data import DEMO_ACCOUNTS, DEMO_TRANSACTIONS, seed
from tests.support import reset_schema

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def ledger(monkeypatch):
    """An empty schema, with the app pointed at it."""
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL page integration tests")
    test_engine = create_async_engine(TEST_DATABASE_URL)
    monkeypatch.setattr("app.main.engine", test_engine)
    async with test_engine.begin() as conn:
        await reset_schema(conn)
    yield test_engine
    async with test_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
    await test_engine.dispose()


async def _account_ids(engine) -> dict[str, uuid.UUID]:
    async with engine.connect() as conn:
        rows = (await conn.execute(select(accounts.c.name, accounts.c.id))).all()
    return {name: account_id for name, account_id in rows}


async def _build_realistic_ledger(engine, client) -> None:
    """
    A ledger written the three ways the app writes one: the demo seed (all
    five account types, USD and EUR, a three-line entry), POST /accounts,
    and POST /post-transaction — including one transaction that moves two
    currencies at once and one with no description.
    """
    async with engine.begin() as conn:
        await seed(conn)

    for name, account_type, currency in [
        ("GBP bank", "asset", "gbp"),
        ("UK consulting revenue", "revenue", "GBP"),
    ]:
        response = await client.post(
            "/accounts", data={"name": name, "account_type": account_type, "currency": currency}
        )
        assert response.status_code == 302

    ids = await _account_ids(engine)
    postings = [
        (
            "Mixed settlement — USD and GBP legs",
            [
                ("Cash", "debit", "500.00", "USD"),
                ("Consulting revenue", "credit", "500.00", "USD"),
                ("GBP bank", "debit", "250.00", "GBP"),
                ("UK consulting revenue", "credit", "250.00", "GBP"),
            ],
        ),
        (
            "",  # stored as NULL — the rebuild must keep it NULL, not ""
            [
                ("Office rent", "debit", "75.25", "USD"),
                ("Cash", "credit", "75.25", "USD"),
            ],
        ),
    ]
    for description, lines in postings:
        response = await client.post(
            "/post-transaction",
            data={
                "description": description,
                "submission_key": str(uuid.uuid4()),
                "account_id": [str(ids[name]) for name, *_ in lines],
                "entry_type": [side for _, side, _, _ in lines],
                "amount": [amount for *_, amount, _ in lines],
                "currency": [ccy for *_, ccy in lines],
            },
        )
        assert response.status_code == 302, response.text


async def _snapshot(engine, client) -> dict:
    """
    The read model's meaning, not its storage: every field a person or a
    query relies on, plus the pages built from them. ledger_entries.id is
    deliberately absent — replay mints new entry ids (see app/domain/rebuild.py).
    """
    signed = case(
        (ledger_entries.c.entry_type == "debit", ledger_entries.c.amount),
        else_=-ledger_entries.c.amount,
    )
    async with engine.connect() as conn:
        account_rows = (
            await conn.execute(
                select(
                    accounts.c.id,
                    accounts.c.name,
                    accounts.c.account_type,
                    accounts.c.currency,
                    accounts.c.created_at,
                ).order_by(accounts.c.id)
            )
        ).all()
        # in posting order: a rebuild must keep the order, not just the set
        transaction_rows = (
            await conn.execute(
                select(
                    transactions.c.id, transactions.c.description, transactions.c.created_at
                ).order_by(transactions.c.sequence)
            )
        ).all()
        entry_rows = sorted(
            (
                await conn.execute(
                    select(
                        ledger_entries.c.transaction_id,
                        ledger_entries.c.account_id,
                        ledger_entries.c.entry_type,
                        ledger_entries.c.amount,
                        ledger_entries.c.currency,
                        ledger_entries.c.created_at,
                    )
                )
            ).all()
        )
        # The same aggregation the overview runs: per-account balance...
        balances = (
            await conn.execute(
                select(accounts.c.id, func.coalesce(func.sum(signed), 0))
                .outerjoin(ledger_entries, ledger_entries.c.account_id == accounts.c.id)
                .group_by(accounts.c.id)
                .order_by(accounts.c.id)
            )
        ).all()
        # ...and per-currency debit and credit totals.
        currency_totals = (
            await conn.execute(
                select(
                    ledger_entries.c.currency,
                    func.sum(
                        case((ledger_entries.c.entry_type == "debit", ledger_entries.c.amount))
                    ),
                    func.sum(
                        case((ledger_entries.c.entry_type == "credit", ledger_entries.c.amount))
                    ),
                )
                .group_by(ledger_entries.c.currency)
                .order_by(ledger_entries.c.currency)
            )
        ).all()

    pages = {}
    for path in ["/", "/transactions"]:
        response = await client.get(path)
        assert response.status_code == 200
        pages[path] = response.text

    return {
        "accounts": account_rows,
        "transactions": transaction_rows,
        "entries": entry_rows,
        "balances": balances,
        "currency_totals": currency_totals,
        "pages": pages,
    }


async def _entry_ids(engine) -> set[uuid.UUID]:
    async with engine.connect() as conn:
        return set((await conn.scalars(select(ledger_entries.c.id))).all())


async def test_rebuild_reproduces_the_read_model(ledger):
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        await _build_realistic_ledger(ledger, client)
        before = await _snapshot(ledger, client)
        entry_ids_before = await _entry_ids(ledger)

        async with ledger.begin() as conn:
            await rebuild_read_model(conn)

        after = await _snapshot(ledger, client)
        entry_ids_after = await _entry_ids(ledger)

    # Guard against a vacuous pass: the ledger really has the mix it claims.
    assert len(before["accounts"]) == len(DEMO_ACCOUNTS) + 2
    assert len(before["transactions"]) == len(DEMO_TRANSACTIONS) + 2
    assert {row.account_type for row in before["accounts"]} == {
        "asset",
        "liability",
        "equity",
        "revenue",
        "expense",
    }
    assert [row.currency for row in before["currency_totals"]] == ["EUR", "GBP", "USD"]
    assert None in {row.description for row in before["transactions"]}

    for key in ("accounts", "transactions", "entries", "balances", "currency_totals"):
        assert after[key] == before[key], f"{key} changed across the rebuild"
    assert after["pages"] == before["pages"], "a rendered page changed across the rebuild"

    # And the rebuild really happened: every entry row was recreated, which
    # is visible only in the one column the replay cannot reproduce.
    assert entry_ids_after.isdisjoint(entry_ids_before)
    assert len(entry_ids_after) == len(entry_ids_before)


async def test_rebuild_rejects_an_unknown_event_type(ledger):
    async with ledger.begin() as conn:
        account = validate_account({"name": "Cash", "account_type": "asset", "currency": "USD"})
        account_id = await create_account_record(conn, account)
        await conn.execute(
            insert(events).values(
                id=uuid.uuid4(),
                aggregate_type="account",
                aggregate_id=account_id,
                event_type="account.renamed",
                payload={"name": "Petty cash"},
            )
        )

    with pytest.raises(UnknownEventError, match="account.renamed"):
        async with ledger.begin() as conn:
            await rebuild_read_model(conn)

    # The failed replay rolled back with its transaction: the truncate
    # never took effect and the account is still there.
    async with ledger.connect() as conn:
        assert await conn.scalar(select(accounts.c.name)) == "Cash"


async def test_rebuild_refuses_a_log_missing_its_account_events(ledger):
    """
    Accounts written before account.created existed — or by anything that
    inserts into `accounts` directly — are absent from the log. Replaying an
    entry that names one must fail, not rebuild a ledger without it.
    """
    orphan_id, revenue_id = uuid.uuid4(), uuid.uuid4()
    async with ledger.begin() as conn:
        await conn.execute(
            insert(accounts),
            [
                {"id": orphan_id, "name": "Cash", "account_type": "asset", "currency": "USD"},
                {"id": revenue_id, "name": "Revenue", "account_type": "revenue", "currency": "USD"},
            ],
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        response = await client.post(
            "/post-transaction",
            data={
                "description": "Sale against event-less accounts",
                "submission_key": "orphan-key",
                "account_id": [str(orphan_id), str(revenue_id)],
                "entry_type": ["debit", "credit"],
                "amount": ["10.00", "10.00"],
                "currency": ["USD", "USD"],
            },
        )
    assert response.status_code == 302

    with pytest.raises(IntegrityError, match="ledger_entries_account_id_fkey"):
        async with ledger.begin() as conn:
            await rebuild_read_model(conn)

    async with ledger.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(accounts)) == 2
        assert await conn.scalar(select(func.count()).select_from(transactions)) == 1


def _client():
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    )


async def _corrupt_read_model(engine) -> None:
    """
    Damage the projection the ways a bad manual fix or a buggy importer
    would, all around the app: a whole transaction's rows lost, an account
    renamed, and an account row that no event ever created. (Deleting a
    single entry is not on the list: the balance trigger refuses it.)
    """
    async with engine.begin() as conn:
        lost = await conn.scalar(
            select(transactions.c.id).where(transactions.c.description == "February office rent")
        )
        await conn.execute(delete(ledger_entries).where(ledger_entries.c.transaction_id == lost))
        await conn.execute(delete(transactions).where(transactions.c.id == lost))
        await conn.execute(
            update(accounts).where(accounts.c.name == "Cash").values(name="Cash (edited by hand)")
        )
        await conn.execute(
            insert(accounts).values(
                id=uuid.uuid4(), name="Phantom", account_type="asset", currency="USD"
            )
        )


def _posting(ids, key, description="After the rebuild"):
    return {
        "description": description,
        "submission_key": key,
        "account_id": [str(ids["Cash"]), str(ids["Consulting revenue"])],
        "entry_type": ["debit", "credit"],
        "amount": ["12.34", "12.34"],
        "currency": ["USD", "USD"],
    }


async def test_rebuild_repairs_a_corrupted_read_model(ledger):
    """Recovery: the log is the source of truth, so replaying it undoes damage to the projection."""
    async with _client() as client:
        await _build_realistic_ledger(ledger, client)
        original = await _snapshot(ledger, client)

        await _corrupt_read_model(ledger)
        damaged = await _snapshot(ledger, client)

        async with ledger.begin() as conn:
            await rebuild_read_model(conn)
        repaired = await _snapshot(ledger, client)

    # guard against a vacuous pass: the corruption really changed what people see
    for key in ("accounts", "transactions", "balances", "pages"):
        assert damaged[key] != original[key], f"corruption did not affect {key}"
    assert repaired == original


async def test_rebuild_is_repeatable_and_posting_continues_after_it(ledger):
    async with _client() as client:
        await _build_realistic_ledger(ledger, client)
        async with ledger.begin() as conn:
            await rebuild_read_model(conn)
        once = await _snapshot(ledger, client)
        async with ledger.begin() as conn:
            await rebuild_read_model(conn)
        twice = await _snapshot(ledger, client)
        assert twice == once, "a second rebuild changed the read model"

        # RESTART IDENTITY renumbered transactions.sequence from 1, so a new
        # posting must continue that numbering, not collide with it or
        # sort behind the replayed rows
        ids = await _account_ids(ledger)
        response = await client.post("/post-transaction", data=_posting(ids, "after-rebuild"))
        assert response.status_code == 302
        listing = await client.get("/transactions")

    async with ledger.connect() as conn:
        count = await conn.scalar(select(func.count()).select_from(transactions))
        newest = await conn.scalar(
            select(transactions.c.description).order_by(transactions.c.sequence.desc()).limit(1)
        )
        top_sequence = await conn.scalar(select(func.max(transactions.c.sequence)))
    assert newest == "After the rebuild"
    assert top_sequence == count
    # and the listing shows it first, ahead of every replayed transaction
    first_row = listing.text.index("/transaction-detail/")
    assert listing.text.find("After the rebuild", first_row) < listing.text.find(
        "Mixed settlement", first_row
    )


async def test_a_posting_during_a_rebuild_waits_for_it_and_is_not_lost(ledger):
    """
    The replay's TRUNCATE holds an ACCESS EXCLUSIVE lock on the read model
    until the rebuild commits, so a posting that arrives mid-rebuild blocks
    rather than writing into a half-built projection. The test observes the
    wait in pg_stat_activity rather than sleeping and hoping.
    """
    async with _client() as client:
        await _build_realistic_ledger(ledger, client)
        ids = await _account_ids(ledger)

        async with ledger.connect() as rebuild_conn:
            rebuild = await rebuild_conn.begin()
            await rebuild_read_model(rebuild_conn)  # done, but not committed

            posting = asyncio.create_task(
                client.post(
                    "/post-transaction", data=_posting(ids, "mid-rebuild", "Posted mid-rebuild")
                )
            )
            async with ledger.connect() as observer:
                waiting = 0
                for _ in range(200):
                    waiting = await observer.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                        )
                    )
                    if waiting:
                        break
                    # Postgres caches pg_stat_activity for the rest of a
                    # transaction on first read, so each poll has to end
                    # the observer's transaction or it keeps seeing the
                    # moment before the posting blocked.
                    await observer.rollback()
                    await asyncio.sleep(0.025)
            assert waiting == 1, "the posting never blocked on the rebuild's lock"
            assert not posting.done()

            await rebuild.commit()

        response = await asyncio.wait_for(posting, timeout=10)
        assert response.status_code == 302

        # it landed on the rebuilt tables and in the log, so a further
        # rebuild keeps it
        before = await _snapshot(ledger, client)
        async with ledger.begin() as conn:
            await rebuild_read_model(conn)
        after = await _snapshot(ledger, client)

    assert "Posted mid-rebuild" in {row.description for row in before["transactions"]}
    assert after == before


async def test_rebuild_script_refuses_without_confirmation_then_repairs(
    ledger, monkeypatch, capsys
):
    monkeypatch.setattr("scripts.rebuild_read_model.engine", ledger)
    async with _client() as client:
        await _build_realistic_ledger(ledger, client)
        original = await _snapshot(ledger, client)

        assert await rebuild_script.main(confirmed=False) == 1
        assert "Nothing changed" in capsys.readouterr().out
        assert await _snapshot(ledger, client) == original

        await _corrupt_read_model(ledger)
        assert await rebuild_script.main(confirmed=True) == 0
        report = capsys.readouterr().out
        assert "drifted from the log" in report
        assert "Cash (USD)" in report
        assert "Phantom: not in the log, removed" in report
        assert await _snapshot(ledger, client) == original

        # a healthy ledger: nothing to correct
        assert await rebuild_script.main(confirmed=True) == 0
        assert "Every account balance is unchanged" in capsys.readouterr().out


async def _build_legacy_ledger(engine) -> dict[str, uuid.UUID]:
    """
    A database from before account.created existed: accounts inserted with
    no event, each with its own creation time, one of them never used, and
    transactions posted normally on top.
    """
    ids = {name: uuid.uuid4() for name in ("Cash", "Revenue", "Dormant")}
    async with engine.begin() as conn:
        await conn.execute(
            insert(accounts),
            [
                {
                    "id": ids[name],
                    "name": name,
                    "account_type": account_type,
                    "currency": "USD",
                    "created_at": datetime(2026, 1, day, 9, 30, tzinfo=UTC),
                }
                for day, (name, account_type) in enumerate(
                    [("Cash", "asset"), ("Revenue", "revenue"), ("Dormant", "expense")], start=1
                )
            ],
        )
    for amount in ("40.00", "60.00"):
        async with engine.begin() as conn:
            await post_transaction(
                conn,
                [
                    EntryInput(
                        account_id=ids["Cash"],
                        entry_type="debit",
                        amount=Decimal(amount),
                        currency="USD",
                    ),
                    EntryInput(
                        account_id=ids["Revenue"],
                        entry_type="credit",
                        amount=Decimal(amount),
                        currency="USD",
                    ),
                ],
                "Legacy sale",
            )
    return ids


async def test_backfill_makes_a_legacy_ledger_rebuildable(ledger):
    ids = await _build_legacy_ledger(ledger)
    async with _client() as client:
        before = await _snapshot(ledger, client)

        async with ledger.connect() as conn:
            assert await accounts_missing_from_log(conn) == sorted([ids["Cash"], ids["Revenue"]])
            last_posting = await conn.scalar(select(func.max(events.c.sequence)))

        async with ledger.begin() as conn:
            backfilled = await backfill_account_events(conn)
        # every event-less account, the unused one too, oldest first
        assert backfilled == [ids["Cash"], ids["Revenue"], ids["Dormant"]]

        async with ledger.connect() as conn:
            assert await accounts_missing_from_log(conn) == []
            assert await accounts_without_events(conn) == []
            appended = (
                await conn.execute(
                    select(events.c.sequence, events.c.payload).where(
                        events.c.event_type == "account.created"
                    )
                )
            ).all()
        # Appended after the postings that use them, which is why replay
        # takes account events first, and marked as backfilled.
        assert all(sequence > last_posting for sequence, _ in appended)
        assert all(payload["backfilled"] is True for _, payload in appended)

        async with ledger.begin() as conn:
            await rebuild_read_model(conn)
        after = await _snapshot(ledger, client)

    # Same accounts (Dormant included) with their original created_at, same
    # transactions, same balances, same pages.
    assert after == before
    assert {row.name for row in after["accounts"]} == {"Cash", "Revenue", "Dormant"}

    async with ledger.begin() as conn:
        assert await backfill_account_events(conn) == []


async def test_backfill_and_rebuild_scripts_on_a_legacy_ledger(ledger, monkeypatch, capsys):
    monkeypatch.setattr("scripts.rebuild_read_model.engine", ledger)
    monkeypatch.setattr("scripts.backfill_account_events.engine", ledger)
    await _build_legacy_ledger(ledger)
    async with _client() as client:
        original = await _snapshot(ledger, client)

        # the rebuild refuses up front instead of failing on a foreign key
        assert await rebuild_script.main(confirmed=True) == 1
        assert "scripts.backfill_account_events" in capsys.readouterr().out
        assert await _snapshot(ledger, client) == original

        # without --yes the backfill only lists what it would do
        assert await backfill_script.main(confirmed=False) == 1
        listing = capsys.readouterr().out
        assert "3 account(s) have no account.created event" in listing
        assert "Dormant (expense, USD), created 2026-01-03" in listing
        async with ledger.connect() as conn:
            assert await accounts_without_events(conn) != []

        assert await backfill_script.main(confirmed=True) == 0
        assert "Appended account.created for 3 account(s)" in capsys.readouterr().out
        assert await backfill_script.main(confirmed=False) == 0
        assert "Every account already has" in capsys.readouterr().out

        assert await rebuild_script.main(confirmed=True) == 0
        assert "Every account balance is unchanged" in capsys.readouterr().out
        assert await _snapshot(ledger, client) == original
