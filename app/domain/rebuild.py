"""
Rebuilding the read model from the event log.

`accounts`, `transactions` and `ledger_entries` are a projection of
`events`. This module is the proof: it throws the projection away and
recomputes it by replaying the log: every `account.created` event first,
then everything else, each group in `sequence` order.

Accounts go first because of `backfill_account_events`, below. An
account that existed before `account.created` did gets its event
appended afterwards, so it sits later in the log than the transactions
that use it, and a strictly sequential replay would insert those entries
before their account existed. Replaying accounts first changes nothing
else: creating an account depends on nothing, and nothing but creation
ever happens to one, so for a log without backfilled events the result
is the same as strict order.

What a rebuild reproduces exactly:

- account ids and transaction ids — each is the event's `aggregate_id`,
  so every entry still points at the right account, and anything holding
  a transaction id (a bookmarked detail page, a stored idempotency
  response) still resolves;
- every account's name, type and currency; every transaction's
  description; every entry's account, side, amount and currency;
- each entry's `position`, its place in the transaction as submitted.
  An event written before entries carried one gets the entry's index in
  the payload array, which is the same thing: `post_transaction` has
  always written that array in submission order;
- `created_at` on all three tables. Each row is stamped with its event's
  `created_at`, which is the value it had originally: the row and its
  event were written in one database transaction, and Postgres `now()`
  is transaction-start time, so they always shared it. A backfilled
  account event was appended later, so it carries the account's original
  `created_at` in its payload and replay uses that instead.

What it does not:

- `ledger_entries.id`. The `transaction.posted` payload does not carry
  entry ids, so replay mints new ones. Nothing references an entry by id
  — no foreign key points at `ledger_entries`, and no query looks one up.
  Entry order used to fall back on the id; it comes from `position` now.
- `transactions.sequence` values. The identity restarts at 1 and is
  reassigned in event order, so relative order is kept and gaps are not.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import column, exists, func, insert, select, text
from sqlalchemy.dialects.postgresql import JSONB
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
    # the first thing to change if the log ever outgrows memory. Account
    # events first; the module docstring says why.
    is_account = events.c.event_type == "account.created"
    log = (
        (await conn.execute(select(events).order_by(~is_account, events.c.sequence)))
        .mappings()
        .all()
    )

    for event in log:
        payload = event["payload"]

        if event["event_type"] == "account.created":
            # Only a backfilled event carries created_at; see backfill_account_events.
            created_at = (
                datetime.fromisoformat(payload["created_at"])
                if "created_at" in payload
                else event["created_at"]
            )
            await conn.execute(
                insert(accounts).values(
                    id=event["aggregate_id"],
                    name=payload["name"],
                    account_type=payload["account_type"],
                    currency=payload["currency"],
                    created_at=created_at,
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
                        # Events written before entries carried a position
                        # still have them in submission order: the payload
                        # array has always been written that way, and JSONB
                        # keeps array order. So the index is the position.
                        "position": entry.get("position", index),
                    }
                    for index, entry in enumerate(payload["entries"])
                ],
            )

        else:
            raise UnknownEventError(
                f"no replay rule for event_type {event['event_type']!r} "
                f"(event {event['id']}, sequence {event['sequence']})"
            )


def _has_account_event():
    return exists().where(
        events.c.event_type == "account.created", events.c.aggregate_id == accounts.c.id
    )


async def accounts_without_events(conn: AsyncConnection) -> list[dict]:
    """Every account in the read model that no `account.created` event describes."""
    rows = await conn.execute(
        select(accounts)
        .where(~_has_account_event())
        .order_by(accounts.c.created_at, accounts.c.id)
    )
    return [dict(row) for row in rows.mappings()]


async def accounts_missing_from_log(conn: AsyncConnection) -> list[uuid.UUID]:
    """
    Accounts that `transaction.posted` events name but no `account.created`
    event creates. A rebuild cannot succeed while this is non-empty: the
    replayed entries would point at accounts the replay never made.
    """
    entry = func.jsonb_array_elements(events.c.payload["entries"]).table_valued(
        column("value", JSONB)
    )
    referenced = (
        select(entry.c.value["account_id"].astext.cast(accounts.c.id.type))
        .select_from(events)
        .join(entry, text("true"))
        .where(events.c.event_type == "transaction.posted")
    )
    created = select(events.c.aggregate_id).where(events.c.event_type == "account.created")
    return sorted(await conn.scalars(referenced.except_(created)))


async def backfill_account_events(conn: AsyncConnection) -> list[uuid.UUID]:
    """
    Append an `account.created` event for every account that has none.

    Accounts created before the event existed are in the read model but
    not in the log, so a rebuild fails on them (and one with no entries
    would silently vanish). This records each of them as the read model
    describes it now, which is the only description left. Returns the
    ids it backfilled, oldest account first; an empty list means the log
    already covered every account, so running it again is harmless.

    Each event is appended now, with a `sequence` after everything already
    in the log, and carries the account's original `created_at` in its
    payload plus `"backfilled": true`, so the log stays honest about when
    the event was written and a rebuild still restores the original
    timestamp.

    Same contract as `post_transaction`: the caller owns the transaction.
    The lock makes two concurrent backfills run one after the other rather
    than both appending events for the same accounts, and holds account
    creation off until this one commits.
    """
    await conn.execute(text("LOCK TABLE accounts IN SHARE ROW EXCLUSIVE MODE"))
    missing = await accounts_without_events(conn)
    for account in missing:
        await conn.execute(
            insert(events).values(
                id=uuid.uuid4(),
                aggregate_type="account",
                aggregate_id=account["id"],
                event_type="account.created",
                payload={
                    "name": account["name"],
                    "account_type": account["account_type"],
                    "currency": account["currency"],
                    "created_at": account["created_at"].isoformat(),
                    "backfilled": True,
                },
            )
        )
    return [account["id"] for account in missing]
