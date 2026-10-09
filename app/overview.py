"""
What the overview shows above its tables: the accounting equation in each
currency, and on the demo, the "try it" steps that open the posting form
already filled in, with the note the form shows for each step.

The figures come from the account balances (app/domain/reads.py). Nothing
here decides anything: the equation holds because every posted transaction
balances in each currency (app/domain/ledger.py), and `holds` only reports
it, so the page would say so if it ever didn't.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from app.posting_messages import money

# --- the accounting equation ----------------------------------------------------------


def equations(balances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Assets = Liabilities + Equity + (Revenue − Expenses), once per currency
    that has entries, in currency order (as the trial balance lists them).
    Each total adds up its accounts' balances on their normal side, so a
    loss shows as a negative Revenue − Expenses.
    """
    totals: dict[str, dict[str, Decimal]] = {}
    for account in balances:
        if not account["debits"] and not account["credits"]:
            continue
        by_type = totals.setdefault(account["currency"], {})
        by_type[account["account_type"]] = (
            by_type.get(account["account_type"], Decimal(0)) + account["balance"]
        )
    rows = []
    for currency in sorted(totals):
        t = totals[currency]
        assets, liabilities, equity, revenue, expenses = (
            t.get(kind, Decimal(0))
            for kind in ("asset", "liability", "equity", "revenue", "expense")
        )
        earned = revenue - expenses
        rows.append(
            {
                "currency": currency,
                "assets": money(assets),
                "liabilities": money(liabilities),
                "equity": money(equity),
                "revenue": money(revenue),
                "expenses": money(expenses),
                "earned": money(earned),
                "holds": assets == liabilities + equity + earned,
            }
        )
    return rows


# --- try it ---------------------------------------------------------------------------

# The demo's own accounts the steps use, by name and currency (scripts/seed_demo_data.py).
SUBSCRIPTIONS = ("Software subscriptions", "USD")
CASH = ("Cash", "USD")
EUR_ACCOUNT = ("EUR operating account", "EUR")
CONSULTING = ("Consulting revenue", "USD")


@dataclass(frozen=True)
class Step:
    title: str
    text: str
    description: str  # the form's description, filled in
    lines: tuple[tuple[tuple[str, str], str, str], ...]  # (account, side, amount)
    note: str  # what the posting form says above the lines
    learn: tuple[str, str] | None = None  # (/learn anchor, what "Why" is about)


STEPS = (
    Step(
        "Post a balanced transaction.",
        "A 12.00 software subscription paid in cash: a debit to Software subscriptions, "
        "a credit to Cash. Post it, then come back here: Assets and Revenue − Expenses "
        "both fall by 12.00, and the equation still holds.",
        "Try it: one month of a note-taking app",
        ((SUBSCRIPTIONS, "debit", "12.00"), (CASH, "credit", "12.00")),
        "this one balances. Press Post transaction, then go back to the Overview.",
    ),
    Step(
        "Try one that doesn't balance.",
        "The same subscription, with only 10.00 credited. The form shows it's out of "
        "balance by 2.00 USD, and Keel won't post it.",
        "Try it: one month of a note-taking app",
        ((SUBSCRIPTIONS, "debit", "12.00"), (CASH, "credit", "10.00")),
        "this one is 2.00 short. Try pressing Post transaction: Keel won't post it, and "
        "the banner shows why.",
        ("balanced", "when a transaction balances"),
    ),
    Step(
        "Try mixing currencies.",
        "50.00 debited to the EUR operating account and 50.00 credited to Consulting "
        "revenue, in USD. The amounts match, but Keel can't convert between currencies "
        "inside one transaction, so each currency is out of balance.",
        "Try it: a payment in two currencies",
        ((EUR_ACCOUNT, "debit", "50.00"), (CONSULTING, "credit", "50.00")),
        "each currency is out of balance. Try pressing Post transaction: Keel won't post "
        "it, and the banner shows why.",
        ("example-two-currencies", "two currencies"),
    ),
)


def try_steps(balances: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """
    The steps, each with its link to the posting form filled in. The accounts
    are found by name and currency as the page is drawn, since the nightly
    reset gives them new ids; if a visitor opened another with the same name,
    the oldest is the demo's. None if any of them is missing, and the page
    leaves the steps out.
    """
    found: dict[tuple[str, str], Any] = {}
    for account in sorted(balances, key=lambda a: a["created_at"]):
        found.setdefault((account["name"], account["currency"]), account["id"])
    needed = {account for step in STEPS for account, _, _ in step.lines}
    if not needed <= found.keys():
        return None
    steps = []
    for number, step in enumerate(STEPS, start=1):
        query = [("try", str(number)), ("description", step.description)]
        for (name, currency), side, amount in step.lines:
            query += [
                ("account_id", str(found[(name, currency)])),
                ("entry_type", side),
                ("amount", amount),
                ("currency", currency),
            ]
        steps.append(
            {
                "number": number,
                "title": step.title,
                "text": step.text,
                "href": "/post-transaction?" + urlencode(query),
                "learn": step.learn,
            }
        )
    return steps


def try_note(step: str | None) -> dict[str, Any] | None:
    """The posting form's note for `?try=N`; None for anything but 1, 2 or 3."""
    if step not in {str(n) for n in range(1, len(STEPS) + 1)}:
        return None
    number = int(step)
    return {
        "number": number,
        "text": f"Try it, {number} of {len(STEPS)}: {STEPS[number - 1].note}",
    }
