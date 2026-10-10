"""
What an account's page says besides its figures: why its normal side is the
one it is, what each side of its T-account does to it, the other side of
each entry, and the sum that gives its balance, including a balance below
zero.

The balance itself comes from app/domain/reads.account_balances, the same
number the overview and GET /api/accounts show. Nothing here decides
anything; it only words what the ledger holds.
"""

import uuid
from decimal import Decimal
from typing import Any

from app.transactions_view import SHOWN_ACCOUNTS

MINUS = "−"


def signed_money(value: Decimal) -> str:
    """A balance as the account's page shows it: "43,643.50", or "−50.00" below zero."""
    return f"{MINUS if value < 0 else ''}{abs(value):,.2f}"


def _other(side: str) -> str:
    return "credit" if side == "debit" else "debit"


def why(name: str, account_type: str, normal: str) -> dict[str, str]:
    """
    The pieces of "Cash is an asset account. Asset accounts increase with
    debits, so its normal side is debit: each debit adds to its balance, and
    each credit subtracts from it." The page puts the type and "normal side"
    in as terms, so it gets the words around them.
    """
    return {
        "heading": f"Why {normal}?",
        "article": "an" if account_type[:1] in "aeiou" else "a",
        "type": account_type,
        "lead": f"{name} is",
        "rule": f"{account_type.capitalize()} accounts increase with {normal}s, so its",
        "result": f"is {normal}: each {normal} adds to its balance, and each "
        f"{_other(normal)} subtracts from it.",
    }


def column_note(side: str, normal: str) -> str:
    """What a column of the T-account does to the account: "add to it" or "subtract"."""
    return "add to it" if side == normal else "subtract"


def entries_count(n: int) -> str:
    return f"{n} entr{'y' if n == 1 else 'ies'}"


def other_side(entry_type: str, others: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    The accounts on the opposite side of an entry's transaction, each named
    once in entry order, the first few shown, as the transaction cards name
    theirs. None if every opposite entry is on this account.
    """
    side = _other(entry_type)
    names = list(dict.fromkeys(row["name"] for row in others if row["entry_type"] == side))
    if not names:
        return None
    return {
        "side": side,
        "shown": names[:SHOWN_ACCOUNTS],
        "more": max(len(names) - SHOWN_ACCOUNTS, 0),
    }


def proof(name: str, normal: str, debits: Decimal, credits: Decimal) -> dict[str, Any]:
    """
    The sum under the T-account: the normal side's total minus the other
    side's, which is the balance, and the sentence saying why that way round.
    Below zero, it also says what that means, and where the balance would sit
    on paper.
    """
    first, second = (debits, credits) if normal == "debit" else (credits, debits)
    balance = first - second
    other = _other(normal)
    below = None
    if balance < 0:
        example = "cash overdrawn" if normal == "debit" else "more repaid than was borrowed"
        below = (
            f"Below zero: its {other}s are larger than its {normal}s, so more has been taken "
            f"off than added, such as {example}. On paper, this balance would sit on the "
            f"{other} side."
        )
    return {
        "sum": f"{first:,.2f} {MINUS} {second:,.2f} = {signed_money(balance)}",
        "because": f"{normal.capitalize()}s minus {other}s, because {name}'s normal side is "
        f"{normal}.",
        "below": below,
    }


def entry_rows(
    entries: list[dict[str, Any]], others: dict[uuid.UUID, list[dict[str, Any]]]
) -> dict[str, list[dict[str, Any]]]:
    """The page's entries split into the T-account's two columns, each with its other side."""
    columns: dict[str, list[dict[str, Any]]] = {"debit": [], "credit": []}
    for entry in entries:
        row = dict(entry)
        row["other"] = other_side(entry["entry_type"], others.get(entry["transaction_id"], []))
        columns[entry["entry_type"]].append(row)
    return columns
