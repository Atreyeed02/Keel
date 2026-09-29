"""
Hard caps on how many accounts and transactions the ledger will hold.

A public demo on a small database needs a ceiling that does not depend on
who is writing: the rate limit (app/ratelimit.py) slows one client down,
but many clients, or one with many addresses, could still fill the disk.
`MAX_ACCOUNTS` and `MAX_TRANSACTIONS` (app/config.py) are that ceiling.

The check is a count in the caller's transaction, just before the insert,
so a refused write rolls back with everything else, the idempotency claim
included. It takes no lock, so concurrent writes that all see room can each
commit, overshooting the cap by at most the number of connections writing at
once. That is noise next to a size limit, which is all a cap is for.
"""

from sqlalchemy import Table, func, select
from sqlalchemy.ext.asyncio import AsyncConnection


class LedgerFullError(Exception):
    """Raised instead of a write that would take the ledger past a cap."""


async def assert_room(conn: AsyncConnection, table: Table, limit: int, noun: str) -> None:
    """Raise `LedgerFullError` if `table` already holds `limit` rows. 0 means no cap."""
    if limit <= 0:
        return
    if await conn.scalar(select(func.count()).select_from(table)) >= limit:
        plural = "" if limit == 1 else "s"
        raise LedgerFullError(f"the ledger already holds its maximum of {limit} {noun}{plural}")
