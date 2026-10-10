"""
The overview's accounting equation per currency and, on the demo, the "try
it" steps and the notes they open the posting form with (app/overview.py).

The functions need no database; the pages are Postgres-backed and skip
without TEST_DATABASE_URL.
"""

import html
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qsl, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.db.schema import metadata
from app.glossary import GLOSSARY
from app.main import app
from app.overview import STEPS, equations, try_note, try_steps
from scripts import seed_demo_data
from tests.support import reset_schema

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
TRY_LINK = '<a class="try-link" href="/#try-it">'


def _client():
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        follow_redirects=False,
    )


def _account(name, account_type, currency, balance, moved=True, created=0):
    return {
        "id": uuid.uuid4(),
        "name": name,
        "account_type": account_type,
        "currency": currency,
        "created_at": datetime(2026, 10, 1, tzinfo=UTC) + timedelta(seconds=created),
        "debits": Decimal(1) if moved else Decimal(0),
        "credits": Decimal(0),
        "balance": Decimal(balance),
    }


# --- the equation ---------------------------------------------------------------------


def test_the_equation_adds_up_each_type_per_currency_in_currency_order():
    rows = equations(
        [
            _account("Cash", "asset", "USD", "900.00"),
            _account("Bank", "asset", "USD", "100.00"),
            _account("Loan", "liability", "USD", "300.00"),
            _account("Capital", "equity", "USD", "500.00"),
            _account("Fees", "revenue", "USD", "450.00"),
            _account("Rent", "expense", "USD", "250.00"),
            _account("Euros", "asset", "EUR", "80.00"),
            _account("Euro fees", "revenue", "EUR", "80.00"),
        ]
    )
    assert [row["currency"] for row in rows] == ["EUR", "USD"]
    assert rows[1] == {
        "currency": "USD",
        "assets": "1,000.00",
        "liabilities": "300.00",
        "equity": "500.00",
        "revenue": "450.00",
        "expenses": "250.00",
        "earned": "200.00",
        "holds": True,
    }
    assert rows[0]["assets"] == rows[0]["earned"] == "80.00"
    assert rows[0]["liabilities"] == rows[0]["equity"] == "0.00"


def test_a_currency_without_entries_has_no_equation():
    rows = equations(
        [
            _account("Cash", "asset", "USD", "10.00"),
            _account("Fees", "revenue", "USD", "10.00"),
            _account("Pounds", "asset", "GBP", "0", moved=False),
        ]
    )
    assert [row["currency"] for row in rows] == ["USD"]
    assert equations([]) == []


def test_a_loss_shows_as_a_negative_revenue_minus_expenses():
    (row,) = equations(
        [
            _account("Cash", "asset", "USD", "700.00"),
            _account("Capital", "equity", "USD", "1000.00"),
            _account("Rent", "expense", "USD", "300.00"),
        ]
    )
    assert row["earned"] == "-300.00"
    assert row["holds"]


def test_the_equation_says_so_when_it_does_not_hold():
    (row,) = equations(
        [_account("Cash", "asset", "USD", "10.00"), _account("Fees", "revenue", "USD", "9.00")]
    )
    assert not row["holds"]


# --- try it ---------------------------------------------------------------------------


def _demo_accounts():
    return [
        _account("Cash", "asset", "USD", "0"),
        _account("EUR operating account", "asset", "EUR", "0"),
        _account("Consulting revenue", "revenue", "USD", "0"),
        _account("Software subscriptions", "expense", "USD", "0"),
    ]


def test_each_step_links_to_the_form_filled_in_with_the_demo_accounts():
    accounts = _demo_accounts()
    ids = {a["name"]: str(a["id"]) for a in accounts}
    steps = try_steps(accounts)
    assert [s["number"] for s in steps] == [1, 2, 3]
    queries = [parse_qsl(urlsplit(s["href"]).query) for s in steps]
    assert all(urlsplit(s["href"]).path == "/post-transaction" for s in steps)
    assert queries[0] == [
        ("try", "1"),
        ("description", "Try it: one month of a note-taking app"),
        ("account_id", ids["Software subscriptions"]),
        ("entry_type", "debit"),
        ("amount", "12.00"),
        ("currency", "USD"),
        ("account_id", ids["Cash"]),
        ("entry_type", "credit"),
        ("amount", "12.00"),
        ("currency", "USD"),
    ]
    assert ("amount", "10.00") in queries[1]
    assert ("try", "2") in queries[1]
    assert queries[2][2:] == [
        ("account_id", ids["EUR operating account"]),
        ("entry_type", "debit"),
        ("amount", "50.00"),
        ("currency", "EUR"),
        ("account_id", ids["Consulting revenue"]),
        ("entry_type", "credit"),
        ("amount", "50.00"),
        ("currency", "USD"),
    ]
    assert [s["learn"] for s in steps] == [
        None,
        ("balanced", "when a transaction balances"),
        ("example-two-currencies", "two currencies"),
    ]


def test_the_oldest_account_of_a_name_is_the_demos():
    accounts = _demo_accounts()
    later = _account("Cash", "asset", "USD", "0", created=60)
    steps = try_steps([later, *accounts])
    cash = next(a for a in accounts if a["name"] == "Cash")
    assert str(cash["id"]) in steps[0]["href"]
    assert str(later["id"]) not in steps[0]["href"]


def test_the_steps_need_every_account_by_name_and_currency():
    accounts = _demo_accounts()
    assert try_steps(accounts[1:]) is None
    accounts[0]["currency"] = "EUR"  # a Cash in euros is not the demo's
    assert try_steps(accounts) is None


def test_the_form_notes():
    assert try_note("1")["text"] == (
        "Try it, 1 of 3: this one balances. Press Post transaction, then go back to the Overview."
    )
    assert try_note("2")["text"] == (
        "Try it, 2 of 3: this one is 2.00 short. Try pressing Post transaction: Keel won't post "
        "it, and the banner shows why."
    )
    assert try_note("3")["text"] == (
        "Try it, 3 of 3: each currency is out of balance. Try pressing Post transaction: Keel "
        "won't post it, and the banner shows why."
    )
    for other in (None, "", "0", "4", "01", "x", "1.0"):
        assert try_note(other) is None
    assert len(STEPS) == 3


# --- the pages ------------------------------------------------------------------------


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setattr(settings, "environment", "demo")


@pytest.fixture
async def database(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL page integration tests")
    test_engine = create_async_engine(TEST_DATABASE_URL)
    monkeypatch.setattr("app.main.engine", test_engine)
    async with test_engine.begin() as conn:
        await reset_schema(conn)
    yield test_engine
    async with test_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
    await test_engine.dispose()


@pytest.fixture
async def seeded(database):
    async with database.begin() as conn:
        await seed_demo_data.seed(conn)


def _text(page: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def _step_links(page: str) -> list[str]:
    return [html.unescape(h) for h in re.findall(r'href="(/post-transaction\?try=[^"]+)"', page)]


def _form(page: str) -> list[tuple[str, str]]:
    """The fields a browser would submit from the posting form, as drawn."""
    form = page.split('<form action="/post-transaction"')[1].split("</form>")[0]
    fields = [
        (name, html.unescape(value))
        for name, value in re.findall(r'<input[^>]*name="(\w+)"[^>]*value="([^"]*)"', form)
    ]
    for name, body in re.findall(r'<select name="(\w+)"[^>]*>(.*?)</select>', form, re.S):
        chosen = re.search(r'<option value="([^"]*)"[^>]*selected', body)
        fields.append((name, chosen.group(1) if chosen else ""))
    return fields


async def test_the_demo_overview_shows_the_equation_per_currency(seeded, demo):
    async with _client() as client:
        page = (await client.get("/")).text
    text = _text(page)
    # the demo's figures, as /learn writes them out; EUR first, like the trial balance
    eur = "1,800.00 = 0.00 + 0.00 + (1,800.00 − 0.00)"
    usd = "43,643.50 = 10,500.00 + 25,000.00 + (14,450.00 − 6,306.50)"
    assert eur in text and usd in text
    assert text.index(eur) < text.index(usd)
    assert text.count("Holds") == 2
    assert "Doesn't hold" not in text
    assert "Earned 14,450.00, spent 6,306.50" in text
    assert "8,143.50" in text
    # the hero, then the steps, then the trial balance
    assert page.index('id="equation"') < page.index('id="try-it"') < page.index("Trial balance")
    assert len(_step_links(page)) == 3


async def test_the_demo_overview_defines_every_term_once(seeded, demo):
    async with _client() as client:
        page = (await client.get("/")).text
    ids = re.findall(r'\sid="([^"]+)"', page)
    assert len(ids) == len(set(ids))
    # all but "t-account", which only an account's page uses
    assert set(re.findall(r'class="term term-([\w-]+)"', page)) == set(GLOSSARY) - {"t-account"}
    # the formula names the five types; the chart's group rows no longer do
    formula = page.split('class="formula"')[1].split("</p>")[0]
    assert set(re.findall(r"term term-([\w-]+)", formula)) == {
        "asset",
        "liability",
        "equity",
        "revenue",
        "expense",
    }


async def test_the_notice_links_to_the_steps_on_every_demo_page(seeded, demo):
    async with _client() as client:
        for path in (
            "/",
            "/transactions",
            "/post-transaction",
            "/event-log",
            "/accounts/new",
            "/learn",
        ):
            assert TRY_LINK in (await client.get(path)).text, path


async def test_outside_the_demo_there_are_no_steps_and_no_notes(seeded, monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    async with _client() as client:
        page = (await client.get("/")).text
        form = (await client.get("/post-transaction?try=2")).text
    assert 'id="try-it"' not in page
    assert TRY_LINK not in page
    assert 'id="try-note"' not in form
    assert 'name="try"' not in form
    # the equation is not a demo feature
    assert "43,643.50 = 10,500.00" in _text(page)


async def test_an_empty_ledger_has_no_equation_and_no_steps(database, demo):
    async with _client() as client:
        page = (await client.get("/")).text
    assert "No entries yet." in _text(page)
    assert 'class="eq-currency"' not in page
    assert 'id="try-it"' not in page


async def test_each_step_opens_the_form_filled_in_with_its_note(seeded, demo):
    async with _client() as client:
        links = _step_links((await client.get("/")).text)
        pages = [(await client.get(link)).text for link in links]
    expected_panel = [
        "Balanced ✓ Debits equal credits in USD. Ready to post.",
        "Out of balance by 2.00 USD.",
        "Out of balance in 2 currencies.",
    ]
    for number, (page, panel) in enumerate(zip(pages, expected_panel, strict=True), start=1):
        text = _text(page)
        assert try_note(str(number))["text"] in text
        assert panel in text
        assert f'<input type="hidden" name="try" value="{number}">' in page
        assert 'href="/#try-it"' in page.split('id="try-note"')[1].split("</div>")[0]
    assert 'value="Try it: one month of a note-taking app"' in pages[0]
    assert 'value="Try it: a payment in two currencies"' in pages[2]


async def test_the_note_stays_while_the_form_is_redrawn_or_refused(seeded, demo):
    async with _client() as client:
        link = _step_links((await client.get("/")).text)[1]
        fields = _form((await client.get(link)).text)
        # without JavaScript, "Add line" is a GET carrying every field
        added = await client.get("/post-transaction", params=[*fields, ("add_line", "1")])
        refused = await client.post("/post-transaction", data=_pairs(fields))
    assert ("try", "2") in fields
    assert try_note("2")["text"] in _text(added.text)
    assert added.text.count('<fieldset class="line') - 1 == 3  # one more line (less the template's)
    assert refused.status_code == 422
    assert try_note("2")["text"] in _text(refused.text)
    assert "Nothing was posted." in _text(refused.text)


async def test_the_first_step_posts_and_the_equation_still_holds(seeded, demo):
    async with _client() as client:
        link = _step_links((await client.get("/")).text)[0]
        fields = _form((await client.get(link)).text)
        posted = await client.post("/post-transaction", data=_pairs(fields))
        page = (await client.get("/")).text
    assert posted.status_code == 302
    assert posted.headers["location"].startswith("/transaction-detail/")
    text = _text(page)
    # Assets and Revenue − Expenses both fall by 12.00
    assert "43,631.50 = 10,500.00 + 25,000.00 + (14,450.00 − 6,318.50)" in text
    assert text.count("Holds") == 2


def _pairs(fields: list[tuple[str, str]]) -> dict[str, list[str]]:
    data: dict[str, list[str]] = {}
    for name, value in fields:
        data.setdefault(name, []).append(value)
    return data
