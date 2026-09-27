"""
Idempotent posting: one submission key, one committed ledger effect.

A client that never saw the response to its first attempt — dropped
connection, double-click, browser refresh — sends the same request again
with the same key. The first attempt to commit posts the transaction;
every other attempt gets that transaction's id back and posts nothing.

The key is *claimed* before anything is posted, with

    INSERT INTO idempotency_keys ... ON CONFLICT (key) DO NOTHING

and that ordering is the whole design. The previous version looked the
key up with a plain SELECT and inserted it only after posting. Two
concurrent requests could then both see "no such key" and both post.
The primary key still stopped the second one from committing, so the
ledger never double-posted, but that request failed with an unhandled
IntegrityError: a 500 for a retry that should have been answered with
the original result.

Claiming first closes that window, because of how Postgres treats a
unique-index conflict with a row another transaction has not committed
yet: the second INSERT *waits* for the first transaction to finish.

- The first commits → the conflict is real, DO NOTHING, and a fresh
  SELECT (READ COMMITTED takes a new snapshot per statement) sees the
  committed key and its response. The caller replays it.
- The first rolls back — say its entries named a nonexistent account →
  its claim vanishes with it, the waiting INSERT succeeds, and this
  request posts. A rejected attempt does not burn the key.

The claim, the posting and the stored response are all written in the
caller's transaction, so a committed key always has its response. There
is no "claimed but unfinished" state for anyone to observe.

Keys are not kept forever: `prune_idempotency_keys` deletes old ones. A
key only protects a retry while it exists, so once it is pruned the same
submission posts again. The retention window has to outlast any client's
retry window.
"""

import hashlib
import json
import uuid
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.schema import idempotency_keys
from app.domain.ledger import EntryInput, post_transaction


class IdempotencyConflictError(ValueError):
    """Raised when a key already used for one request arrives with a different one."""


def request_fingerprint(description: str, raw_entries: list[dict[str, str]]) -> str:
    """
    SHA-256 over a form submission as submitted. `sort_keys=True` makes
    the serialisation deterministic, so the same logical request always
    hashes the same.

    Hashes the raw form values, not the parsed ones, so "100" and "100.00"
    are different requests. That is deliberately strict: a browser resending
    a form resends the same bytes. The JSON API uses `entries_fingerprint`
    instead; ARCHITECTURE.md §4 explains why the two differ.
    """
    return hashlib.sha256(
        json.dumps({"description": description, "entries": raw_entries}, sort_keys=True).encode()
    ).hexdigest()


def entries_fingerprint(description: str | None, entries: list[EntryInput]) -> str:
    """
    SHA-256 over what a JSON request would write, not over its bytes.

    A JSON client re-serialises its request on a retry, and nothing obliges
    it to produce the same bytes: key order, whitespace, `"100"` against
    `"100.00"`, a lower-case currency or UUID. Hashing the raw body would
    answer such a genuine retry with a 409. So the hash is over the
    validated request in canonical form:

    - amounts at exactly two decimal places (they have been validated to
      have at most two), currencies upper-cased, account ids as canonical
      UUID strings;
    - entries in the order they were sent, because that order is stored
      (each entry's `position`) and shown;
    - the description exactly as sent, after an empty one has become None,
      which is how it is stored.

    Two requests hash the same exactly when they would post the same
    transaction. Anything that would change what is stored changes the
    hash, and a key reused for it is a 409. Entry order is part of that now.
    It costs a genuine retry nothing: JSON arrays are ordered, and a client
    re-serialising a request keeps its array in order, whatever it does
    with object keys.

    The `format` marker keeps these hashes apart from `request_fingerprint`
    ones, so a key first used by the HTML form and then sent to the API is
    a conflict, never a replay of a request made through the other door. It
    went from `json-v1` to `json-v2` when entry order joined the canonical
    form.
    """
    canonical_entries = [
        {
            "account_id": str(e.account_id),
            "entry_type": e.entry_type,
            "amount": f"{e.amount:.2f}",
            "currency": e.currency.upper(),
        }
        for e in entries
    ]
    canonical = {"format": "json-v2", "description": description, "entries": canonical_entries}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


async def post_transaction_once(
    conn: AsyncConnection,
    key: str,
    fingerprint: str,
    entries: list[EntryInput],
    description: str | None,
) -> tuple[uuid.UUID, bool]:
    """
    Post `entries` under `key` unless that key has already been used.

    Returns `(transaction_id, replayed)`. `replayed` is True when the key
    was already committed and nothing new was written.

    Raises `IdempotencyConflictError` when the key was committed for a
    request with a different fingerprint. Anything `post_transaction`
    raises propagates unchanged, and rolling back the caller's transaction
    releases the claim along with everything else.

    Same contract as `post_transaction`: the caller owns the transaction
    boundary. The claim only protects anything if it commits atomically
    with the posting it guards.
    """
    claimed = await conn.scalar(
        pg_insert(idempotency_keys)
        .values(key=key, request_hash=fingerprint)
        .on_conflict_do_nothing(index_elements=[idempotency_keys.c.key])
        .returning(idempotency_keys.c.key)
    )

    if claimed is None:
        saved = (
            (
                await conn.execute(
                    select(idempotency_keys.c.request_hash, idempotency_keys.c.response_body).where(
                        idempotency_keys.c.key == key
                    )
                )
            )
            .mappings()
            .one()
        )
        if saved["request_hash"] != fingerprint:
            raise IdempotencyConflictError("submission key was used for another request")
        return uuid.UUID(saved["response_body"]["transaction_id"]), True

    transaction_id = await post_transaction(conn, entries, description)
    await conn.execute(
        update(idempotency_keys)
        .where(idempotency_keys.c.key == key)
        .values(response_body={"transaction_id": str(transaction_id)}, response_status="302")
    )
    return transaction_id, False


# Below this a pruned key could belong to a retry still in flight, which
# would then post a second time.
MINIMUM_RETENTION = timedelta(days=1)


def _expired(older_than: timedelta):
    if older_than < MINIMUM_RETENTION:
        raise ValueError(
            f"idempotency keys must be kept for at least {MINIMUM_RETENTION.days} day"
        )
    return idempotency_keys.c.created_at < func.now() - older_than


async def count_expired_idempotency_keys(conn: AsyncConnection, older_than: timedelta) -> int:
    """How many keys `prune_idempotency_keys` would delete."""
    expired = _expired(older_than)
    return await conn.scalar(select(func.count()).select_from(idempotency_keys).where(expired))


async def prune_idempotency_keys(conn: AsyncConnection, older_than: timedelta) -> int:
    """
    Delete keys claimed more than `older_than` ago; return how many went.

    Only the keys go. Their transactions and events are ledger history and
    stay. What is lost is the ability to recognise a retry of one of those
    submissions, which is why `older_than` has a floor.

    Measured against the database clock, the same one that stamped
    `created_at`. A claim that is not committed yet is invisible to the
    DELETE, so an in-flight posting can never lose its key mid-request.
    """
    expired = _expired(older_than)  # validated before the connection is touched
    result = await conn.execute(delete(idempotency_keys).where(expired))
    return result.rowcount
