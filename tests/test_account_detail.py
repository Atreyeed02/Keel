"""
An account's page, /account-detail/{id} (app/account_view.py): the account
as a T-account, debits on the left and credits on the right, oldest first;
why its normal side is the one it is; the sum that gives its balance,
including below zero; the pages that link to it; and the page for an id
that isn't an account's.

The functions need no database; the pages are Postgres-backed and skip
without TEST_DATABASE_URL.
"""

import html
import os
import re
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from markupsafe import escape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app import main as main_module
from app.account_view import column_note, entry_rows, other_side, proof, signed_money, why
from app.config import settings
from app.db.schema import accounts, metadata
from app.main import app
from scripts import seed_demo_data
from tests.support import reset_schema

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


# --- the words ------------------------------------------------------------------------


def test_a_balance_below_zero_carries_a_minus_sign():
    assert signed_money(Decimal("43643.5")) == "43,643.50"
    assert signed_money(Decimal("-50")) == "−50.00"
    assert signed_money(Decimal(0)) == "0.00"


@pytest.mark.parametrize(
    "account_type, normal, article",
    [
        ("asset", "debit", "an"),
        ("liability", "credit", "a"),
        ("equity", "credit", "an"),
        ("revenue", "credit", "a"),
        ("expense", "debit", "an"),
    ],
)
def test_why_the_normal_side_follows_the_type(account_type, normal, article):
    words = why("Cash", account_type, normal)
    other = "credit" if normal == "debit" else "debit"
    assert words["heading"] == f"Why {normal}?"
    assert words["article"] == article
    assert words["rule"] == (
        f"{account_type.capitalize()} accounts increase with {normal}s, so its"
    )
    assert words["result"] == (
        f"is {normal}: each {normal} adds to its balance, and each {other} subtracts from it."
    )


def test_a_column_adds_on_the_normal_side_and_subtracts_on_the_other():
    assert column_note("debit", "debit") == "add to it"
    assert column_note("credit", "debit") == "subtract"
    assert column_note("credit", "credit") == "add to it"
    assert column_note("debit", "credit") == "subtract"


def _other(side, name):
    return {"entry_type": side, "name": name}


def test_the_other_side_names_each_opposite_account_once_then_how_many_more():
    others = [
        _other("debit", "Rent"),
        _other("debit", "Software"),
        _other("debit", "Rent"),
        _other("credit", "Fees"),  # the same side as the entry: not its other side
        _other("debit", "Travel"),
        _other("debit", "Meals"),
    ]
    assert other_side("credit", others) == {
        "side": "debit",
        "shown": ["Rent", "Software", "Travel"],
        "more": 1,
    }
    assert other_side("debit", [_other("debit", "Rent")]) is None


def test_the_sum_takes_the_other_side_from_the_normal_one():
    debit = proof("Cash", "debit", Decimal("51450.00"), Decimal("7806.50"))
    assert debit["sum"] == "51,450.00 − 7,806.50 = 43,643.50"
    assert debit["because"] == "Debits minus credits, because Cash's normal side is debit."
    assert debit["below"] is None
    credit = proof("Loan", "credit", Decimal("1500.00"), Decimal("12000.00"))
    assert credit["sum"] == "12,000.00 − 1,500.00 = 10,500.00"
    assert credit["because"] == "Credits minus debits, because Loan's normal side is credit."


def test_below_zero_says_what_it_means_on_either_normal_side():
    cash = proof("Cash", "debit", Decimal("100.00"), Decimal("150.00"))
    assert cash["sum"] == "100.00 − 150.00 = −50.00"
    assert cash["below"] == (
        "Below zero: its credits are larger than its debits, so more has been taken off than "
        "added, such as cash overdrawn. On paper, this balance would sit on the credit side."
    )
    loan = proof("Loan", "credit", Decimal("150.00"), Decimal("100.00"))
    assert loan["below"] == (
        "Below zero: its debits are larger than its credits, so more has been taken off than "
        "added, such as more repaid than was borrowed. On paper, this balance would sit on "
        "the debit side."
    )


def test_entries_split_into_the_two_columns_in_their_order():
    t1, t2 = uuid.uuid4(), uuid.uuid4()
    entries = [
        {"transaction_id": t1, "entry_type": "debit", "sequence": 1},
        {"transaction_id": t2, "entry_type": "credit", "sequence": 2},
        {"transaction_id": t2, "entry_type": "debit", "sequence": 2},
    ]
    columns = entry_rows(entries, {t2: [_other("debit", "Rent"), _other("credit", "Fees")]})
    assert [row["sequence"] for row in columns["debit"]] == [1, 2]
    assert columns["debit"][0]["other"] is None
    assert columns["debit"][1]["other"]["shown"] == ["Fees"]
    assert columns["credit"][0]["other"]["shown"] == ["Rent"]


# --- the pages ------------------------------------------------------------------------


def _client():
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        follow_redirects=False,
    )


def _text(page: str) -> str:
    # the term cards' definitions are hidden until opened: leave them out
    page = re.sub(
        r'(?s)<span id="def-[^"]+" class="definition[^"]*" popover>.*?</button></span></span>',
        "",
        page,
    )
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
            "account_id": [ids[name] for name, _, _ in lines],
            "entry_type": [side for _, side, _ in lines],
            "amount": [amount for _, _, amount in lines],
            "currency": ["USD" for _ in lines],
        },
    )
    assert response.status_code == 302, response.text


def _column(page: str, side: str) -> str:
    return page.split(f'class="side-panel side-panel-{side}"')[1].split("</section>")[0]


def _numbers(column: str) -> list[int]:
    return [int(n) for n in re.findall(r'class="tx-number">No\. (\d+)<', column)]


async def test_the_chart_of_accounts_links_each_account_to_its_page(seeded, monkeypatch):
    monkeypatch.setattr("app.api.accounts.engine", seeded)
    ids = await _ids(seeded)
    async with _client() as client:
        overview = (await client.get("/")).text
        api = (await client.get("/api/accounts")).json()
        pages = {name: (await client.get(f"/account-detail/{i}")).text for name, i in ids.items()}
    assert 'id="accounts"' in overview
    for name, account_id in ids.items():
        assert f'<a href="/account-detail/{account_id}">{escape(name)}</a>' in overview
    # each page's balance is the overview's and the JSON API's
    for row in api["accounts"]:
        balance = f"{Decimal(str(row['balance'])):,.2f}"
        text = _text(pages[row["name"]])
        assert f"Balance USD {balance}" in text or f"Balance EUR {balance}" in text, row["name"]


async def test_cash_as_a_t_account_oldest_first(seeded):
    ids = await _ids(seeded)
    async with _client() as client:
        page = (await client.get(f"/account-detail/{ids['Cash']}")).text
    text = _text(page)
    debits, credits = _column(page, "debit"), _column(page, "credit")
    assert _numbers(debits) == [1, 2, 3, 4, 10]
    assert _numbers(credits) == [5, 6, 7, 8]
    assert "Debits · add to it 5 entries" in _text(debits)
    assert "Credits · subtract 4 entries" in _text(credits)
    assert "Total debits USD 51,450.00" in _text(debits)
    assert "Total credits USD 7,806.50" in _text(credits)
    assert "USD 51,450.00 − 7,806.50 = 43,643.50" in text
    assert "Debits minus credits, because Cash's normal side is debit." in text
    assert (
        "Cash is an asset account. Asset accounts increase with debits, so its normal side "
        "is debit: each debit adds to its balance, and each credit subtracts from it."
    ) in text
    assert "Below zero" not in text
    # each entry's one link is its transaction, and it names the other side
    assert "Opening capital contribution 25,000.00 Other side: Cr Credit Owner's capital" in text
    assert (
        "January operating costs 2,718.50 Other side: Dr Debit Office rent, Software "
        "subscriptions"
    ) in text
    entries = re.findall(r'(?s)<li class="acct-entry">.*?</li>', page)
    assert len(entries) == 9
    assert all(len(re.findall(r"<a ", entry)) == 1 for entry in entries)
    assert all('href="/transaction-detail/' in entry for entry in entries)


async def test_a_credit_normal_account_reads_the_other_way_round(seeded):
    ids = await _ids(seeded)
    async with _client() as client:
        text = _text((await client.get(f"/account-detail/{ids['Equipment loan payable']}")).text)
    assert "Why credit?" in text
    assert "Equipment loan payable is a liability account." in text
    assert "Debits · subtract 1 entry" in text
    assert "Credits · add to it 1 entry" in text
    assert "USD 12,000.00 − 1,500.00 = 10,500.00" in text
    assert "On its normal side, credit" in text


async def test_a_balance_below_zero_is_shown_and_explained(seeded):
    ids = await _ids(seeded)
    async with _client() as client:
        await _post(
            client,
            ids,
            "Big rent",
            [("Office rent", "debit", "50000.00"), ("Cash", "credit", "50000.00")],
        )
        cash = _text((await client.get(f"/account-detail/{ids['Cash']}")).text)
        overview = _text((await client.get("/")).text)
    assert "Balance Below zero USD −6,356.50" in cash
    assert "USD 51,450.00 − 57,806.50 = −6,356.50" in cash
    assert "such as cash overdrawn. On paper, this balance would sit on the credit side." in cash
    assert "Why: normal side" in cash
    # the overview shows the same number
    assert "-6,356.50" in overview


async def test_a_new_account_has_no_entries_yet(seeded):
    async with _client() as client:
        response = await client.post(
            "/accounts", data={"name": "Petty cash", "account_type": "asset", "currency": "USD"}
        )
        assert response.status_code == 302
        account_id = (await _ids(seeded))["Petty cash"]
        page = (await client.get(f"/account-detail/{account_id}")).text
    text = _text(page)
    assert "No entries yet, so the balance is 0.00: only transactions change it." in text
    assert "Balance USD 0.00" in text
    assert 'class="side-panel' not in page
    assert "Opened in Event No. 19" in text


async def test_many_entries_come_a_page_at_a_time(seeded, monkeypatch):
    monkeypatch.setattr(main_module, "ACCOUNT_PAGE_SIZE", 3)
    account_id = (await _ids(seeded))["Cash"]
    base = f"/account-detail/{account_id}"
    async with _client() as client:
        first = (await client.get(base)).text
        second = (await client.get(f"{base}?page=2")).text
        third = (await client.get(f"{base}?page=3")).text
        past = (await client.get(f"{base}?page=9")).text
    # oldest first across both sides: Nos. 1-3, then 4, 5, 6, then 7, 8, 10
    assert _numbers(_column(first, "debit")) == [1, 2, 3]
    assert "No credit entries on this page." in _text(first)
    assert _numbers(_column(second, "debit")) == [4]
    assert _numbers(_column(second, "credit")) == [5, 6]
    assert _numbers(_column(third, "debit")) == [10]
    assert _numbers(_column(third, "credit")) == [7, 8]
    text = _text(second)
    assert "Page 2 of 3 · 4–6 of 9 entries" in text
    assert "Totals are for all 9 entries, not just this page." in text
    # the counts and totals are the whole account's on every page
    assert "5 entries" in _text(_column(second, "debit"))
    assert "Total debits USD 51,450.00" in text
    assert f'href="{base}?page=1"' in second and f'href="{base}?page=3"' in second
    assert "Earlier" in text and "Later" in text
    assert "Later" not in _text(third)
    assert "There's no page 9: these entries fit on 3 pages." in _text(past)
    assert f'href="{base}?page=3"' in past


@pytest.mark.parametrize("path", ["00000000-0000-0000-0000-000000000000", "not-an-id"])
async def test_an_unknown_account_gets_a_page_saying_so(seeded, path):
    async with _client() as client:
        response = await client.get(f"/account-detail/{path}")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    text = _text(response.text)
    assert "No such account There's no account with this ID." in text
    assert "The demo's data resets nightly, so a link from before the last reset" in text
    assert 'href="/#accounts"' in response.text


async def test_outside_the_demo_the_missing_page_says_nothing_of_resets(seeded, monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    async with _client() as client:
        text = _text((await client.get(f"/account-detail/{uuid.uuid4()}")).text)
    assert "There's no account with this ID." in text
    assert "resets" not in text.split("Post transaction", 1)[1]


async def test_transactions_and_the_event_log_link_to_accounts(seeded):
    ids = await _ids(seeded)
    async with _client() as client:
        listing = (await client.get("/transactions")).text
        detail_href = dict(
            re.findall(
                r'No\. (\d+)</span>.*?href="(/transaction-detail/[0-9a-f-]+)"', listing, re.S
            )
        )["5"]
        detail = (await client.get(detail_href)).text
        log = (await client.get("/event-log")).text
    for name in ("Office rent", "Software subscriptions", "Cash"):
        assert f'<a class="entry-account" href="/account-detail/{ids[name]}">{name}</a>' in detail
    opened = re.findall(r'href="/account-detail/([0-9a-f-]+)">Open account', log)
    assert sorted(opened) == sorted(ids.values())


async def test_an_accounts_page_defines_its_terms_once(seeded):
    ids = await _ids(seeded)
    async with _client() as client:
        page = (await client.get(f"/account-detail/{ids['Office rent']}")).text
    found = re.findall(r'\sid="([^"]+)"', page)
    assert len(found) == len(set(found))
    assert set(re.findall(r'class="term term-([\w-]+)"', page)) == {
        "expense",
        "normal-side",
        "t-account",
        "debit",
        "credit",
        "event",
    }
    assert set(re.findall(r'popovertarget="([^"]+)"', page)) <= set(found)


async def test_the_event_link_opens_the_event_logs_page_holding_it(seeded, monkeypatch):
    monkeypatch.setattr(main_module, "EVENT_LOG_PAGE_SIZE", 5)
    account_id = (await _ids(seeded))["Cash"]
    async with _client() as client:
        page = (await client.get(f"/account-detail/{account_id}")).text
        link = re.search(r'href="(/event-log\?page=\d+)"', page).group(1)
        log = (await client.get(link)).text
    # 18 events, newest first; Cash's is No. 1, with 17 newer: page 4 of 5 a page
    assert link == "/event-log?page=4"
    assert f'href="/account-detail/{account_id}">Open account' in log
