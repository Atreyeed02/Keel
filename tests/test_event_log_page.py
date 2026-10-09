"""
The event log as a timeline (app/event_log.py): each event in words, from
its own payload, with the raw event folded away; the explainer, and on the
demo its example and the nightly reset; the pager.

The functions need no database; the page is Postgres-backed and skips
without TEST_DATABASE_URL. The page's URL and the transaction page's link to
it are covered where they always were (tests/test_transactions_pages.py).
"""

import html
import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.db.schema import accounts, events, ledger_entries, metadata, transactions
from app.event_log import REVERSAL_EXAMPLE, account_ids, describe
from app.main import app
from scripts import seed_demo_data
from tests.support import reset_schema

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
WHEN = datetime(2026, 10, 10, 1, 28, 15, 123456, tzinfo=UTC)
CASH, FEES, EUROS = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
NAMES = {CASH: "Cash", FEES: "Fees", EUROS: "Euros"}


def _row(event_type, payload, aggregate_type="transaction", sequence=7):
    return {
        "id": uuid.uuid4(),
        "sequence": sequence,
        "aggregate_type": aggregate_type,
        "aggregate_id": uuid.uuid4(),
        "event_type": event_type,
        "payload": payload,
        "created_at": WHEN,
    }


def _posted(*entries, description="Consulting fees", version=1):
    payload = {
        "description": description,
        "entries": [
            {
                "account_id": str(a),
                "entry_type": side,
                "amount": amount,
                "currency": cur,
                "position": i,
            }
            for i, (a, side, amount, cur) in enumerate(entries)
        ],
    }
    if version is not None:
        payload["schema_version"] = version
    return _row("transaction.posted", payload)


# --- one event in words ---------------------------------------------------------------


def test_a_posted_transaction_lists_its_entries_by_name_in_their_order():
    row = _posted((CASH, "debit", "100.00", "USD"), (FEES, "credit", "100.00", "USD"))
    row["payload"]["entries"].reverse()  # stored out of order; position decides
    item = describe(row, NAMES, {row["aggregate_id"]: 5})
    assert item["kind"] == "transaction"
    assert item["title"] == "Consulting fees"
    assert [(x["entry_type"], x["name"], x["amount"]) for x in item["lines"]] == [
        ("debit", "Cash", Decimal("100.00")),
        ("credit", "Fees", Decimal("100.00")),
    ]
    assert item["balanced"] is True
    assert item["transaction_number"] == 5
    assert item["number"] == 7


def test_each_currency_is_totalled_by_itself():
    row = _posted(
        (EUROS, "debit", "50.00", "EUR"),
        (FEES, "credit", "50.00", "EUR"),
        (CASH, "debit", "54.00", "USD"),
        (FEES, "credit", "54.00", "USD"),
    )
    item = describe(row, NAMES, {})
    assert [(t["currency"], t["debits"]) for t in item["totals"]] == [
        ("EUR", Decimal("50.00")),
        ("USD", Decimal("54.00")),
    ]
    assert item["transaction_number"] is None


def test_damaged_data_would_not_be_shown_as_balanced():
    item = describe(
        _posted((CASH, "debit", "10.00", "USD"), (FEES, "credit", "9.00", "USD")), NAMES, {}
    )
    assert item["balanced"] is False


def test_an_unknown_account_is_shown_by_its_id_and_an_untitled_one_says_so():
    stranger = uuid.uuid4()
    item = describe(
        _posted(
            (stranger, "debit", "1.00", "USD"), (CASH, "credit", "1.00", "USD"), description=None
        ),
        NAMES,
        {},
    )
    assert item["lines"][0]["name"] is None
    assert item["lines"][0]["account_id"] == str(stranger)
    assert item["title"] == "Untitled transaction"


@pytest.mark.parametrize(
    ("account_type", "side"),
    [
        ("asset", "debit"),
        ("expense", "debit"),
        ("liability", "credit"),
        ("equity", "credit"),
        ("revenue", "credit"),
    ],
)
def test_an_opened_account_says_its_type_currency_and_normal_side(account_type, side):
    row = _row(
        "account.created",
        {"schema_version": 1, "name": "Cash", "account_type": account_type, "currency": "USD"},
        aggregate_type="account",
    )
    item = describe(row, {}, {})
    assert item["kind"] == "account"
    assert (item["title"], item["account_type"], item["currency"]) == (
        "Cash",
        account_type.capitalize(),
        "USD",
    )
    assert item["normal_side"] == side
    assert item["raw"]["names_accounts"] is False


def test_an_event_type_without_words_still_shows_its_raw_event():
    item = describe(_row("account.renamed", {"name": "Till"}, aggregate_type="account"), {}, {})
    assert item["kind"] == "other"
    assert "title" not in item
    assert json.loads(item["raw"]["payload"]) == {"name": "Till"}


def test_the_raw_event_as_stored():
    row = _posted((CASH, "debit", "1.00", "USD"), (FEES, "credit", "1.00", "USD"))
    raw = describe(row, NAMES, {})["raw"]
    assert raw["aggregate"] == f"transaction / {row['aggregate_id']}"
    assert raw["schema_version"] == "1"
    assert raw["recorded_at"] == "2026-10-10T01:28:15.123456+00:00"
    assert json.loads(raw["payload"]) == row["payload"]
    assert raw["names_accounts"] is True
    # before payloads carried a version, a rebuild replays them as version 1
    old = describe(_posted((CASH, "debit", "1.00", "USD"), version=None), NAMES, {})
    assert old["raw"]["schema_version"] == "1 (not recorded; replayed as version 1)"


def test_the_accounts_to_look_up_are_the_posted_entries_ones():
    rows = [
        _posted((CASH, "debit", "1.00", "USD"), (FEES, "credit", "1.00", "USD")),
        _row("account.created", {"name": "Euros"}, aggregate_type="account"),
        _row("transaction.posted", {"entries": [{"account_id": "not-a-uuid"}]}),
    ]
    assert account_ids(rows) == {CASH, FEES}


# --- the page -------------------------------------------------------------------------


def _client():
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        follow_redirects=False,
    )


def _text(page: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page))).strip()


def _items(page: str) -> list[str]:
    return [part.split(">", 1)[1] for part in page.split('<li class="timeline-item')[1:]]


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
async def seeded(database, monkeypatch):
    monkeypatch.setattr(settings, "environment", "demo")
    async with database.begin() as conn:
        await seed_demo_data.seed(conn)
    return database


async def test_the_timeline_newest_first_in_words(seeded):
    async with _client() as client:
        page = (await client.get("/event-log")).text
    items = _items(page)
    assert "18 events, newest first." in _text(page)
    assert len(items) == 18
    newest, january, cash = _text(items[0]), _text(items[5]), _text(items[-1])
    assert newest.startswith("Event No. 18 · transaction.posted")
    assert "Transaction posted: Invoice 1004 settled — Fabrikam Design" in newest
    assert january.startswith("Event No. 13")
    assert "Transaction posted: January operating costs" in january
    assert "Dr Debit Office rent USD 2,400.00" in january
    assert "Dr Debit Software subscriptions USD 318.50" in january
    assert "Cr Credit Cash USD 2,718.50" in january
    assert "Balanced Debits = credits USD 2,718.50" in january
    assert "Open transaction No. 5" in january
    assert cash.startswith("Event No. 1 · account.created")
    assert "Account opened: Cash" in cash
    assert "Asset · USD · normal side Debit" in cash
    assert "Its balance starts at 0.00: only transactions change it." in cash
    assert "Doesn't balance" not in _text(page)


async def test_each_posted_event_links_to_its_transaction(seeded):
    async with seeded.connect() as conn:
        number, transaction_id = (
            await conn.execute(
                select(transactions.c.sequence, transactions.c.id).where(
                    transactions.c.description == "January operating costs"
                )
            )
        ).one()
    async with _client() as client:
        page = (await client.get("/event-log")).text
    assert (
        f'<a class="link-action" href="/transaction-detail/{transaction_id}">'
        f"Open transaction No. {number}" in page
    )


async def test_the_raw_event_is_folded_away_and_shows_what_is_stored(seeded):
    async with seeded.connect() as conn:
        event = (await conn.execute(select(events).where(events.c.sequence == 13))).mappings().one()
    async with _client() as client:
        page = (await client.get("/event-log")).text
    item = _items(page)[5]
    assert '<details class="raw-event">' in item  # not open
    assert "Raw event: what the database stores" in _text(item)
    text = _text(item)
    assert f"Event ID {event['id']}" in text
    assert f"Aggregate transaction / {event['aggregate_id']}" in text
    assert "Event type transaction.posted" in text
    assert "Schema version 1" in text
    assert f"Recorded at {event['created_at'].astimezone(UTC).isoformat()}" in text
    payload = html.unescape(item.split('<pre class="payload">')[1].split("</pre>")[0])
    assert json.loads(payload) == event["payload"]
    assert "The payload names accounts by id; the lines above show their names." in text


async def test_the_explainer_on_the_demo(seeded):
    async with _client() as client:
        page = (await client.get("/event-log")).text
    text = _text(page)
    assert "The database refuses to change or delete an event." in text
    assert (
        "For example, undoing February's rent (No. 6) would be: "
        "Dr Debit Cash 2,400.00 · Cr Credit Office rent 2,400.00" in text
    )
    assert "On this demo, the nightly reset is the one exception" in text
    assert 'href="/learn#events"' in page
    # the example is not an event: no number, time or marker of its own
    example = page.split('<div class="example">')[1].split("</div>")[0]
    assert "Event No." not in example and "<time" not in example


async def test_the_example_undoes_the_demos_own_february_rent(seeded):
    """If the seed changes, the example must change with it."""
    async with seeded.connect() as conn:
        rows = (
            await conn.execute(
                select(
                    transactions.c.description,
                    ledger_entries.c.entry_type,
                    accounts.c.name,
                    ledger_entries.c.amount,
                )
                .join(ledger_entries, ledger_entries.c.transaction_id == transactions.c.id)
                .join(accounts, accounts.c.id == ledger_entries.c.account_id)
                .where(transactions.c.sequence == REVERSAL_EXAMPLE["number"])
            )
        ).all()
    assert {row.description for row in rows} == {"February office rent"}
    posted = {(row.entry_type, row.name, f"{row.amount:,.2f}") for row in rows}
    flipped = {
        ("credit" if side == "debit" else "debit", name, amount) for side, name, amount in posted
    }
    assert flipped == set(REVERSAL_EXAMPLE["lines"])


async def test_outside_the_demo_no_example_and_no_reset(seeded, monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    async with _client() as client:
        page = (await client.get("/event-log")).text
    text = _text(page)
    assert 'class="example"' not in page
    assert "nightly reset" not in text
    # the reversal sentence stays; the term's card sits inside it in the text
    assert "to fix a mistake, post a balanced" in text
    assert (
        "transaction with the same entries on the opposite sides, then post the right one." in text
    )


async def test_the_page_defines_its_terms_once(seeded):
    async with _client() as client:
        page = (await client.get("/event-log")).text
    ids = re.findall(r'\sid="([^"]+)"', page)
    assert len(ids) == len(set(ids))
    assert set(re.findall(r'class="term term-([\w-]+)"', page)) == {
        "event",
        "balanced",
        "debit",
        "credit",
        "normal-side",
    }


async def test_two_currencies_and_an_unknown_account(seeded):
    stranger = uuid.uuid4()
    async with seeded.begin() as conn:
        await conn.execute(
            insert(events),
            [
                {
                    "aggregate_type": "transaction",
                    "aggregate_id": uuid.uuid4(),
                    "event_type": "transaction.posted",
                    "payload": {
                        "schema_version": 1,
                        "description": "Workshop fees",
                        "entries": [
                            {
                                "account_id": str(stranger),
                                "entry_type": "debit",
                                "amount": "50.00",
                                "currency": "EUR",
                                "position": 0,
                            },
                            {
                                "account_id": str(stranger),
                                "entry_type": "credit",
                                "amount": "50.00",
                                "currency": "EUR",
                                "position": 1,
                            },
                            {
                                "account_id": str(stranger),
                                "entry_type": "debit",
                                "amount": "54.00",
                                "currency": "USD",
                                "position": 2,
                            },
                            {
                                "account_id": str(stranger),
                                "entry_type": "credit",
                                "amount": "54.00",
                                "currency": "USD",
                                "position": 3,
                            },
                        ],
                    },
                }
            ],
        )
    async with _client() as client:
        newest = _text(_items((await client.get("/event-log")).text)[0])
    assert "Debits = credits EUR 50.00 · USD 54.00" in newest
    assert f"Dr Debit {stranger} EUR 50.00" in newest
    # no transaction in the tables beside the log has this id, so no number
    assert "Open the transaction" in newest


async def test_the_pager_and_a_page_past_the_end(seeded):
    base = datetime(2026, 10, 10, tzinfo=UTC)
    async with seeded.begin() as conn:
        await conn.execute(
            insert(events),
            [
                {
                    "aggregate_type": "account",
                    "aggregate_id": uuid.uuid4(),
                    "event_type": "account.created",
                    "payload": {
                        "schema_version": 1,
                        "name": f"Filler {i}",
                        "account_type": "asset",
                        "currency": "USD",
                    },
                    "created_at": base + timedelta(minutes=i),
                }
                for i in range(12)
            ],
        )
    async with _client() as client:
        page1 = (await client.get("/event-log")).text
        page2 = (await client.get("/event-log", params={"page": 2})).text
        past = (await client.get("/event-log", params={"page": 9})).text
    assert "30 events, newest first." in _text(page1)
    assert len(_items(page1)) == 25 and len(_items(page2)) == 5
    assert "Page 1 of 2 · 1–25 of 30" in _text(page1)
    assert 'href="/event-log?page=2"' in page1
    assert "Page 2 of 2 · 26–30 of 30" in _text(page2)
    assert 'href="/event-log?page=1"' in page2
    assert "There's no page 9: these events fit on 2 pages. Go to page 2" in _text(past)
    assert 'class="pager"' not in past


async def test_one_event_and_none(database):
    async with _client() as client:
        empty = (await client.get("/event-log")).text
        await client.post(
            "/accounts", data={"name": "Cash", "account_type": "asset", "currency": "USD"}
        )
        one = (await client.get("/event-log")).text
    assert "No events yet." in _text(empty)
    assert 'class="pager"' not in empty
    assert "1 event, newest first." in _text(one)
    assert 'class="pager"' not in one
