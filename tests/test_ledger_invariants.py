"""
The ledger's invariants, checked at the database rather than through a form.

Three groups:

- **The database enforces the rules itself.** Writes that go around the
  app, as a migration, a `psql` session or a buggy importer would, are
  refused: unbalanced entries, a tampered amount, a deleted leg, any
  UPDATE, DELETE or TRUNCATE of `events`.
- **Failure is atomic.** A posting that fails at any point, including
  after its rows were written but before commit, leaves no trace in the
  read model or the log.
- **The log and the read model agree.** After a realistic mix of writes,
  every transaction and account in the read model has exactly one event,
  the entries match the event payloads, and the whole ledger balances
  per currency (the trial balance).

Postgres-backed; skipped without TEST_DATABASE_URL.
"""

import os
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import case, delete, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app import main as main_module
from app.db.schema import accounts, events, ledger_entries, metadata, transactions
from app.domain.accounts import create_account_record, validate_account
from app.domain.ledger import EntryAccountError, EntryInput, post_transaction
from scripts.seed_demo_data import seed
from tests.support import reset_schema

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def ledger(monkeypatch):
    """An empty schema with a USD and a EUR account pair, created through the domain layer."""
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL page integration tests")
    test_engine = create_async_engine(TEST_DATABASE_URL)
    monkeypatch.setattr("app.main.engine", test_engine)
    async with test_engine.begin() as conn:
        await reset_schema(conn)
        ids = {}
        for name, account_type, currency in [
            ("Cash", "asset", "USD"),
            ("Revenue", "revenue", "USD"),
            ("EUR bank", "asset", "EUR"),
            ("EUR revenue", "revenue", "EUR"),
        ]:
            account = validate_account(
                {"name": name, "account_type": account_type, "currency": currency}
            )
            ids[name] = await create_account_record(conn, account)
    yield test_engine, ids
    async with test_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
    await test_engine.dispose()


def _entry(account_id, entry_type, amount, currency="USD"):
    return {
        "id": uuid.uuid4(),
        "account_id": account_id,
        "entry_type": entry_type,
        "amount": Decimal(amount),
        "currency": currency,
    }


async def _write_around_the_app(engine, txn_id, entries):
    """Insert a transaction and its entries with raw SQL, bypassing every Python check."""
    async with engine.begin() as conn:
        await conn.execute(insert(transactions).values(id=txn_id, description="raw"))
        await conn.execute(
            insert(ledger_entries), [dict(e, transaction_id=txn_id) for e in entries]
        )


async def _count(engine, table) -> int:
    async with engine.connect() as conn:
        return await conn.scalar(select(func.count()).select_from(table))


async def _post(engine, ids, amount="100.00") -> uuid.UUID:
    async with engine.begin() as conn:
        return await post_transaction(
            conn,
            [
                EntryInput(
                    account_id=ids["Cash"], entry_type="debit", amount=amount, currency="USD"
                ),
                EntryInput(
                    account_id=ids["Revenue"], entry_type="credit", amount=amount, currency="USD"
                ),
            ],
            "Sale",
        )


# --- the database enforces the rules itself --------------------------------


async def test_database_accepts_a_balanced_write_around_the_app(ledger):
    """The control for the rejections below: the trigger is not refusing everything."""
    engine, ids = ledger
    await _write_around_the_app(
        engine,
        uuid.uuid4(),
        [_entry(ids["Cash"], "debit", "10.00"), _entry(ids["Revenue"], "credit", "10.00")],
    )
    assert await _count(engine, ledger_entries) == 2


async def test_database_rejects_unbalanced_entries_and_keeps_nothing(ledger):
    engine, ids = ledger
    txn_id = uuid.uuid4()
    with pytest.raises(IntegrityError, match="do not balance") as caught:
        await _write_around_the_app(
            engine,
            txn_id,
            [_entry(ids["Cash"], "debit", "100.00"), _entry(ids["Revenue"], "credit", "99.99")],
        )
    # the message says which transaction, by how much, in which currency
    assert f"transaction {txn_id} is off by 0.01 USD" in str(caught.value)
    # no partially committed transaction: the header row went with the entries
    assert await _count(engine, transactions) == 0
    assert await _count(engine, ledger_entries) == 0


async def test_database_balances_each_currency_separately(ledger):
    """
    100 USD against 100 EUR nets to zero only if the currencies are added
    together, which is meaningless. Each currency must balance on its own.
    """
    engine, ids = ledger
    with pytest.raises(IntegrityError, match="do not balance"):
        await _write_around_the_app(
            engine,
            uuid.uuid4(),
            [
                _entry(ids["Cash"], "debit", "100.00", "USD"),
                _entry(ids["EUR revenue"], "credit", "100.00", "EUR"),
            ],
        )


async def test_database_rejects_tampering_with_a_posted_amount(ledger):
    engine, ids = ledger
    txn_id = await _post(engine, ids)
    with pytest.raises(IntegrityError, match="do not balance"):
        async with engine.begin() as conn:
            await conn.execute(
                update(ledger_entries)
                .where(ledger_entries.c.transaction_id == txn_id)
                .where(ledger_entries.c.entry_type == "debit")
                .values(amount=Decimal("1000.00"))
            )
    async with engine.connect() as conn:
        amounts = (await conn.scalars(select(ledger_entries.c.amount))).all()
    assert amounts == [Decimal("100.00"), Decimal("100.00")]


async def test_database_rejects_deleting_one_leg(ledger):
    engine, ids = ledger
    await _post(engine, ids)
    with pytest.raises(IntegrityError, match="do not balance"):
        async with engine.begin() as conn:
            await conn.execute(
                delete(ledger_entries).where(ledger_entries.c.entry_type == "credit")
            )
    assert await _count(engine, ledger_entries) == 2


async def test_database_rejects_moving_an_entry_to_another_transaction(ledger):
    """An UPDATE is checked against the transaction the entry left, not only the one it joined."""
    engine, ids = ledger
    first, second = await _post(engine, ids), await _post(engine, ids)
    with pytest.raises(IntegrityError, match=f"transaction {first}"):
        async with engine.begin() as conn:
            # first loses its debit, second gains one: both are now unbalanced
            await conn.execute(
                update(ledger_entries)
                .where(ledger_entries.c.transaction_id == first)
                .where(ledger_entries.c.entry_type == "debit")
                .values(transaction_id=second)
            )


@pytest.mark.parametrize(
    "statement",
    [
        update(events).values(event_type="transaction.reversed"),
        delete(events),
        text("TRUNCATE events"),
    ],
    ids=["update", "delete", "truncate"],
)
async def test_the_event_log_cannot_be_rewritten(ledger, statement):
    engine, _ids = ledger
    before = await _count(engine, events)
    with pytest.raises(IntegrityError, match="events is append-only"):
        async with engine.begin() as conn:
            await conn.execute(statement)
    assert await _count(engine, events) == before == 4


# --- failure is atomic ------------------------------------------------------


async def test_a_posting_rejected_by_validation_leaves_no_trace(ledger):
    engine, ids = ledger
    with pytest.raises(EntryAccountError):
        async with engine.begin() as conn:
            await post_transaction(
                conn,
                [
                    EntryInput(
                        account_id=ids["Cash"], entry_type="debit", amount="5", currency="USD"
                    ),
                    EntryInput(
                        account_id=uuid.uuid4(), entry_type="credit", amount="5", currency="USD"
                    ),
                ],
            )
    assert await _count(engine, transactions) == 0
    assert await _count(engine, ledger_entries) == 0
    assert await _count(engine, events) == 4  # only the four account.created events


async def test_a_failure_after_the_rows_are_written_rolls_back_log_and_read_model(ledger):
    """
    `post_transaction` has already written the transaction, its entries and
    its event when something later in the same database transaction fails,
    as a failed idempotency write would. None of the three may survive:
    a read-model row without its event, or an event without its rows,
    would make the ledger disagree with its own log.
    """
    engine, ids = ledger

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with engine.begin() as conn:
            await post_transaction(
                conn,
                [
                    EntryInput(
                        account_id=ids["Cash"], entry_type="debit", amount="5", currency="USD"
                    ),
                    EntryInput(
                        account_id=ids["Revenue"], entry_type="credit", amount="5", currency="USD"
                    ),
                ],
            )
            assert await conn.scalar(select(func.count()).select_from(ledger_entries)) == 2
            raise Boom
    assert await _count(engine, transactions) == 0
    assert await _count(engine, ledger_entries) == 0
    assert await _count(engine, events) == 4


# --- the log and the read model agree ---------------------------------------


async def test_read_model_and_event_log_agree_and_the_ledger_balances(ledger):
    engine, ids = ledger
    async with engine.begin() as conn:
        await seed(conn)
    transport = ASGITransport(app=main_module.app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        response = await client.post(
            "/post-transaction",
            data={
                "description": "Mixed settlement",
                "submission_key": "agree-1",
                "account_id": [str(ids[n]) for n in ("Cash", "Revenue", "EUR bank", "EUR revenue")],
                "entry_type": ["debit", "credit", "debit", "credit"],
                "amount": ["40.00", "40.00", "30.00", "30.00"],
                "currency": ["USD", "USD", "EUR", "EUR"],
            },
        )
    assert response.status_code == 302

    async with engine.connect() as conn:
        log = (await conn.execute(select(events))).mappings().all()
        account_ids = set((await conn.scalars(select(accounts.c.id))).all())
        txn_ids = set((await conn.scalars(select(transactions.c.id))).all())
        entries = (await conn.execute(select(ledger_entries))).mappings().all()
        trial_balance = (
            await conn.execute(
                select(
                    ledger_entries.c.currency,
                    func.sum(
                        case(
                            (ledger_entries.c.entry_type == "debit", ledger_entries.c.amount),
                            else_=-ledger_entries.c.amount,
                        )
                    ),
                ).group_by(ledger_entries.c.currency)
            )
        ).all()

    # exactly one event per account and per transaction, and no event for
    # anything the read model does not have
    created = [e["aggregate_id"] for e in log if e["event_type"] == "account.created"]
    posted = {e["aggregate_id"]: e for e in log if e["event_type"] == "transaction.posted"}
    assert sorted(created) == sorted(account_ids)
    assert len(posted) == len(log) - len(created)
    assert set(posted) == txn_ids

    # every transaction's entries are exactly what its event recorded
    for txn_id, event in posted.items():
        from_log = sorted(
            (e["account_id"], e["entry_type"], Decimal(e["amount"]), e["currency"])
            for e in event["payload"]["entries"]
        )
        from_read_model = sorted(
            (str(e["account_id"]), e["entry_type"], e["amount"], e["currency"])
            for e in entries
            if e["transaction_id"] == txn_id
        )
        assert from_log == from_read_model, f"transaction {txn_id} disagrees with its event"

    # the trial balance: every currency nets to exactly zero across the ledger
    assert {currency for currency, _ in trial_balance} == {"USD", "EUR"}
    assert all(net == 0 for _, net in trial_balance), trial_balance
