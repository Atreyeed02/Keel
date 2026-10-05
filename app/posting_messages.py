"""
What the posting form says when it refuses a transaction, and what its live
balance panel says while one is being typed.

The rules themselves live in the domain (app/domain/ledger.py: EntryInput,
assert_balanced, assert_accounts_valid; app/domain/capacity.py;
app/domain/idempotency.py), and the JSON API reports them in its own terms
(app/api/). This module only words them for a person filling in the form:
what went wrong, why the rule exists, and how to fix it, linking to the
section of /learn that explains it. Nothing here decides whether a
transaction is valid.

The panel's sentences are in PANEL_TEXT once: the server fills them in for
the page it renders, and the page hands the same templates to
static/js/post-transaction.js, which fills them in as you type.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from app.domain.ledger import EntryInput

BALANCED = ("balanced", "When a transaction balances")
SIDES = ("debit", "Debits and credits")
EVENTS = ("events", "How Keel records it")


@dataclass(frozen=True)
class Problem:
    """One thing to fix, as the form shows it: in the summary and by its line."""

    title: str  # what went wrong, one sentence
    detail: str = ""  # why the rule exists, and how to fix it
    line: int | None = None  # the 1-based line it belongs to
    field: str | None = None  # the field to mark: account_id, entry_type, amount, currency
    link: tuple[str, str] | None = None  # (href, text), after `detail`
    more: str = ""  # text after the link
    learn: tuple[str, str] | None = None  # (/learn anchor, text), last


def money(value: Decimal) -> str:
    return f"{value:,.2f}"


# --- one line -------------------------------------------------------------------------


def incomplete(line: int | None = None, field: str | None = None) -> Problem:
    return Problem(
        "Some lines arrived incomplete.",
        "Every line needs an account, a side, an amount and a currency. "
        "Fill in each line and post again.",
        line,
        field,
    )


def check_line(line: int, row: dict[str, str]) -> tuple[EntryInput | None, list[Problem]]:
    """Validate one line with the domain's own model; word whatever it refuses."""
    if not row["amount"].strip():
        return None, [incomplete(line, "amount")]
    if not row["currency"].strip():
        return None, [incomplete(line, "currency")]
    try:
        return EntryInput.model_validate(row), []
    except ValidationError as exc:
        return None, [_line_problem(line, row, err) for err in exc.errors()]


def _line_problem(line: int, row: dict[str, str], err: Any) -> Problem:
    field = str(err["loc"][0]) if err["loc"] else ""
    if field == "account_id":
        return Problem(
            f"Line {line}: choose an account.",
            "Every entry is recorded in exactly one account.",
            line,
            "account_id",
        )
    if field == "entry_type":
        return Problem(
            f"Line {line}: choose Debit or Credit.",
            "Every entry goes on one side of its account.",
            line,
            "entry_type",
            learn=SIDES,
        )
    typed = row["amount"].strip()
    kind = err["type"]
    if kind == "decimal_max_places":
        return Problem(
            f"Line {line}: {typed} has more than two decimal places.",
            "Keel keeps amounts to the cent and won't round what you typed without "
            "telling you. Use at most two decimal places.",
            line,
            "amount",
        )
    if kind in ("decimal_whole_digits", "decimal_max_digits"):
        return Problem(
            f"Line {line}: {typed} is too large.",
            "An amount can have at most 16 digits before the decimal point. "
            "Check for an extra digit.",
            line,
            "amount",
        )
    if kind == "value_error":  # EntryInput: "amount must be positive"
        value = Decimal(typed)
        if value == 0:
            return Problem(
                f"Line {line}: the amount must be more than zero.",
                "An entry records value moving; zero moves nothing. "
                "Enter the amount, or remove the line.",
                line,
                "amount",
            )
        other = "Credit" if row["entry_type"] == "debit" else "Debit"
        return Problem(
            f"Line {line}: the amount must be more than zero.",
            "The side carries the direction, so a negative debit would really be a "
            f"credit. Enter {money(-value)} and switch the line to {other}.",
            line,
            "amount",
            learn=SIDES,
        )
    # decimal_parsing, finite_number, and anything else that isn't a number
    return Problem(
        f'Line {line}: "{typed}" isn\'t an amount.',
        "Keel stores exact decimals, so amounts are plain digits. Type it like 1250.00, "
        "without a currency sign or thousands separators.",
        line,
        "amount",
    )


# --- the whole transaction ------------------------------------------------------------


def fewer_than_two() -> Problem:
    return Problem(
        "A transaction needs at least two entries.",
        "Value always moves from somewhere to somewhere, so one entry can't record it. "
        "Add a line for the other side.",
        learn=BALANCED,
    )


def description_too_long(length: int) -> Problem:
    return Problem(
        f"The description is {length} characters; the limit is 512.",
        "It's a short label for the transaction. Shorten it.",
        field="description",
    )


def currency_totals(entries: list[EntryInput]) -> dict[str, tuple[Decimal, Decimal]]:
    """(debits, credits) per currency, in the order each currency first appears."""
    totals: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for entry in entries:
        totals[entry.currency][0 if entry.entry_type == "debit" else 1] += entry.amount
    return {currency: (debits, credits) for currency, (debits, credits) in totals.items()}


def unbalanced(entries: list[EntryInput]) -> list[Problem]:
    """One problem per currency whose debits and credits differ."""
    off = {c: (d, cr) for c, (d, cr) in currency_totals(entries).items() if d != cr}
    problems = []
    for currency, (debits, credits) in off.items():
        diff = abs(debits - credits)
        side = "credits" if debits > credits else "debits"
        problems.append(
            Problem(
                f"{currency} is out of balance by {money(diff)}: debits {money(debits)}, "
                f"credits {money(credits)}.",
                "Every transaction must put the same amount on each side, in each currency; "
                f"that's what keeps the books balanced. Add {money(diff)} {currency} of "
                f"{side}, or correct an amount.",
                learn=BALANCED,
            )
        )
    if _opposite_directions(off) and problems:
        last = problems[-1]
        problems[-1] = Problem(
            last.title,
            last.detail + " " + PANEL_TEXT["no_conversion"],
            learn=last.learn,
        )
    return problems


def _opposite_directions(off: dict[str, tuple[Decimal, Decimal]]) -> bool:
    directions = {debits > credits for debits, credits in off.values()}
    return len(directions) == 2


def account_problems(
    entries: list[EntryInput], accounts_by_id: dict[uuid.UUID, Any], demo: bool
) -> list[Problem]:
    """Per line: an account that doesn't exist, or one in another currency."""
    problems = []
    for line, entry in enumerate(entries, start=1):
        account = accounts_by_id.get(entry.account_id)
        if account is None:
            if demo:
                problems.append(
                    Problem(
                        f"Line {line}: that account doesn't exist any more.",
                        "The demo's data resets every night, and this form was loaded before "
                        "the last reset. Choose the account again; the list is up to date now.",
                        line,
                        "account_id",
                    )
                )
            else:
                problems.append(
                    Problem(
                        f"Line {line}: that account doesn't exist.",
                        "The page may have been out of date. Choose the account again; the "
                        "list is up to date now.",
                        line,
                        "account_id",
                    )
                )
        elif account["currency"] != entry.currency:
            held, typed = account["currency"], entry.currency
            problems.append(
                Problem(
                    f"Line {line}: {account['name']} is a {held} account, but this line "
                    f"says {typed}.",
                    "An account holds one currency, fixed when it's created, so its balance "
                    f"never mixes currencies. Change this line's currency to {held}, or "
                    f"choose a {typed} account.",
                    line,
                    "currency",
                    learn=BALANCED,
                )
            )
    return problems


def ledger_full(limit: int, demo: bool) -> Problem:
    noun = "transaction" if limit == 1 else "transactions"
    if demo:
        return Problem(
            f"The demo's ledger is full: it holds its maximum of {limit} {noun}.",
            "The public demo caps what it stores so its small database can't fill up. "
            "Nothing was posted. The ledger empties at the nightly reset, so try again "
            "after that.",
        )
    return Problem(
        f"The ledger is full: it holds its maximum of {limit} {noun}.",
        "This Keel is set to stop there (MAX_TRANSACTIONS). Nothing was posted; ask whoever "
        "runs it to raise the limit.",
    )


def changed_after_posting(transaction_id: uuid.UUID | None) -> Problem:
    return Problem(
        "This form already posted a transaction, and you've changed it since.",
        "Each form carries a one-time key, so pressing Post twice or resending the page "
        "can't post the same transaction twice.",
        link=(f"/transaction-detail/{transaction_id}", "See what was posted")
        if transaction_id
        else None,
        more="If you post again, it will be recorded as a separate, second transaction. "
        "To correct the first one, post a transaction that reverses it, then the right one.",
        learn=EVENTS,
    )


ALREADY_POSTED = Problem(
    "Already posted.",
    "This form had already been submitted, so Keel showed you that transaction instead of "
    "posting it twice.",
)


# --- refusals before the form is read (the write limit, the body limit) ---------------


def rate_limited(limit: int, window: float, retry_after: int) -> Problem:
    return Problem(
        "Too many changes from your connection.",
        f"Keel accepts at most {limit} every {window:g} seconds from one address, so one "
        f"visitor can't swamp it. Nothing was saved. Wait {retry_after} seconds, then go back "
        "and send it again: your browser's Back button usually keeps what you typed.",
    )


def too_large(limit: int) -> Problem:
    return Problem(
        f"That's more than Keel accepts in one request ({limit} bytes).",
        "Nothing was saved. Split it into smaller transactions.",
    )


# --- the live balance panel -----------------------------------------------------------

# Placeholders in braces; the page passes these to the script as data attributes.
PANEL_TEXT = {
    "empty": "Enter amounts to see whether the transaction balances.",
    "balanced_title": "Balanced ✓",
    "balanced_detail": "Debits equal credits in {currencies}. Ready to post.",
    "off_title": "Out of balance by {diff} {currency}.",
    "off_debits": "Debits are {diff} more than credits: add {diff} of credits, or lower a debit.",
    "off_credits": "Credits are {diff} more than debits: add {diff} of debits, or lower a credit.",
    "off_many_title": "Out of balance in {count} currencies.",
    "off_many_detail": "Each currency must balance by itself.",
    "no_conversion": "Keel can't convert between currencies inside one transaction: each "
    "currency has to balance by itself.",
    "chip_balanced": "Balanced ✓",
    "chip_off": "Out of balance by {diff}",
    "post_blocked": "Debits must equal credits, in each currency, before you can post.",
    "wrong_currency": "{account} is a {currency} account.",
    "amount_not_number": "That isn't an amount: type digits, like 1250.00.",
    "amount_not_positive": "The amount must be more than zero.",
    "amount_places": "Use at most two decimal places.",
}


def _join(currencies: list[str]) -> str:
    if len(currencies) <= 1:
        return "".join(currencies)
    return ", ".join(currencies[:-1]) + " and " + currencies[-1]


def balance_panel(rows: list[dict[str, str]]) -> dict[str, Any]:
    """
    The panel for these lines, as the page first shows it: totals per currency
    from every line with a readable positive amount, and the banner's state.
    The script recomputes the same thing as the lines change.
    """
    entries = []
    for row in rows:
        try:
            amount = Decimal(row.get("amount", "").strip())
        except InvalidOperation:
            continue
        currency = row.get("currency", "").strip().upper()
        if not amount.is_finite() or amount <= 0 or not currency:
            continue
        entries.append((currency, row.get("entry_type"), amount))

    totals: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for currency, side, amount in entries:
        totals[currency][0 if side == "debit" else 1] += amount
    currencies = [
        {
            "currency": currency,
            "debits": money(debits),
            "credits": money(credits),
            "difference": money(abs(debits - credits)),
            "balanced": debits == credits,
            "chip": PANEL_TEXT["chip_balanced"]
            if debits == credits
            else PANEL_TEXT["chip_off"].format(diff=money(abs(debits - credits))),
        }
        for currency, (debits, credits) in totals.items()
    ]
    off = [(c, d, cr) for c, (d, cr) in totals.items() if d != cr]
    if not currencies:
        state, title, detail = "empty", PANEL_TEXT["empty"], ""
    elif not off:
        state = "balanced"
        title = PANEL_TEXT["balanced_title"]
        detail = PANEL_TEXT["balanced_detail"].format(currencies=_join(list(totals)))
    elif len(off) == 1:
        currency, debits, credits = off[0]
        diff = money(abs(debits - credits))
        state = "off"
        title = PANEL_TEXT["off_title"].format(diff=diff, currency=currency)
        key = "off_debits" if debits > credits else "off_credits"
        detail = PANEL_TEXT[key].format(diff=diff)
    else:
        state = "off"
        title = PANEL_TEXT["off_many_title"].format(count=len(off))
        opposite = len({d > cr for _, d, cr in off}) == 2
        detail = PANEL_TEXT["no_conversion"] if opposite else PANEL_TEXT["off_many_detail"]
    return {"state": state, "title": title, "detail": detail, "currencies": currencies}
