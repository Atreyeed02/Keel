"""
The posting form's own behaviour (app/posting_messages.py, the form routes in
app/main.py): every refusal worded for a person, the live balance panel as
the server draws it, the no-JavaScript "Add line" and "Remove", and the
notice on a resubmission.

The first half needs no database: it tests the wording directly. The second
half is Postgres-backed and skips without TEST_DATABASE_URL.
"""

import html
import json
import logging
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import create_async_engine

from app import posting_messages as messages
from app.config import settings
from app.db.schema import accounts, metadata
from app.domain.ledger import EntryInput
from app.main import app, write_limiter
from app.observability import JsonFormatter, log
from tests.support import reset_schema

CASH = uuid.uuid4()


def _row(amount="10.00", side="debit", account=None, currency="USD"):
    return {
        "account_id": str(account or CASH),
        "entry_type": side,
        "amount": amount,
        "currency": currency,
    }


def _entry(amount, side, currency="USD"):
    return EntryInput.model_validate(_row(amount, side, currency=currency))


# --- one line, worded -----------------------------------------------------------------


@pytest.mark.parametrize(
    "row, title, detail",
    [
        (_row("abc"), 'Line 3: "abc" isn\'t an amount.', "Type it like 1250.00"),
        (_row("NaN"), 'Line 3: "NaN" isn\'t an amount.', "plain digits"),
        (_row("0"), "Line 3: the amount must be more than zero.", "zero moves nothing"),
        (
            _row("-5"),
            "Line 3: the amount must be more than zero.",
            "Enter 5.00 and switch the line to Credit.",
        ),
        (
            _row("-5", side="credit"),
            "Line 3: the amount must be more than zero.",
            "switch the line to Debit.",
        ),
        (_row("1.005"), "Line 3: 1.005 has more than two decimal places.", "to the cent"),
        (_row("1" * 17), f"Line 3: {'1' * 17} is too large.", "16 digits"),
        (_row(side="sideways"), "Line 3: choose Debit or Credit.", "one side of its account"),
        (
            {**_row(), "account_id": ""},
            "Line 3: choose an account.",
            "exactly one account",
        ),
        (_row(""), "Some lines arrived incomplete.", "an account, a side, an amount"),
        (_row(currency=""), "Some lines arrived incomplete.", "an account, a side, an amount"),
    ],
)
def test_each_bad_line_is_worded_by_its_rule(row, title, detail):
    entry, problems = messages.check_line(3, row)
    assert entry is None
    (problem,) = problems
    assert problem.title == title
    assert detail in problem.detail
    assert problem.line == 3


def test_a_good_line_is_the_domains_entry():
    entry, problems = messages.check_line(1, _row("100.000"))
    assert problems == []
    assert entry.amount == 100


def test_unbalanced_currencies_each_get_a_message_and_opposite_ones_hear_why():
    entries = [_entry("100.00", "debit", "USD"), _entry("100.00", "credit", "EUR")]
    usd, eur = messages.unbalanced(entries)
    assert usd.title == "USD is out of balance by 100.00: debits 100.00, credits 0.00."
    assert "Add 100.00 USD of credits" in usd.detail
    assert eur.title == "EUR is out of balance by 100.00: debits 0.00, credits 100.00."
    assert "Add 100.00 EUR of debits" in eur.detail
    # said once, at the end
    assert "can't convert" not in usd.detail
    assert "can't convert between currencies inside one transaction" in eur.detail
    assert usd.learn == eur.learn == ("balanced", "When a transaction balances")


def test_the_wording_that_depends_on_the_demo():
    assert messages.ledger_full(2000, demo=True).title.startswith("The demo's ledger is full")
    assert "nightly reset" in messages.ledger_full(2000, demo=True).detail
    assert messages.ledger_full(1, demo=False).title == (
        "The ledger is full: it holds its maximum of 1 transaction."
    )
    entry = _entry("10.00", "debit")
    (gone,) = messages.account_problems([entry], {}, demo=True)
    assert gone.title == "Line 1: that account doesn't exist any more."
    assert "resets every night" in gone.detail
    (gone,) = messages.account_problems([entry], {}, demo=False)
    assert gone.title == "Line 1: that account doesn't exist."


def test_the_duplicate_and_rate_limit_wording():
    changed = messages.changed_after_posting(CASH)
    assert changed.link == (f"/transaction-detail/{CASH}", "See what was posted")
    assert "If you post again, it will be recorded as a separate, second transaction." in (
        changed.more
    )
    limited = messages.rate_limited(30, 60.0, 12)
    assert "at most 30 every 60 seconds" in limited.detail
    assert "Wait 12 seconds" in limited.detail
    assert "your browser's Back button usually keeps what you typed" in limited.detail


# --- the balance panel, as the server draws it ---------------------------------------


def test_the_panel_before_any_amount():
    panel = messages.balance_panel([_row(""), _row("", side="credit")])
    assert panel["state"] == "empty"
    assert panel["title"] == "Enter amounts to see whether the transaction balances."
    assert panel["currencies"] == []


def test_the_panel_balanced_in_two_currencies():
    panel = messages.balance_panel(
        [
            _row("650.00", "debit", currency="usd"),
            _row("650.00", "credit"),
            _row("50.00", "debit", currency="EUR"),
            _row("50.00", "credit", currency="EUR"),
        ]
    )
    assert panel["state"] == "balanced"
    assert panel["title"] == "Balanced ✓"
    assert panel["detail"] == "Debits equal credits in USD and EUR. Ready to post."
    assert [row["chip"] for row in panel["currencies"]] == ["Balanced ✓", "Balanced ✓"]


def test_the_panel_out_of_balance_says_by_how_much_and_what_to_do():
    panel = messages.balance_panel([_row("650.00", "debit"), _row("500.00", "credit")])
    assert panel["state"] == "off"
    assert panel["title"] == "Out of balance by 150.00 USD."
    assert panel["detail"] == (
        "Debits are 150.00 more than credits: add 150.00 of credits, or lower a debit."
    )
    (usd,) = panel["currencies"]
    assert (usd["debits"], usd["credits"], usd["difference"]) == ("650.00", "500.00", "150.00")
    assert usd["chip"] == "Out of balance by 150.00"


def test_the_panel_with_several_currencies_off():
    same_way = messages.balance_panel(
        [_row("10.00", "debit"), _row("5.00", "debit", currency="EUR")]
    )
    assert same_way["title"] == "Out of balance in 2 currencies."
    assert same_way["detail"] == "Each currency must balance by itself."
    opposite = messages.balance_panel(
        [_row("10.00", "debit"), _row("10.00", "credit", currency="EUR")]
    )
    assert opposite["detail"].startswith("Keel can't convert between currencies")


def test_the_panel_skips_amounts_it_cannot_count():
    panel = messages.balance_panel(
        [_row("abc"), _row("-5"), _row("10.00"), _row("10.00", "credit")]
    )
    assert panel["state"] == "balanced"


# --- the form (Postgres-backed) -------------------------------------------------------


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def database(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL page integration tests")
    test_engine = create_async_engine(TEST_DATABASE_URL)
    monkeypatch.setattr("app.main.engine", test_engine)
    async with test_engine.begin() as conn:
        await reset_schema(conn)
        cash_id, revenue_id = uuid.uuid4(), uuid.uuid4()
        await conn.execute(
            insert(accounts),
            [
                {"id": cash_id, "name": "Cash", "account_type": "asset", "currency": "USD"},
                {"id": revenue_id, "name": "Sales", "account_type": "revenue", "currency": "USD"},
            ],
        )
    yield cash_id, revenue_id
    async with test_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
    await test_engine.dispose()


def _client():
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    )


def _form(cash_id, revenue_id, amounts=("10.00", "10.00"), key="form-key"):
    return {
        "description": "A sale",
        "submission_key": key,
        "account_id": [str(cash_id), str(revenue_id)],
        "entry_type": ["debit", "credit"],
        "amount": list(amounts),
        "currency": ["USD", "USD"],
    }


async def test_lines_that_do_not_line_up_are_a_422_not_a_500(database):
    cash_id, revenue_id = database
    form = _form(cash_id, revenue_id)
    form["currency"] = ["USD"]  # one currency for two lines
    async with _client() as client:
        response = await client.post("/post-transaction", data=form)
    assert response.status_code == 422
    assert "Some lines arrived incomplete." in response.text


async def test_a_post_with_no_lines_needs_two_entries(database):
    async with _client() as client:
        empty = await client.post("/post-transaction", data={"description": "Nothing"})
        one = await client.post(
            "/post-transaction",
            data={
                "account_id": [str(database[0])],
                "entry_type": ["debit"],
                "amount": ["5.00"],
                "currency": ["USD"],
            },
        )
    for response in (empty, one):
        assert response.status_code == 422
        assert "A transaction needs at least two entries." in response.text
        assert 'href="/learn#balanced"' in response.text


async def test_every_bad_line_is_reported_at_once_and_marked(database):
    cash_id, revenue_id = database
    async with _client() as client:
        response = await client.post(
            "/post-transaction", data=_form(cash_id, revenue_id, amounts=("-5", "1.005"))
        )
    page = html.unescape(response.text)
    assert response.status_code == 422
    assert "Fix these 2 things and post again:" in page
    assert "Line 1: the amount must be more than zero." in page
    assert "Line 2: 1.005 has more than two decimal places." in page
    # each linked from the summary, and each amount field marked invalid
    assert 'href="#line-1"' in response.text and 'href="#line-2"' in response.text
    assert response.text.count('aria-invalid="true"') == 2
    # what was typed comes back
    assert 'value="-5"' in response.text and 'value="1.005"' in response.text


async def test_the_demo_says_why_an_account_is_gone(database, monkeypatch):
    monkeypatch.setattr(settings, "environment", "demo")
    cash_id, _ = database
    async with _client() as client:
        response = await client.post("/post-transaction", data=_form(cash_id, uuid.uuid4()))
    assert response.status_code == 422
    assert "Line 2: that account doesn't exist any more." in html.unescape(response.text)


async def test_a_resubmission_lands_on_the_transaction_saying_so(database):
    cash_id, revenue_id = database
    async with _client() as client:
        first = await client.post("/post-transaction", data=_form(cash_id, revenue_id))
        again = await client.post("/post-transaction", data=_form(cash_id, revenue_id))
        fresh = await client.get(first.headers["location"])
        notice = await client.get(again.headers["location"])
    assert again.headers["location"] == first.headers["location"] + "?already=1"
    assert "Already posted." not in fresh.text
    assert "Already posted." in notice.text
    assert "instead of posting it twice" in notice.text


async def test_without_javascript_add_and_remove_redraw_the_form(database):
    cash_id, revenue_id = database
    typed = {**_form(cash_id, revenue_id, amounts=("650.00", "500.00")), "add_line": "1"}
    async with _client() as client:
        added = await client.get("/post-transaction", params=typed)
        removed = await client.get(
            "/post-transaction",
            # line 1, 5.00 too many, goes; the 2.00 each side that's left balances
            params={
                **_form(cash_id, revenue_id),
                "account_id": [str(cash_id), str(revenue_id), str(cash_id)],
                "entry_type": ["debit", "credit", "debit"],
                "amount": ["5.00", "2.00", "2.00"],
                "currency": ["USD", "USD", "USD"],
                "remove_line": "1",
            },
        )
        floor = await client.get(
            "/post-transaction", params={**_form(cash_id, revenue_id), "remove_line": "1"}
        )
    assert added.status_code == 200
    # three lines, and the new-line template
    assert added.text.count('<fieldset class="line') == 3 + 1
    assert 'value="650.00"' in added.text and 'value="form-key"' in added.text
    assert "A sale" in added.text
    # the panel is drawn by the server from what was typed
    assert "Out of balance by 150.00 USD." in added.text
    assert removed.text.count('<fieldset class="line') == 2 + 1
    assert 'value="2.00"' in removed.text and "Balanced ✓" in removed.text
    # never fewer than two lines
    assert floor.text.count('<fieldset class="line') == 2 + 1


async def test_redrawing_the_form_is_not_a_write(database, monkeypatch):
    """GET carries the typed values, so the write limit never counts it."""
    monkeypatch.setattr(write_limiter, "limit", 1)
    async with _client() as client:
        statuses = [
            (await client.get("/post-transaction", params={"add_line": "1"})).status_code
            for _ in range(5)
        ]
    assert statuses == [200] * 5


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.setFormatter(JsonFormatter())
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


async def test_typed_values_in_the_query_string_are_never_logged(database):
    handler = _Capture()
    log.addHandler(handler)
    previous = log.level
    log.setLevel(logging.INFO)
    try:
        async with _client() as client:
            await client.get(
                "/post-transaction",
                params={"description": "Secret memo 7781", "add_line": "1"},
            )
    finally:
        log.removeHandler(handler)
        log.setLevel(previous)
    (line,) = (json.loads(entry) for entry in handler.lines)
    assert line["event"] == "request.completed"
    assert line["path"] == "/post-transaction"
    assert "7781" not in "".join(handler.lines)
