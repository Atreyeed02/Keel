"""
How ledger rows look in the JSON API.

Amounts are strings with exactly two decimal places, never JSON numbers.
Most JSON parsers read a number into a binary float, which cannot hold
0.10 exactly, so a client summing floats would drift from the ledger. A
string forces the client to choose a decimal type deliberately.
"""

from decimal import Decimal
from typing import Any


def money(value: Decimal) -> str:
    # Formatting a Decimal is exact; it never passes through a float.
    return f"{value:.2f}"


def account_json(row: dict[str, Any]) -> dict[str, Any]:
    """An account from `app.domain.reads.account_balances`."""
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "account_type": row["account_type"],
        "currency": row["currency"],
        "normal_side": row["normal_side"],
        "debits": money(row["debits"]),
        "credits": money(row["credits"]),
        # signed by the normal side, exactly as the overview page shows it
        "balance": money(row["balance"]),
        "created_at": row["created_at"].isoformat(),
    }


def transaction_json(transaction: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    """
    A transaction from `app.domain.reads.transaction_with_entries`.

    Entry ids are left out: a rebuild mints new ones (ARCHITECTURE.md §3.3),
    so nothing should hold on to them.
    """
    return {
        "id": str(transaction["id"]),
        "description": transaction["description"],
        "created_at": transaction["created_at"].isoformat(),
        "entries": [
            {
                "account_id": str(entry["account_id"]),
                "account_name": entry["name"],
                "entry_type": entry["entry_type"],
                "amount": money(entry["amount"]),
                "currency": entry["currency"],
            }
            for entry in entries
        ],
    }
