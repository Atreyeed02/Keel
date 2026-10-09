"""
What the transaction list and a transaction's page show besides the rows
themselves: each transaction's accounts by side and its amount in each
currency, the filters in force as chips, the pager's line, and on a
transaction's page, what each entry does to its account and the notes that
explain the transaction's shape.

The amount is a transaction's debit total in each currency, which is also
its credit total. Adding every entry instead counts each amount twice, once
per side, and adds currencies together.

Nothing here decides anything: a posted transaction balances because the
domain refuses one that doesn't (app/domain/ledger.py), and `balanced` only
reports it, so the page would say so if one ever didn't.
"""

import uuid
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from app.domain.reads import normal_side

# A card names at most this many accounts on a side, then "and N more".
SHOWN_ACCOUNTS = 3

_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


# --- amounts --------------------------------------------------------------------------


def entry_totals(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Debits, credits and their difference in each currency, in currency order."""
    totals: dict[str, list[Decimal]] = {}
    for entry in entries:
        sides = totals.setdefault(entry["currency"], [Decimal(0), Decimal(0)])
        sides[0 if entry["entry_type"] == "debit" else 1] += entry["amount"]
    return [
        {"currency": currency, "debits": debits, "credits": credits, "difference": debits - credits}
        for currency, (debits, credits) in sorted(totals.items())
    ]


def summaries(entries: Iterable[dict[str, Any]]) -> dict[uuid.UUID, dict[str, Any]]:
    """
    Per transaction, from its entries in their order: the accounts on each
    side (each named once, the first few shown), the totals per currency, and
    whether every currency balances. A transaction without entries has none
    of these, and the page says so instead.
    """
    by_transaction: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for entry in entries:
        by_transaction.setdefault(entry["transaction_id"], []).append(entry)
    out = {}
    for transaction_id, rows in by_transaction.items():
        sides = {}
        for side in ("debit", "credit"):
            names = list(dict.fromkeys(row["name"] for row in rows if row["entry_type"] == side))
            sides[side] = {
                "shown": names[:SHOWN_ACCOUNTS],
                "more": max(len(names) - SHOWN_ACCOUNTS, 0),
            }
        totals = entry_totals(rows)
        out[transaction_id] = {
            **sides,
            "totals": totals,
            "balanced": all(total["difference"] == 0 for total in totals),
        }
    return out


# --- the lists' filters and pager -----------------------------------------------------


def filter_chips(q: str | None, date_from: date | None, date_to: date | None) -> list[dict]:
    """
    One chip per filter in force. Each links to the list without that filter
    and with the others, and goes back to the first page, since the page it
    was on may not exist once the set changes.
    """
    active = [
        ("q", q, f'Description contains "{q}"'),
        ("date_from", date_from, f"From {date_from.isoformat()}" if date_from else ""),
        ("date_to", date_to, f"To {date_to.isoformat()}" if date_to else ""),
    ]
    active = [(key, value, label) for key, value, label in active if value]
    chips = []
    for key, _, label in active:
        rest = urlencode({k: str(v) for k, v, _ in active if k != key})
        chips.append({"label": label, "href": "/transactions" + (f"?{rest}" if rest else "")})
    return chips


def pager(page: int, page_size: int, total: int, noun: str = "transactions") -> dict[str, Any]:
    """
    Where this page sits in a list: "Page 1 of 2 · 1–25 of 30", or past the
    end. `noun` names what the list holds, for the past-the-end sentence.
    """
    pages = max(-(-total // page_size), 1)
    first = (page - 1) * page_size + 1
    return {
        "pages": pages,
        "past_the_end": total > 0 and page > pages,
        "line": f"Page {page} of {pages} · {first}–{min(page * page_size, total)} of {total}",
        "past_the_end_text": f"There's no page {page}: these {noun} fit on "
        f"{pages} page{'' if pages == 1 else 's'}.",
    }


# --- a transaction's page -------------------------------------------------------------


def effect(account_type: str, entry_type: str) -> str:
    """What an entry does to its account, such as "Expense: a debit increases it"."""
    change = "increases" if normal_side(account_type) == entry_type else "decreases"
    return f"{account_type.capitalize()}: a {entry_type} {change} it."


def _count(n: int, word: str) -> str:
    number = _WORDS[n] if n < len(_WORDS) else str(n)
    return f"{number} {word}{'' if n == 1 else 's'}"


def entries_line(debits: int, credits: int) -> str:
    """The page's "Entries" figure: "3: 2 debits, 1 credit"."""
    return (
        f"{debits + credits}: {debits} debit{'' if debits == 1 else 's'}, "
        f"{credits} credit{'' if credits == 1 else 's'}"
    )


def shape_note(debits: int, credits: int) -> str | None:
    """
    When a side has more than one entry, the point a beginner misses: the
    counts needn't match, the totals must. None for one entry a side.
    """
    if max(debits, credits) < 2:
        return None
    line = f"{_count(debits, 'debit')} and {_count(credits, 'credit')}"
    return (
        f"{line[0].upper()}{line[1:]}: the number of entries on each side needn't match, "
        "but their totals must."
    )
