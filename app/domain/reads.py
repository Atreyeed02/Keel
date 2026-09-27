"""
Read-side queries shared by the HTML pages and the JSON API.

The overview page and `GET /api/accounts` show the same balances, and the
transaction-detail page and `GET /api/transactions/{id}` show the same
entries. Each query lives here once, so the two surfaces cannot drift into
reporting different numbers for the same ledger.

Nothing here writes, so none of it has the "caller owns the transaction"
contract the write functions have. Any connection will do.
"""

import uuid
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.schema import accounts, ledger_entries, transactions

# Asset and expense accounts grow with debits; liability, equity and
# revenue accounts grow with credits. ARCHITECTURE.md §2.2.
NORMAL_DEBIT_TYPES = {"asset", "expense"}


def normal_side(account_type: str) -> str:
    return "debit" if account_type in NORMAL_DEBIT_TYPES else "credit"


def _debits():
    return func.coalesce(
        func.sum(case((ledger_entries.c.entry_type == "debit", ledger_entries.c.amount), else_=0)),
        0,
    )


def _credits():
    return func.coalesce(
        func.sum(case((ledger_entries.c.entry_type == "credit", ledger_entries.c.amount), else_=0)),
        0,
    )


async def account_balances(conn: AsyncConnection) -> list[dict[str, Any]]:
    """
    Every account with its debit and credit totals and its balance, ordered
    by account type and then name.

    `raw_balance` is debits minus credits. `balance` is signed by the
    account's normal side, so a revenue account that has taken 100 in
    credits shows 100, not -100: the number an accountant expects.
    """
    raw_balance = func.coalesce(
        func.sum(
            case(
                (ledger_entries.c.entry_type == "debit", ledger_entries.c.amount),
                else_=-ledger_entries.c.amount,
            )
        ),
        0,
    )
    rows = (
        (
            await conn.execute(
                select(
                    accounts.c.id,
                    accounts.c.name,
                    accounts.c.account_type,
                    accounts.c.currency,
                    accounts.c.created_at,
                    _debits().label("debits"),
                    _credits().label("credits"),
                    raw_balance.label("raw_balance"),
                )
                .outerjoin(ledger_entries, ledger_entries.c.account_id == accounts.c.id)
                .group_by(
                    accounts.c.id,
                    accounts.c.name,
                    accounts.c.account_type,
                    accounts.c.currency,
                    accounts.c.created_at,
                )
                .order_by(accounts.c.account_type, accounts.c.name)
            )
        )
        .mappings()
        .all()
    )
    result = []
    for row in rows:
        data = dict(row)
        data["normal_side"] = normal_side(row["account_type"])
        raw = row["raw_balance"]
        data["balance"] = raw if data["normal_side"] == "debit" else -raw
        result.append(data)
    return result


async def currency_totals(conn: AsyncConnection) -> list[dict[str, Any]]:
    """
    Debit and credit totals per currency, which must be equal for each.

    Grouped by currency, not summed across all of them: adding USD to EUR
    produces a real number that means nothing. Entries are guaranteed to
    carry their account's currency, so grouping on the entry column gives
    one honest total per currency.
    """
    rows = (
        (
            await conn.execute(
                select(
                    ledger_entries.c.currency,
                    _debits().label("debits"),
                    _credits().label("credits"),
                )
                .group_by(ledger_entries.c.currency)
                .order_by(ledger_entries.c.currency)
            )
        )
        .mappings()
        .all()
    )
    return [dict(row, delta=row["debits"] - row["credits"]) for row in rows]


async def transaction_with_entries(
    conn: AsyncConnection, transaction_id: uuid.UUID
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """
    One transaction and its entries, each entry with its account's name and
    type. `(None, [])` if there is no such transaction.

    Entries are ordered by `(created_at, id)`. Within one transaction every
    entry shares `created_at`, so that order is arbitrary but stable until a
    rebuild; see ARCHITECTURE.md §8.
    """
    transaction = (
        (await conn.execute(select(transactions).where(transactions.c.id == transaction_id)))
        .mappings()
        .one_or_none()
    )
    if transaction is None:
        return None, []
    entries = (
        (
            await conn.execute(
                select(ledger_entries, accounts.c.name, accounts.c.account_type)
                .join(accounts, accounts.c.id == ledger_entries.c.account_id)
                .where(ledger_entries.c.transaction_id == transaction_id)
                .order_by(ledger_entries.c.created_at, ledger_entries.c.id)
            )
        )
        .mappings()
        .all()
    )
    return dict(transaction), [dict(entry) for entry in entries]
