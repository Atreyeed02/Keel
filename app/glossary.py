"""
The terms Keel's pages define where they are used: the `term` macro in
templates/_ui.html turns one into a button that opens a short card, and the
card links to the section of /learn that explains it in full.

The wording lives here once, so a card and the screen reader's reading of
it cannot drift apart, and tests/test_learn.py checks that every term has
its section on /learn.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Term:
    title: str  # the card's heading
    definition: str  # one or two sentences; also the term button's description
    learn: str  # the id of the /learn section that explains it
    more: str  # the card's link reads "More about <more>"


GLOSSARY: dict[str, Term] = {
    "debit": Term(
        "Debit",
        "An entry on the left side of an account. Debits increase assets and expenses, "
        "and decrease liabilities, equity and revenue.",
        "debit",
        "debits",
    ),
    "credit": Term(
        "Credit",
        "An entry on the right side of an account. Credits increase liabilities, equity "
        "and revenue, and decrease assets and expenses.",
        "credit",
        "credits",
    ),
    "normal-side": Term(
        "Normal side",
        "The side that increases an account: debit for assets and expenses, credit for "
        "liabilities, equity and revenue. Keel shows each balance on its normal side.",
        "normal-side",
        "normal sides",
    ),
    "balanced": Term(
        "Balanced",
        "Debits equal credits. Keel checks every transaction per currency and refuses "
        "one that doesn't balance.",
        "balanced",
        "balancing",
    ),
    "trial-balance": Term(
        "Trial balance",
        "Traditionally, every account's balance listed in debit and credit columns. Keel "
        "adds up every debit and credit entry per currency instead. Either way, when every "
        "transaction balances, the two totals are equal.",
        "trial-balance",
        "the trial balance",
    ),
    "asset": Term(
        "Asset",
        "What the business owns or is owed, such as cash. Debits increase it.",
        "asset",
        "assets",
    ),
    "liability": Term(
        "Liability",
        "What the business owes others, such as a loan. Credits increase it.",
        "liability",
        "liabilities",
    ),
    "equity": Term(
        "Equity",
        "The owners' stake: what they put in, plus profits kept in the business. "
        "Credits increase it.",
        "equity",
        "equity",
    ),
    "revenue": Term(
        "Revenue",
        "What the business earns from its work. Credits increase it.",
        "revenue",
        "revenue",
    ),
    "expense": Term(
        "Expense",
        "A cost of running the business, such as rent. Debits increase it.",
        "expense",
        "expenses",
    ),
    "event": Term(
        "Event",
        "A record of one change to the ledger, such as an account opened or a "
        "transaction posted. Keel only ever adds events.",
        "events",
        "events",
    ),
}
