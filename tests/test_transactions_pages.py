"""
The transaction list and a transaction's page (app/transactions_view.py):
each transaction's amount as its debit total per currency, its accounts by
side, the filters in force as chips, the pager's line, and on a
transaction's page, what each entry does, the totals per currency, the notes
and the link to its event.

The functions need no database; the pages are Postgres-backed and skip
without TEST_DATABASE_URL. URLs, the pagination tests and the JSON API are
covered where they always were (tests/test_ledger_pages.py, test_api.py).
"""

import html
import os
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import create_async_engine

from app import main as main_module
from app.config import settings
from app.db.schema import accounts, metadata, transactions
from app.main import app
from app.transactions_view import (
    effect,
    entries_line,
    filter_chips,
    pager,
    shape_note,
    summaries,
)
from scripts import seed_demo_data
from tests.support import reset_schema

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _entry(transaction, side, name, amount, currency="USD"):
    return {
        "transaction_id": transaction,
        "entry_type": side,
        "name": name,
        "amount": Decimal(amount),
        "currency": currency,
    }


# --- amounts and sides ----------------------------------------------------------------


def test_the_amount_is_the_debit_total_not_both_sides_added():
    t = uuid.uuid4()
    summary = summaries(
        [_entry(t, "debit", "Cash", "100.00"), _entry(t, "credit", "Fees", "100.00")]
    )[t]
    assert summary["totals"] == [
        {
            "currency": "USD",
            "debits": Decimal("100.00"),
            "credits": Decimal("100.00"),
            "difference": Decimal(0),
        }
    ]
    assert summary["balanced"] is True


def test_each_currency_is_totalled_by_itself_in_currency_order():
    t = uuid.uuid4()
    summary = summaries(
        [
            _entry(t, "debit", "Cash", "54.00", "USD"),
            _entry(t, "credit", "Fees", "54.00", "USD"),
            _entry(t, "debit", "Euros", "50.00", "EUR"),
            _entry(t, "credit", "Euro fees", "50.00", "EUR"),
        ]
    )[t]
    assert [(x["currency"], x["debits"]) for x in summary["totals"]] == [
        ("EUR", Decimal("50.00")),
        ("USD", Decimal("54.00")),
    ]


def test_a_side_names_each_account_once_in_entry_order_then_how_many_more():
    t = uuid.uuid4()
    names = ["Rent", "Software", "Rent", "Travel", "Meals", "Postage"]
    rows = [_entry(t, "debit", name, "1.00") for name in names]
    rows.append(_entry(t, "credit", "Cash", "6.00"))
    summary = summaries(rows)[t]
    assert summary["debit"] == {"shown": ["Rent", "Software", "Travel"], "more": 2}
    assert summary["credit"] == {"shown": ["Cash"], "more": 0}


def test_a_transaction_that_did_not_balance_would_say_so():
    t = uuid.uuid4()
    summary = summaries([_entry(t, "debit", "Cash", "10.00"), _entry(t, "credit", "Fees", "9.00")])[
        t
    ]
    assert summary["balanced"] is False
    assert summary["totals"][0]["difference"] == Decimal("1.00")


# --- filters and pager ----------------------------------------------------------------


def test_each_chip_drops_its_own_filter_and_keeps_the_others():
    chips = filter_chips("invoice", date(2026, 3, 1), date(2026, 3, 31))
    assert [chip["label"] for chip in chips] == [
        'Description contains "invoice"',
        "From 2026-03-01",
        "To 2026-03-31",
    ]
    assert [chip["href"] for chip in chips] == [
        "/transactions?date_from=2026-03-01&date_to=2026-03-31",
        "/transactions?q=invoice&date_to=2026-03-31",
        "/transactions?q=invoice&date_from=2026-03-01",
    ]
    assert filter_chips("rent", None, None)[0]["href"] == "/transactions"
    assert filter_chips(None, None, None) == []


def test_the_pager_line_and_a_page_past_the_end():
    assert pager(1, 25, 30)["line"] == "Page 1 of 2 · 1–25 of 30"
    assert pager(2, 25, 30)["line"] == "Page 2 of 2 · 26–30 of 30"
    assert pager(2, 25, 30)["past_the_end"] is False
    past = pager(9, 25, 30)
    assert past["past_the_end"] is True
    assert past["past_the_end_text"] == "There's no page 9: these transactions fit on 2 pages."
    assert pager(3, 25, 10)["past_the_end_text"].endswith("fit on 1 page.")
    # an empty list is not "past the end": it has its own message
    assert pager(1, 25, 0)["past_the_end"] is False


# --- a transaction's page -------------------------------------------------------------


@pytest.mark.parametrize(
    ("account_type", "debit", "credit"),
    [
        ("asset", "increases", "decreases"),
        ("expense", "increases", "decreases"),
        ("liability", "decreases", "increases"),
        ("equity", "decreases", "increases"),
        ("revenue", "decreases", "increases"),
    ],
)
def test_what_an_entry_does_follows_the_normal_side(account_type, debit, credit):
    kind = account_type.capitalize()
    assert effect(account_type, "debit") == f"{kind}: a debit {debit} it."
    assert effect(account_type, "credit") == f"{kind}: a credit {credit} it."


def test_the_entries_line_and_the_note_on_uneven_sides():
    assert entries_line(2, 1) == "3: 2 debits, 1 credit"
    assert entries_line(1, 1) == "2: 1 debit, 1 credit"
    assert shape_note(1, 1) is None
    assert shape_note(2, 1) == (
        "Two debits and one credit: the number of entries on each side needn't match, "
        "but their totals must."
    )
    assert shape_note(1, 12).startswith("One debit and 12 credits:")


# --- the pages ------------------------------------------------------------------------


def _client():
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        follow_redirects=False,
    )


def _text(page: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page))).strip()


@pytest.fixture
async def seeded(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL page integration tests")
    test_engine = create_async_engine(TEST_DATABASE_URL)
    monkeypatch.setattr("app.main.engine", test_engine)
    monkeypatch.setattr(settings, "environment", "demo")
    async with test_engine.begin() as conn:
        await reset_schema(conn)
        await seed_demo_data.seed(conn)
    yield test_engine
    async with test_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
    await test_engine.dispose()


async def _ids(engine) -> dict[str, str]:
    async with engine.connect() as conn:
        rows = await conn.execute(select(accounts.c.name, accounts.c.id))
        return {name: str(account_id) for name, account_id in rows}


async def _post(client, ids, description, lines):
    response = await client.post(
        "/post-transaction",
        data={
            "description": description,
            "submission_key": str(uuid.uuid4()),
            "account_id": [ids[name] for name, _, _, _ in lines],
            "entry_type": [side for _, side, _, _ in lines],
            "amount": [amount for _, _, amount, _ in lines],
            "currency": [currency for _, _, _, currency in lines],
        },
    )
    assert response.status_code == 302, response.text
    return response.headers["location"]


async def _detail_of(client, number) -> str:
    listing = (await client.get("/transactions")).text
    cards = dict(
        re.findall(r'No\. (\d+)</span>.*?href="(/transaction-detail/[0-9a-f-]+)"', listing, re.S)
    )
    return (await client.get(cards[str(number)])).text


MIXED = [
    ("EUR operating account", "debit", "50.00", "EUR"),
    ("European consulting revenue", "credit", "50.00", "EUR"),
    ("Cash", "debit", "54.00", "USD"),
    ("Consulting revenue", "credit", "54.00", "USD"),
]


async def test_the_list_shows_each_amount_once_per_currency(seeded):
    async with _client() as client:
        await _post(client, await _ids(seeded), "Mixed settlement", MIXED)
        page = (await client.get("/transactions")).text
    text = _text(page)
    # January's costs: 2,400.00 + 318.50 debited, 2,718.50 credited
    assert "January operating costs" in text
    assert "Debits = credits USD 2,718.50" in text
    assert "5,437.00" not in text  # both sides added, the old "Volume"
    # two currencies, each by itself, never 104.00 or 208.00
    assert "Debits = credits EUR 50.00 · USD 54.00" in text
    assert "104.00" not in text and "208.00" not in text
    assert page.count('<span class="badge badge-ok">') == 11  # one per card
    assert "Doesn't balance" not in text


async def test_a_card_names_its_accounts_by_side(seeded):
    async with _client() as client:
        await _post(
            client,
            await _ids(seeded),
            "Year-end tidy-up",
            [
                ("Office rent", "debit", "1.00", "USD"),
                ("Software subscriptions", "debit", "1.00", "USD"),
                ("Owner's capital", "debit", "1.00", "USD"),
                ("Equipment loan payable", "debit", "1.00", "USD"),
                ("Cash", "credit", "4.00", "USD"),
            ],
        )
        page = (await client.get("/transactions")).text
    cards = page.split('<li class="card tx-card">')[1:]
    newest, january = _text(cards[0]), _text(next(c for c in cards if "January operating" in c))
    assert "Dr Debit Office rent, Software subscriptions, Owner's capital and 1 more" in newest
    assert "Cr Credit Cash" in newest
    assert newest.startswith("No. 11 ·")
    assert "Dr Debit Office rent, Software subscriptions ⇄ , Cr Credit Cash" in january
    # still one link to each transaction, so the pagination tests' counts hold
    assert page.count("/transaction-detail/") == 11


async def test_the_overview_recent_table_shows_the_amount_too(seeded):
    async with _client() as client:
        await _post(client, await _ids(seeded), "Mixed settlement", MIXED)
        page = (await client.get("/")).text
    recent = page.split('id="recent-heading"')[1]
    assert '<th scope="col" class="num">Amount</th>' in recent
    assert "Volume" not in page
    text = _text(recent)
    assert "USD 2,718.50" in text
    assert "EUR 50.00 · USD 54.00" in text
    assert "Debits = credits" not in text


async def test_chips_link_to_the_list_without_each_filter(seeded):
    async with _client() as client:
        page = (
            await client.get("/transactions", params={"q": "invoice", "date_from": "2026-01-01"})
        ).text
    chips = re.findall(r'<a class="chip" href="([^"]+)">(.*?)<span class="sr-only">', page)
    assert [(html.unescape(href), html.unescape(label)) for href, label in chips] == [
        ("/transactions?date_from=2026-01-01", 'Description contains "invoice"'),
        ("/transactions?q=invoice", "From 2026-01-01"),
    ]
    assert "Showing only:" in _text(page)
    assert '<a class="link-action" href="/transactions">Clear all</a>' in page


async def test_no_chips_without_a_filter(seeded):
    async with _client() as client:
        page = (await client.get("/transactions")).text
    assert 'class="chip"' not in page
    assert "Showing only:" not in page


async def test_a_page_past_the_end_says_so_and_links_to_the_last(seeded):
    async with _client() as client:
        page = (await client.get("/transactions", params={"page": 9, "q": "invoice"})).text
    text = _text(page)
    assert "There's no page 9: these transactions fit on 1 page. Go to page 1" in text
    # the same raw "&" the pager's links have always used (test_ledger_pages.py)
    assert 'href="/transactions?page=1&q=invoice"' in page
    assert "No posted transactions." not in text
    assert 'class="pager"' not in page


async def test_an_empty_filter_offers_to_clear_it(seeded):
    async with _client() as client:
        page = (await client.get("/transactions", params={"q": "zzz-nothing"})).text
    assert "No transactions match that filter. Clear filters" in _text(page)


async def test_the_pager_line_shows_where_the_page_sits(seeded):
    async with seeded.begin() as conn:
        await conn.execute(
            insert(transactions),
            [{"id": uuid.uuid4(), "description": f"Filler {i:02d}"} for i in range(20)],
        )
    async with _client() as client:
        page1 = (await client.get("/transactions")).text
        page2 = (await client.get("/transactions", params={"page": 2})).text
    assert "Page 1 of 2 · 1–25 of 30" in _text(page1)
    assert "Page 2 of 2 · 26–30 of 30" in _text(page2)
    # a transaction with no entries (only possible outside the posting flow) says so
    assert "No entries." in _text(page1)


async def test_a_compound_transactions_page(seeded):
    async with _client() as client:
        page = await _detail_of(client, 5)
    text = _text(page)
    assert "Transactions › No. 5" not in text  # the separator is drawn by the stylesheet
    assert "Transactions No. 5" in text
    assert "Transaction No. 5" in text
    assert "<h1>January operating costs</h1>" in page
    assert "Entries 3: 2 debits, 1 credit" in text
    assert "Currencies USD" in text
    assert "Office rent USD 2,400.00 Expense: a debit increases it." in text
    assert "Software subscriptions USD 318.50 Expense: a debit increases it." in text
    assert "Cash USD 2,718.50 Asset: a credit decreases it." in text
    assert "Total debits USD 2,718.50" in text and "Total credits USD 2,718.50" in text
    assert "USD 2,718.50 − 2,718.50 = 0.00" in text
    # the term's card follows "balanced" in the page's text
    assert "Debits equal credits in every currency, so this transaction is balanced" in text
    assert "Two debits and one credit: the number of entries on each side needn't match" in text
    assert 'href="/learn#example-january"' in page
    assert 'href="/learn#example-two-currencies"' not in page
    assert 'href="/learn#events"' in page
    assert "Keel never edits a posted transaction." in text


async def test_a_simple_transactions_page_has_no_note_on_uneven_sides(seeded):
    async with _client() as client:
        page = await _detail_of(client, 7)
    text = _text(page)
    assert "Entries 2: 1 debit, 1 credit" in text
    assert "Equipment loan payable USD 1,500.00 Liability: a debit decreases it." in text
    assert "needn't match" not in text
    assert 'href="/learn#example-january"' not in page


async def test_a_two_currency_transactions_page_totals_each_currency(seeded):
    async with _client() as client:
        location = await _post(client, await _ids(seeded), "Mixed settlement", MIXED)
        page = (await client.get(location)).text
    text = _text(page)
    assert "Currencies EUR, USD" in text
    assert "Total debits EUR 50.00 USD 54.00" in text
    assert "Total credits EUR 50.00 USD 54.00" in text
    assert "EUR 50.00 − 50.00 = 0.00" in text and "USD 54.00 − 54.00 = 0.00" in text
    assert "104.00" not in text
    assert "Each currency balances by itself" in text
    assert 'href="/learn#example-two-currencies"' in page
    assert "Two debits and two credits" in text


async def test_a_transactions_page_defines_its_terms_once(seeded):
    async with _client() as client:
        page = await _detail_of(client, 5)
    ids = re.findall(r'\sid="([^"]+)"', page)
    assert len(ids) == len(set(ids))
    assert set(re.findall(r'class="term term-([\w-]+)"', page)) == {
        "debit",
        "credit",
        "normal-side",
        "balanced",
        "event",
    }
    targets = re.findall(r'popovertarget="([^"]+)"', page)
    assert set(targets) <= set(ids)


async def test_the_event_link_opens_the_event_logs_page_holding_it(seeded, monkeypatch):
    monkeypatch.setattr(main_module, "EVENT_LOG_PAGE_SIZE", 5)
    async with _client() as client:
        page = await _detail_of(client, 1)
        link = re.search(r'href="(/event-log\?page=\d+)"', page).group(1)
        event_id = re.search(r'<p class="meta">([0-9a-f-]{36})</p>', page).group(1)
        log = (await client.get(link)).text
    # 18 events: 8 accounts, then 10 transactions, newest first. No. 1's is the
    # 9th event, with 9 newer than it: page 2 of 5 a page.
    assert link == "/event-log?page=2"
    assert event_id in log
    assert "Event No. 9" in _text(page)


async def test_an_untitled_transaction_keeps_its_fallback(seeded):
    async with seeded.begin() as conn:
        await conn.execute(
            insert(transactions),
            [
                {
                    "id": uuid.uuid4(),
                    "description": None,
                    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                }
            ],
        )
    async with _client() as client:
        listing = (await client.get("/transactions")).text
    assert "Untitled transaction" in listing


async def test_the_filter_form_as_a_browser_sends_it(seeded):
    """
    A browser sends every field of the form, so a search by description alone
    arrives with both dates empty. That is no date filter, not a bad date.
    """
    async with _client() as client:
        by_description = await client.get("/transactions?q=rent&date_from=&date_to=")
        empty = await client.get("/transactions?q=&date_from=&date_to=")
        malformed = await client.get("/transactions?date_from=not-a-date")
    assert by_description.status_code == 200
    # "February office rent"; January's costs debit Office rent, but search reads descriptions
    assert "1 transaction matching, newest first." in _text(by_description.text)
    assert 'Description contains "rent"' in html.unescape(by_description.text)
    assert by_description.text.count('class="chip"') == 1
    assert empty.status_code == 200
    assert "10 transactions, newest first." in _text(empty.text)
    assert 'class="chip"' not in empty.text
    # anything else in a date field is still refused
    assert malformed.status_code == 422
