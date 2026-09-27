"""
Rebuild the read model from the event log, from the command line.

    python -m scripts.rebuild_read_model          # report only, changes nothing
    python -m scripts.rebuild_read_model --yes    # truncate and replay

A thin wrapper around `app.domain.rebuild.rebuild_read_model`, which
does the work and documents exactly what a rebuild does and does not
reproduce. This script adds three things: a refusal to run without
`--yes`, row counts before and after, and a list of every account whose
balance the rebuild changed.

On a healthy ledger that list is empty: the rebuild reproduced what was
there. A non-empty list means the read model had drifted from the log,
for example a row deleted or edited around the app, and the rebuild put
it back. The log is the source of truth, so the post-rebuild balance is
the correct one.

It is safe to run while the app is serving. The replay's `TRUNCATE`
takes an ACCESS EXCLUSIVE lock on the three read-model tables, so a
posting that arrives mid-rebuild waits for the rebuild to commit and
then lands on the rebuilt tables. Nothing is lost; requests are delayed
for as long as the replay takes.

Deliberately not an HTTP route: nothing in the app has authentication
yet, and who may rewrite the whole read model is a separate decision.
"""

import argparse
import asyncio
import sys
import uuid
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.engine import engine
from app.db.schema import accounts, events, ledger_entries, transactions
from app.domain.rebuild import rebuild_read_model

TABLES = (events, accounts, transactions, ledger_entries)


async def _counts(conn: AsyncConnection) -> dict[str, int]:
    return {t.name: await conn.scalar(select(func.count()).select_from(t)) for t in TABLES}


async def _balances(conn: AsyncConnection) -> dict[uuid.UUID, tuple[str, str, Decimal]]:
    """Every account's name, currency and debit-minus-credit balance."""
    signed = case(
        (ledger_entries.c.entry_type == "debit", ledger_entries.c.amount),
        else_=-ledger_entries.c.amount,
    )
    balance = func.coalesce(func.sum(signed), 0)
    rows = await conn.execute(
        select(accounts.c.id, accounts.c.name, accounts.c.currency, balance)
        .outerjoin(ledger_entries, ledger_entries.c.account_id == accounts.c.id)
        .group_by(accounts.c.id, accounts.c.name, accounts.c.currency)
    )
    return {row[0]: (row[1], row[2], row[3]) for row in rows}


async def main(confirmed: bool) -> int:
    try:
        # One transaction: a replay that fails part-way (an unknown event
        # type, an entry naming an account no event created) rolls back
        # and leaves the old read model exactly as it was.
        async with engine.begin() as conn:
            before = await _counts(conn)
            if not confirmed:
                print("Read model now: " + ", ".join(f"{k}={v}" for k, v in before.items()))
                print("Nothing changed. Re-run with --yes to truncate and replay from events.")
                return 1
            balances_before = await _balances(conn)
            await rebuild_read_model(conn)
            after = await _counts(conn)
            balances_after = await _balances(conn)
    finally:
        await engine.dispose()

    print(f"Replayed {after['events']} events.")
    for table in ("accounts", "transactions", "ledger_entries"):
        print(f"  {table:<15} {before[table]:>6} -> {after[table]:>6}")

    # (name, currency, balance before or None if the account was missing, balance after)
    changed = []
    for account_id, (name, currency, balance) in sorted(
        balances_after.items(), key=lambda item: item[1][0]
    ):
        old = balances_before.get(account_id)
        if old is None or old[2] != balance:
            changed.append((name, currency, None if old is None else old[2], balance))
    # in the read model before, but no event ever created them
    vanished = sorted(
        name
        for account_id, (name, *_) in balances_before.items()
        if account_id not in balances_after
    )
    if not changed and not vanished:
        print("Every account balance is unchanged: the read model already matched the log.")
    else:
        print("The read model had drifted from the log. Corrected (debits minus credits):")
        for name, currency, old, new in changed:
            print(f"  {name} ({currency}): {'missing' if old is None else old} -> {new}")
        for name in vanished:
            print(f"  {name}: not in the log, removed")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("--yes", action="store_true", help="actually truncate and replay")
    sys.exit(asyncio.run(main(parser.parse_args().yes)))
