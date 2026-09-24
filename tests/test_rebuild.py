"""
The read model is rebuildable from the event log — checked, not claimed.

Postgres-backed like the page tests, and skipped the same way without
TEST_DATABASE_URL. The fixture here starts from an empty schema rather
than reusing `database` from test_ledger_pages.py: that fixture inserts
its accounts directly, with no account.created events, and a log missing
the accounts its entries point at is exactly what a rebuild must refuse.
"""

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import case, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.schema import accounts, events, ledger_entries, metadata, transactions
from app.domain.accounts import create_account_record, validate_account
from app.domain.rebuild import UnknownEventError, rebuild_read_model
from app.main import app
from scripts.seed_demo_data import DEMO_ACCOUNTS, DEMO_TRANSACTIONS, seed

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def ledger(monkeypatch):
    """An empty schema, with the app pointed at it."""
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL page integration tests")
    test_engine = create_async_engine(TEST_DATABASE_URL)
    monkeypatch.setattr("app.main.engine", test_engine)
    async with test_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)
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
