"""
Wipe the public demo's ledger and restore the demo dataset.

    python -m scripts.reset_demo_data          # say what would go, change nothing
    python -m scripts.reset_demo_data --yes    # wipe it and reseed

This deletes every account, transaction, entry, event and idempotency key,
then writes `scripts.seed_demo_data`'s dataset again, so the demo comes back
exactly as a fresh seed leaves it, sequences starting from 1.

It refuses to run unless ENVIRONMENT=demo, and checks that before it
connects to anything, so a production ledger or a developer's local one
cannot be wiped by running the wrong command in the wrong shell. Without
`--yes` it only counts what it would delete.

The event log is append-only, enforced by the `events_append_only` trigger,
which refuses TRUNCATE (app/db/schema.py). This script is the one sanctioned
exception. It disables that trigger, truncates and enables it again inside
one transaction, so no other session ever sees the log unguarded, and a
failure anywhere, reseeding included, rolls back to the ledger as it was,
trigger on. Disabling a trigger needs the table's owner: the role that ran
the migrations, which on a single-database deployment is the app's own.
"""

import argparse
import asyncio
import sys

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import settings
from app.db.engine import engine
from app.db.schema import accounts, events, transactions
from scripts.seed_demo_data import seed

RESET_ENVIRONMENT = "demo"

# Locked in the order a posting first touches them (its key, the cap's count,
# its accounts, then the writes), so a reset never deadlocks with a posting:
# it waits at the first table the posting holds until the posting commits. A
# page read can still deadlock with it, by holding one of these tables while
# asking for another the reset already has. Postgres then aborts one of the
# two, and either way nothing is lost: a reset that loses rolls back whole and
# can simply be run again.
LEDGER_TABLES = "idempotency_keys, transactions, accounts, ledger_entries, events"


async def ledger_counts(conn: AsyncConnection) -> dict[str, int]:
    return {
        table.name: await conn.scalar(select(func.count()).select_from(table))
        for table in (accounts, transactions, events)
    }


async def reset(conn: AsyncConnection) -> tuple[int, int, int]:
    """Empty the ledger and reseed it, in the caller's transaction. Returns `seed`'s counts."""
    await conn.execute(text(f"LOCK TABLE {LEDGER_TABLES} IN ACCESS EXCLUSIVE MODE"))
    await conn.execute(text("ALTER TABLE events DISABLE TRIGGER events_append_only"))
    await conn.execute(text(f"TRUNCATE {LEDGER_TABLES} RESTART IDENTITY"))
    await conn.execute(text("ALTER TABLE events ENABLE TRIGGER events_append_only"))
    return await seed(conn)


async def main(confirmed: bool) -> int:
    if settings.environment.lower() != RESET_ENVIRONMENT:
        print(
            f"Refusing to reset: ENVIRONMENT is {settings.environment!r}, not "
            f"{RESET_ENVIRONMENT!r}. This deletes the whole ledger, so it only runs on "
            "the public demo. Nothing changed."
        )
        return 1
    try:
        async with engine.begin() as conn:
            before = await ledger_counts(conn)
            summary = (
                f"{before['accounts']} account(s), {before['transactions']} transaction(s) "
                f"and {before['events']} event(s)"
            )
            if not confirmed:
                print(f"This would delete {summary} and restore the demo data.")
                print("Nothing changed. Re-run with --yes to reset.")
                return 1
            account_count, transaction_count, _ = await reset(conn)
    finally:
        await engine.dispose()

    print(
        f"Deleted {summary}. Restored the demo data: {account_count} accounts and "
        f"{transaction_count} transactions."
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("--yes", action="store_true", help="actually wipe and reseed")
    sys.exit(asyncio.run(main(parser.parse_args().yes)))
