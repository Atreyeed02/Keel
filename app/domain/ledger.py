"""
Double-entry posting logic.

The one invariant this module exists to protect: for any transaction,
sum(debit amounts) == sum(credit amounts), per currency. Everything
else (accounts, balances, reports) is derived from entries that
satisfy this.

Posting a transaction does two things atomically:
  1. Appends an `events` row (the source-of-truth log).
  2. Writes the `transactions` + `ledger_entries` rows (the read model).

If step 2 ever needs to be rebuilt, it's replayed from `events` by
`app.domain.rebuild.rebuild_read_model` — that's the whole point of
keeping them separate.
"""

import uuid
from collections import defaultdict
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.schema import accounts, events, ledger_entries, transactions
from app.domain.event_versions import CURRENT_VERSION

# The longest description `transactions.description` can hold. Longer
# ones used to reach Postgres and fail there as an unhandled 500.
DESCRIPTION_MAX_LENGTH = transactions.c.description.type.length


class EntryInput(BaseModel):
    account_id: uuid.UUID
    entry_type: str  # "debit" | "credit"
    # Matches the column, Numeric(18, 2). Without it Postgres rounds instead
    # of refusing: 100.005 was stored as 100.01, silently changing what was
    # posted, and 0.001 became 0.00 and failed the `amount > 0` CHECK as an
    # unhandled 500. `100.000` is still accepted; it is exactly 100.00.
    amount: Decimal = Field(max_digits=18, decimal_places=2)
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


class EntryAccountError(ValueError):
    """
    Raised when entries don't line up with the accounts they name.

    One error rather than one per problem kind, because a submission can
    have several at once and the person filling the form should see all of
    them in one response instead of fixing one, resubmitting, and being
    told about the next. `missing` and `mismatched` stay available for a
    caller that wants to treat the two differently; nothing does today,
    which is exactly why they are not separate exception classes.
    """

    def __init__(self, missing: list[str], mismatched: list[str]) -> None:
        self.missing = missing
        self.mismatched = mismatched
        problems = []
        if missing:
            problems.append(f"no account exists with id: {', '.join(missing)}")
        problems.extend(mismatched)
        super().__init__("; ".join(problems))


async def assert_accounts_valid(conn: AsyncConnection, entries: list[EntryInput]) -> None:
    """
    Check every entry against the account it points at.

    An account's `currency` is fixed when the account is created and never
    changes, so it — not the submitted entry — is the authority on what
    currency that account holds. Letting an entry name a different one
    would put two currencies in a single account, and the overview page
    sums each account's balance under `accounts.currency`, so the result
    is two incompatible amounts silently added into one number.

    One query for all entries, not one per entry: a transaction can name
    the same account more than once (both sides of an internal transfer),
    so the distinct ids are what gets looked up.
    """
    wanted = {e.account_id for e in entries}
    rows = (
        (
            await conn.execute(
                select(accounts.c.id, accounts.c.name, accounts.c.currency).where(
                    accounts.c.id.in_(wanted)
                )
            )
        )
        .mappings()
        .all()
    )
    known = {row["id"]: row for row in rows}

    missing = sorted(str(account_id) for account_id in wanted - known.keys())

    # Currency is only checked against accounts that were actually found —
    # asking what currency a nonexistent account holds is meaningless — but
    # the entries pointing at accounts that DO exist are still checked, so a
    # submission with one bad id and one bad currency reports both at once
    # rather than revealing the second only after the first is fixed.
    #
    # dict.fromkeys: de-duplicate repeats (the same account named twice)
    # while keeping the order the entries were submitted in.
    mismatched = list(
        dict.fromkeys(
            f"account '{known[e.account_id]['name']}' is {known[e.account_id]['currency']}, "
            f"but an entry was submitted as {e.currency}"
            for e in entries
            if e.account_id in known and e.currency != known[e.account_id]["currency"]
        )
    )

    if missing or mismatched:
        raise EntryAccountError(missing, mismatched)


def validate_description(description: str | None) -> None:
    """Raise if `description` is too long for `transactions.description`."""
    if description is not None and len(description) > DESCRIPTION_MAX_LENGTH:
        raise ValueError(f"description must be at most {DESCRIPTION_MAX_LENGTH} characters")


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
    validate_description(description)
    # Before the balance check: an entry naming an account that doesn't
    # exist would otherwise reach Postgres and fail the foreign key as an
    # unhandled IntegrityError, and one naming the wrong currency would
    # post silently. Both become ordinary validation errors here.
    await assert_accounts_valid(conn, entries)
    assert_balanced(entries)

    txn_id = uuid.uuid4()

    await conn.execute(insert(transactions).values(id=txn_id, description=description))
    # Each entry keeps its place in the submission, in the read model and in
    # the event, so the order it is shown in is the order it was entered in
    # and a rebuild reproduces it.
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
                "position": position,
            }
            for position, e in enumerate(entries)
        ],
    )
    await conn.execute(
        insert(events).values(
            id=uuid.uuid4(),
            aggregate_type="transaction",
            aggregate_id=txn_id,
            event_type="transaction.posted",
            payload={
                "schema_version": CURRENT_VERSION["transaction.posted"],
                "description": description,
                "entries": [
                    {**e.model_dump(mode="json"), "position": position}
                    for position, e in enumerate(entries)
                ],
            },
        )
    )

    return txn_id
