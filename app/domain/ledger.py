"""
Double-entry posting logic.

The one invariant this module exists to protect: for any transaction,
sum(debit amounts) == sum(credit amounts), per currency. Everything
else (accounts, balances, reports) is derived from entries that
satisfy this.

Posting a transaction does two things atomically:
  1. Appends an `events` row (the source-of-truth log).
  2. Writes the `transactions` + `ledger_entries` rows (the read model).

If step 2 ever needs to be rebuilt, it's replayed from `events` —
that's the whole point of keeping them separate.
"""

import uuid
from collections import defaultdict
from decimal import Decimal

from pydantic import BaseModel, field_validator
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.schema import events, ledger_entries, transactions


class EntryInput(BaseModel):
    account_id: uuid.UUID
    entry_type: str  # "debit" | "credit"
    amount: Decimal
    currency: str

    @field_validator("entry_type")
    @classmethod
    def validate_entry_type(cls, v: str) -> str:
        if v not in ("debit", "credit"):
            raise ValueError("entry_type must be 'debit' or 'credit'")
        return v

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("amount must be positive")
        return v


class UnbalancedTransactionError(ValueError):
    """Raised when debits and credits don't net to zero per currency."""


def assert_balanced(entries: list[EntryInput]) -> None:
    """Sum debits and credits per currency; raise if any currency doesn't net to zero."""
    net: dict[str, Decimal] = defaultdict(Decimal)
    for e in entries:
        net[e.currency] += e.amount if e.entry_type == "debit" else -e.amount

    unbalanced = {ccy: total for ccy, total in net.items() if total != 0}
    if unbalanced:
        raise UnbalancedTransactionError(f"transaction does not balance per currency: {unbalanced}")


async def post_transaction(
    conn: AsyncConnection,
    entries: list[EntryInput],
    description: str | None = None,
) -> uuid.UUID:
    """
    Validate and atomically post a double-entry transaction.

    Caller owns the connection's transaction boundary — this function
    issues statements but doesn't commit, so it composes with an
    idempotency check wrapping it in the same DB transaction.
    """
    if len(entries) < 2:
        raise ValueError("a transaction needs at least two entries")
    assert_balanced(entries)

    txn_id = uuid.uuid4()

    await conn.execute(insert(transactions).values(id=txn_id, description=description))
    await conn.execute(
        insert(ledger_entries),
        [
            {
                "id": uuid.uuid4(),
                "transaction_id": txn_id,
                "account_id": e.account_id,
                "entry_type": e.entry_type,
                "amount": e.amount,
                "currency": e.currency,
            }
            for e in entries
        ],
    )
    await conn.execute(
        insert(events).values(
            id=uuid.uuid4(),
            aggregate_type="transaction",
            aggregate_id=txn_id,
            event_type="transaction.posted",
            payload={
                "description": description,
                "entries": [e.model_dump(mode="json") for e in entries],
            },
        )
    )

    return txn_id
