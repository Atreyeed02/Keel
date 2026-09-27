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
"""

import hashlib
import json
import uuid

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.schema import idempotency_keys
from app.domain.ledger import EntryInput, post_transaction


class IdempotencyConflictError(ValueError):
    """Raised when a key already used for one request arrives with a different one."""


def request_fingerprint(description: str, raw_entries: list[dict[str, str]]) -> str:
    """
    SHA-256 over the request as submitted. `sort_keys=True` makes the
    serialisation deterministic, so the same logical request always
    hashes the same.

    Hashes the raw form values, not the parsed ones, so "100" and "100.00"
    are different requests. That is deliberately strict: a client reusing a
    key should be resending the same bytes.
    """
    return hashlib.sha256(
        json.dumps({"description": description, "entries": raw_entries}, sort_keys=True).encode()
    ).hexdigest()


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
