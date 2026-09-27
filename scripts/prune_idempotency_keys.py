"""
Delete idempotency keys older than the retention window.

    python -m scripts.prune_idempotency_keys                        # count, change nothing
    python -m scripts.prune_idempotency_keys --yes                  # delete keys over 30 days old
    python -m scripts.prune_idempotency_keys --older-than-days 7 --yes

`idempotency_keys` gains a row for every posting and nothing else ever
removes one. This is the cleanup, meant to run on a schedule. The work
is `app.domain.idempotency.prune_idempotency_keys`.

Transactions and events are untouched. What a pruned key gives up is
recognising a retry: resubmitting it posts again. So the window must be
longer than any client will keep retrying, and at least a day, which the
domain function enforces.
"""

import argparse
import asyncio
import sys
from datetime import timedelta

from app.db.engine import engine
from app.domain.idempotency import count_expired_idempotency_keys, prune_idempotency_keys

DEFAULT_RETENTION_DAYS = 30


async def main(older_than_days: int, confirmed: bool) -> int:
    older_than = timedelta(days=older_than_days)
    try:
        async with engine.begin() as conn:
            if not confirmed:
                count = await count_expired_idempotency_keys(conn, older_than)
                print(f"{count} idempotency key(s) are older than {older_than_days} days.")
                print("Nothing changed. Re-run with --yes to delete them.")
                return 1
            deleted = await prune_idempotency_keys(conn, older_than)
    finally:
        await engine.dispose()

    print(f"Deleted {deleted} idempotency key(s) older than {older_than_days} days.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        help=f"retention window in days (default {DEFAULT_RETENTION_DAYS}, minimum 1)",
    )
    parser.add_argument("--yes", action="store_true", help="actually delete them")
    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(main(args.older_than_days, args.yes)))
    except ValueError as exc:
        parser.error(str(exc))
