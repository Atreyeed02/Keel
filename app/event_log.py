"""
What the event log shows for each event: a heading in words, what it
recorded, and the raw event as the database stores it.

Everything comes from the event's own payload, so the page shows what the
event recorded, not what the tables beside it say now. The one exception is
an account's name on a posted transaction's line: the payload names accounts
by id, so the name comes from the accounts table. Accounts are never renamed,
and an id with no account is shown as the id.

Nothing here decides anything. A posted transaction balances because the
domain refuses one that doesn't (app/domain/ledger.py); `balanced` only
reports what the payload says, so damaged data would say so rather than be
shown as balanced.
"""

import json
import uuid
from datetime import UTC
from decimal import Decimal
from typing import Any

from app.domain.reads import normal_side
from app.transactions_view import entry_totals

# The demo's own February rent (scripts/seed_demo_data.py), undone: the
# explainer's example on the demo, never posted.
REVERSAL_EXAMPLE = {
    "number": 6,
    "lines": (("debit", "Cash", "2,400.00"), ("credit", "Office rent", "2,400.00")),
}


def account_ids(rows: list[dict[str, Any]]) -> set[uuid.UUID]:
    """The accounts the page's posted transactions name, to look their names up."""
    ids = set()
    for row in rows:
        if row["event_type"] == "transaction.posted":
            for entry in row["payload"].get("entries", []):
                try:
                    ids.add(uuid.UUID(str(entry.get("account_id"))))
                except ValueError:
                    pass
    return ids


def _lines(payload: dict[str, Any], names: dict[uuid.UUID, str]) -> list[dict[str, Any]]:
    entries = payload.get("entries", [])
    if all("position" in entry for entry in entries):
        entries = sorted(entries, key=lambda entry: entry["position"])
    lines = []
    for entry in entries:
        account_id = str(entry.get("account_id"))
        try:
            name = names.get(uuid.UUID(account_id))
        except ValueError:
            name = None
        lines.append(
            {
                "entry_type": entry.get("entry_type"),
                "name": name,
                "account_id": account_id,
                "currency": entry.get("currency"),
                "amount": Decimal(str(entry.get("amount"))),
            }
        )
    return lines


def describe(
    row: dict[str, Any],
    names: dict[uuid.UUID, str],
    numbers: dict[uuid.UUID, int],
) -> dict[str, Any]:
    """
    One event as the timeline shows it. `names` maps account ids to names,
    `numbers` transaction ids to their "No. N".
    """
    payload = row["payload"]
    version = payload.get("schema_version")
    item: dict[str, Any] = {
        "id": row["id"],
        "number": row["sequence"],
        "event_type": row["event_type"],
        "created_at": row["created_at"],
        "kind": "other",
        "raw": {
            "aggregate": f"{row['aggregate_type']} / {row['aggregate_id']}",
            "schema_version": str(version)
            if version is not None
            else "1 (not recorded; replayed as version 1)",
            "recorded_at": row["created_at"].astimezone(UTC).isoformat(),
            "payload": json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
            "names_accounts": row["event_type"] == "transaction.posted",
        },
    }
    if row["event_type"] == "transaction.posted":
        lines = _lines(payload, names)
        totals = entry_totals(lines)
        item.update(
            kind="transaction",
            title=payload.get("description") or "Untitled transaction",
            lines=lines,
            totals=totals,
            balanced=bool(totals) and all(t["difference"] == 0 for t in totals),
            transaction_id=row["aggregate_id"],
            transaction_number=numbers.get(row["aggregate_id"]),
        )
    elif row["event_type"] == "account.created":
        account_type = payload.get("account_type", "")
        item.update(
            kind="account",
            title=payload.get("name", ""),
            account_type=account_type.capitalize(),
            currency=payload.get("currency"),
            normal_side=normal_side(account_type),
        )
    return item
