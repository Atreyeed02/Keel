"""
Read-side queries shared by the HTML pages and the JSON API.

The overview page, an account's page and `GET /api/accounts` show the same
balances, and the transaction-detail page and `GET /api/transactions/{id}`
show the same entries. Each query lives here once, so the two surfaces cannot drift into
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


async def account_balances(
    conn: AsyncConnection, account_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    """
    Every account with its debit and credit totals and its balance, ordered
    by account type and then name. With `account_id`, just that one (an
    empty list if it does not exist).

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
    query = (
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
    if account_id is not None:
        query = query.where(accounts.c.id == account_id)
    rows = (await conn.execute(query)).mappings().all()
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


async def account_entries(
    conn: AsyncConnection, account_id: uuid.UUID, limit: int, offset: int
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """
    One account's entries, oldest first, as its T-account lists them: how
    many there are on each side in all, and one page of them, each with its
    transaction's id, number, description and time.

    Oldest first is by transaction number, then by each entry's place in its
    transaction (`position`, as `transaction_with_entries` orders it), so two
    entries one transaction makes on the same account keep their order.
    """
    counts = {"debit": 0, "credit": 0}
    counts.update(
        (
            await conn.execute(
                select(ledger_entries.c.entry_type, func.count())
                .where(ledger_entries.c.account_id == account_id)
                .group_by(ledger_entries.c.entry_type)
            )
        ).all()
    )
    rows = (
        (
            await conn.execute(
                select(
                    ledger_entries.c.id,
                    ledger_entries.c.entry_type,
                    ledger_entries.c.amount,
                    ledger_entries.c.currency,
                    transactions.c.id.label("transaction_id"),
                    transactions.c.sequence,
                    transactions.c.description,
                    transactions.c.created_at,
                )
                .join(transactions, transactions.c.id == ledger_entries.c.transaction_id)
                .where(ledger_entries.c.account_id == account_id)
                .order_by(
                    transactions.c.sequence,
                    ledger_entries.c.position.asc().nulls_last(),
                    ledger_entries.c.created_at,
                    ledger_entries.c.id,
                )
                .offset(offset)
                .limit(limit)
            )
        )
        .mappings()
        .all()
    )
    return counts, [dict(row) for row in rows]


async def other_sides(
    conn: AsyncConnection, account_id: uuid.UUID, transaction_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict[str, Any]]]:
    """
    For each of the given transactions, its entries on accounts other than
    this one, in entry order: the other side of what this account's T-account
    lists. Each with its account's name and side.
    """
    if not transaction_ids:
        return {}
    rows = (
        await conn.execute(
            select(ledger_entries.c.transaction_id, ledger_entries.c.entry_type, accounts.c.name)
            .join(accounts, accounts.c.id == ledger_entries.c.account_id)
            .where(
                ledger_entries.c.transaction_id.in_(transaction_ids),
                ledger_entries.c.account_id != account_id,
            )
            .order_by(
                ledger_entries.c.transaction_id,
                ledger_entries.c.position.asc().nulls_last(),
                ledger_entries.c.created_at,
                ledger_entries.c.id,
            )
        )
    ).mappings()
    out: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(row["transaction_id"], []).append(dict(row))
    return out


async def transaction_with_entries(
    conn: AsyncConnection, transaction_id: uuid.UUID
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """
    One transaction and its entries, each entry with its account's name and
    type. `(None, [])` if there is no such transaction.

    Entries are in submission order, by `position`. Rows written before
    `position` existed have none; they sort after numbered ones and then by
    `(created_at, id)`, the order they have always been shown in
    (ARCHITECTURE.md §3.3).
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
                .order_by(
                    ledger_entries.c.position.asc().nulls_last(),
                    ledger_entries.c.created_at,
                    ledger_entries.c.id,
                )
            )
        )
        .mappings()
        .all()
    )
    return dict(transaction), [dict(entry) for entry in entries]
