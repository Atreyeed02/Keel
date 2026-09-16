"""
Populate an empty ledger with a small, realistic demo dataset.

    python -m scripts.seed_demo_data

Everything is written through the domain layer — `validate_account` for
the chart of accounts, `post_transaction` for the entries — rather than
by raw INSERT. That matters for more than tidiness: `post_transaction`
appends the `events` row alongside the read-model rows, so a seeded
database has the same event log it would have had if a human had typed
every transaction into the app. A SQL dump would leave the event log
empty and the event-log page blank, which is precisely the page a demo
most wants to show.

Safe to run repeatedly: it refuses to touch a ledger that already has
accounts rather than duplicating the dataset.
"""

import asyncio
import uuid
from decimal import Decimal

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.engine import engine
from app.db.schema import accounts
from app.domain.accounts import validate_account
from app.domain.ledger import EntryInput, post_transaction

# A one-person consultancy's books: every account type the ledger
# recognises, plus a second currency so the per-currency balance rule is
# visible in the demo rather than only in the tests.
#
# (name, account_type, currency)
DEMO_ACCOUNTS = [
    ("Cash", "asset", "USD"),
    ("EUR operating account", "asset", "EUR"),
    ("Equipment loan payable", "liability", "USD"),
    ("Owner's capital", "equity", "USD"),
    ("Consulting revenue", "revenue", "USD"),
    ("European consulting revenue", "revenue", "EUR"),
    ("Office rent", "expense", "USD"),
    ("Software subscriptions", "expense", "USD"),
]

# (description, [(account name, debit|credit, amount, currency), ...])
#
# Each transaction must net to zero per currency — that invariant is
# enforced by post_transaction, so a typo here fails the seed loudly
# instead of writing a lopsided ledger.
DEMO_TRANSACTIONS = [
    (
        "Opening capital contribution",
        [
            ("Cash", "debit", "25000.00", "USD"),
            ("Owner's capital", "credit", "25000.00", "USD"),
        ],
    ),
    (
        "Equipment loan drawdown",
        [
            ("Cash", "debit", "12000.00", "USD"),
            ("Equipment loan payable", "credit", "12000.00", "USD"),
        ],
    ),
    (
        "Invoice 1001 settled — Northwind Trading",
        [
            ("Cash", "debit", "4800.00", "USD"),
            ("Consulting revenue", "credit", "4800.00", "USD"),
        ],
    ),
    (
        "Invoice 1002 settled — Contoso Logistics",
        [
            ("Cash", "debit", "6250.00", "USD"),
            ("Consulting revenue", "credit", "6250.00", "USD"),
        ],
    ),
    # Three-line entry: one payment covering two expense accounts, so the
    # transaction-detail page shows something other than a 1:1 pair.
    (
        "January operating costs",
        [
            ("Office rent", "debit", "2400.00", "USD"),
            ("Software subscriptions", "debit", "318.50", "USD"),
            ("Cash", "credit", "2718.50", "USD"),
        ],
    ),
    (
        "February office rent",
        [
            ("Office rent", "debit", "2400.00", "USD"),
            ("Cash", "credit", "2400.00", "USD"),
        ],
    ),
    (
        "Equipment loan repayment",
        [
            ("Equipment loan payable", "debit", "1500.00", "USD"),
            ("Cash", "credit", "1500.00", "USD"),
        ],
    ),
    (
        "Annual analytics subscription",
        [
            ("Software subscriptions", "debit", "1188.00", "USD"),
            ("Cash", "credit", "1188.00", "USD"),
        ],
    ),
    # The euro entry. It balances within EUR and never nets against the
    # USD entries above — which is the whole point of enforcing the
    # invariant per currency rather than over a single total.
    (
        "Invoice 1003 settled — Kessler GmbH (EUR)",
        [
            ("EUR operating account", "debit", "1800.00", "EUR"),
            ("European consulting revenue", "credit", "1800.00", "EUR"),
        ],
    ),
    (
        "Invoice 1004 settled — Fabrikam Design",
        [
            ("Cash", "debit", "3400.00", "USD"),
            ("Consulting revenue", "credit", "3400.00", "USD"),
        ],
    ),
]


async def seed(conn: AsyncConnection) -> tuple[int, int, int]:
    """Write the demo dataset. Returns (accounts, transactions, entries)."""
    account_ids: dict[str, uuid.UUID] = {}
    for name, account_type, currency in DEMO_ACCOUNTS:
        account = validate_account(
            {"name": name, "account_type": account_type, "currency": currency}
        )
        # Server-generated id, the same way POST /accounts does it.
        account_id = uuid.uuid4()
        await conn.execute(
            insert(accounts).values(
                id=account_id,
                name=account.name,
                account_type=account.account_type,
                currency=account.currency,
            )
        )
        account_ids[account.name] = account_id

    entry_count = 0
    for description, lines in DEMO_TRANSACTIONS:
        entries = [
            EntryInput(
                account_id=account_ids[account_name],
                entry_type=entry_type,
                amount=Decimal(amount),
                currency=currency,
            )
            for account_name, entry_type, amount, currency in lines
        ]
        await post_transaction(conn, entries, description)
        entry_count += len(entries)

    return len(DEMO_ACCOUNTS), len(DEMO_TRANSACTIONS), entry_count


async def main() -> None:
    try:
        # One connection, one transaction: either the whole demo ledger
        # lands or none of it does.
        async with engine.begin() as conn:
            existing = await conn.scalar(select(func.count()).select_from(accounts))
            if existing:
                print(
                    f"Ledger already has {existing} account(s) — nothing was seeded.\n"
                    "Drop the data first if you want a clean demo set."
                )
                return
            account_count, transaction_count, entry_count = await seed(conn)
    finally:
        await engine.dispose()

    currencies = sorted({currency for *_, currency in DEMO_ACCOUNTS})
    print(
        f"Seeded {account_count} accounts and {transaction_count} transactions "
        f"({entry_count} ledger entries, {len(currencies)} currencies: "
        f"{', '.join(currencies)})."
    )


if __name__ == "__main__":
    asyncio.run(main())
