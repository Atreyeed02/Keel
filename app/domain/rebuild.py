"""
Rebuilding the read model from the event log.

`accounts`, `transactions` and `ledger_entries` are a projection of
`events`. This module is the proof: it throws the projection away and
recomputes it by replaying every event in `sequence` order.

What a rebuild reproduces exactly:

- account ids and transaction ids — each is the event's `aggregate_id`,
  so every entry still points at the right account, and anything holding
  a transaction id (a bookmarked detail page, a stored idempotency
  response) still resolves;
- every account's name, type and currency; every transaction's
  description; every entry's account, side, amount and currency;
- `created_at` on all three tables. Each row is stamped with its event's
  `created_at`, which is the value it had originally: the row and its
  event were written in one database transaction, and Postgres `now()`
  is transaction-start time, so they always shared it.

What it does not:

- `ledger_entries.id`. The `transaction.posted` payload does not carry
  entry ids, so replay mints new ones. Nothing references an entry by id
  — no foreign key points at `ledger_entries`, and no query looks one up —
  but see ARCHITECTURE.md §3.3 for the one place the value is visible.
- `transactions.sequence` values. The identity restarts at 1 and is
  reassigned in event order, so relative order is kept and gaps are not.
"""

import uuid
from decimal import Decimal

from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.schema import accounts, events, ledger_entries, transactions


class UnknownEventError(ValueError):
    """Raised when replay meets an event type it has no rule for."""


async def rebuild_read_model(conn: AsyncConnection) -> None:
    """
    Discard the read model and replay it from `events`.

    Destructive, and the same contract as `post_transaction`: the caller
    owns the transaction boundary. This issues statements but doesn't
    commit, so a replay that fails part-way — an unknown event type, an
    entry naming an account no event created — rolls back with the
    caller's transaction and leaves the old read model in place.

    An event type without a replay rule raises rather than being skipped.
    Skipping would produce a read model that silently disagrees with the
    log, which is the one outcome a rebuild exists to rule out.
    """
    # One statement for all three tables: Postgres refuses to truncate a
    # table another table references unless the referencing table is
    # truncated with it, so listing them together is FK-safe by
    # construction. RESTART IDENTITY resets `transactions.sequence`.
    await conn.execute(text("TRUNCATE ledger_entries, transactions, accounts RESTART IDENTITY"))

    # Loaded in full rather than streamed — fine at this ledger's size, and
    # the first thing to change if the log ever outgrows memory.
    log = (await conn.execute(select(events).order_by(events.c.sequence))).mappings().all()

    for event in log:
        payload = event["payload"]

        if event["event_type"] == "account.created":
            await conn.execute(
                insert(accounts).values(
                    id=event["aggregate_id"],
                    name=payload["name"],
                    account_type=payload["account_type"],
                    currency=payload["currency"],
                    created_at=event["created_at"],
                )
            )

        elif event["event_type"] == "transaction.posted":
            txn_id = event["aggregate_id"]
            await conn.execute(
                insert(transactions).values(
                    id=txn_id,
                    description=payload["description"],
                    created_at=event["created_at"],
                )
            )
            await conn.execute(
                insert(ledger_entries),
                [
                    {
                        "id": uuid.uuid4(),
                        "transaction_id": txn_id,
                        "account_id": uuid.UUID(entry["account_id"]),
                        "entry_type": entry["entry_type"],
                        "amount": Decimal(entry["amount"]),
                        "currency": entry["currency"],
                        "created_at": event["created_at"],
                    }
                    for entry in payload["entries"]
                ],
            )

        else:
            raise UnknownEventError(
                f"no replay rule for event_type {event['event_type']!r} "
                f"(event {event['id']}, sequence {event['sequence']})"
            )
