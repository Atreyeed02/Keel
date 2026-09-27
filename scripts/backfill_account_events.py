"""
Append `account.created` for accounts that predate the event.

    python -m scripts.backfill_account_events          # list them, change nothing
    python -m scripts.backfill_account_events --yes    # append their events

A database with accounts created before 2026-09-24 has no event for
them, and `python -m scripts.rebuild_read_model` refuses to run on it
until this has. The work is `app.domain.rebuild.backfill_account_events`,
which documents what the appended events contain. Running it on a
database with nothing to backfill does nothing.

Each account is recorded as the read model describes it now. That
includes any account inserted around the app, which a rebuild would
otherwise have dropped, so review the list before passing `--yes`.
"""

import argparse
import asyncio
import sys

from app.db.engine import engine
from app.domain.rebuild import accounts_without_events, backfill_account_events


async def main(confirmed: bool) -> int:
    try:
        async with engine.begin() as conn:
            if not confirmed:
                missing = await accounts_without_events(conn)
                if not missing:
                    print("Every account already has an account.created event.")
                    return 0
                print(f"{len(missing)} account(s) have no account.created event:")
                for account in missing:
                    print(
                        f"  {account['name']} ({account['account_type']}, "
                        f"{account['currency']}), created {account['created_at']:%Y-%m-%d}"
                    )
                print("Nothing changed. Re-run with --yes to append their events.")
                return 1
            backfilled = await backfill_account_events(conn)
    finally:
        await engine.dispose()

    print(f"Appended account.created for {len(backfilled)} account(s).")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("--yes", action="store_true", help="actually append the events")
    sys.exit(asyncio.run(main(parser.parse_args().yes)))
