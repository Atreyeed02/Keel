import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import create_async_engine

from app import main as main_module
from app.db.schema import accounts, metadata, transactions
from app.domain.ledger import EntryAccountError
from app.main import app

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
async def database(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL page integration tests")
    test_engine = create_async_engine(TEST_DATABASE_URL)
    monkeypatch.setattr("app.main.engine", test_engine)
    async with test_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)
        cash_id, revenue_id = uuid.uuid4(), uuid.uuid4()
        await conn.execute(
            insert(accounts),
            [
                {"id": cash_id, "name": "Cash", "account_type": "asset", "currency": "USD"},
                {"id": revenue_id, "name": "Revenue", "account_type": "revenue", "currency": "USD"},
            ],
        )
    yield cash_id, revenue_id
    async with test_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
    await test_engine.dispose()


def _submission(cash_id, revenue_id, amount="100.00"):
    return {
        "description": "Test sale",
        "submission_key": "test-submission-key",
        "account_id": [str(cash_id), str(revenue_id)],
        "entry_type": ["debit", "credit"],
        "amount": [amount, "100.00"],
        "currency": ["USD", "USD"],
    }


async def test_idempotent_retry_returns_same_transaction_id(database):
    cash_id, revenue_id = database
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        first = await client.post("/post-transaction", data=_submission(cash_id, revenue_id))
        retry = await client.post("/post-transaction", data=_submission(cash_id, revenue_id))
    assert first.status_code == retry.status_code == 302
    assert first.headers["location"] == retry.headers["location"]


async def test_unbalanced_submission_renders_inline_error(database):
    cash_id, revenue_id = database
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/post-transaction", data=_submission(cash_id, revenue_id, "101.00")
        )
    assert response.status_code == 422
    assert "Cannot post transaction" in response.text
    assert "does not balance" in response.text


async def test_created_account_appears_in_overview(database):
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        created = await client.post(
            "/accounts",
            data={"name": "Office rent", "account_type": "expense", "currency": "usd"},
        )
        overview = await client.get("/")
    assert created.status_code == 302
    assert created.headers["location"] == "/"
    assert overview.status_code == 200
    assert "Office rent" in overview.text


async def test_invalid_account_type_renders_inline_error(database):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/accounts",
            data={"name": "Suspense", "account_type": "banana", "currency": "USD"},
        )
        overview = await client.get("/")
    assert response.status_code == 422
    assert "Cannot create account" in response.text
    assert "account_type must be one of" in response.text
    # the submitted values come back so the form isn't retyped from scratch
    assert 'value="Suspense"' in response.text
    # and nothing was written
    assert "Suspense" not in overview.text


async def test_overview_shows_posted_account_balance(database):
    cash_id, revenue_id = database
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        await client.post("/post-transaction", data=_submission(cash_id, revenue_id))
        overview = await client.get("/")
    assert overview.status_code == 200
    assert "Cash" in overview.text
    assert "100.00" in overview.text


@pytest.fixture
async def eur_accounts(database):
    """A EUR account pair, alongside the USD pair `database` already made."""
    bank_id, revenue_id = uuid.uuid4(), uuid.uuid4()
    async with main_module.engine.begin() as conn:
        await conn.execute(
            insert(accounts),
            [
                {"id": bank_id, "name": "EUR bank", "account_type": "asset", "currency": "EUR"},
                {
                    "id": revenue_id,
                    "name": "EUR revenue",
                    "account_type": "revenue",
                    "currency": "EUR",
                },
            ],
        )
    return bank_id, revenue_id


async def test_unknown_account_renders_inline_error(database):
    """A nonexistent account_id is a form error, not a foreign-key 500."""
    cash_id, revenue_id = database
    ghost_id = uuid.uuid4()
    submission = _submission(cash_id, revenue_id)
    submission["account_id"] = [str(cash_id), str(ghost_id)]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/post-transaction", data=submission)
        overview = await client.get("/")
    assert response.status_code == 422
    assert "Cannot post transaction" in response.text
    assert "no account exists with id" in response.text
    # the offending id is named, so the message is actionable
    assert str(ghost_id) in response.text
    # and the transaction was rolled back, not half-written
    assert "Test sale" not in overview.text


async def test_currency_mismatch_renders_inline_error(database):
    """An entry may not carry a currency its account doesn't hold."""
    cash_id, revenue_id = database  # both USD
    submission = _submission(cash_id, revenue_id)
    # balances cleanly in EUR, so this gets past assert_balanced and is
    # caught by the account check rather than the invariant check
    submission["currency"] = ["EUR", "EUR"]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/post-transaction", data=submission)
        overview = await client.get("/")
    assert response.status_code == 422
    assert "Cannot post transaction" in response.text
    # the message names the account, its currency, and what was submitted
    assert "is USD" in response.text
    assert "submitted as EUR" in response.text
    assert "Cash" in response.text
    # crucially: it did not silently succeed
    assert "Test sale" not in overview.text


async def test_multi_currency_transaction_still_posts(database, eur_accounts):
    """
    The check constrains an entry to *its own* account's currency — it does
    not stop one transaction touching several currencies. Each side here
    matches the account it names and balances within its own currency.
    """
    cash_id, usd_revenue_id = database
    eur_bank_id, eur_revenue_id = eur_accounts
    submission = {
        "description": "Mixed-currency settlement",
        "submission_key": "multi-currency-key",
        "account_id": [
            str(cash_id),
            str(usd_revenue_id),
            str(eur_bank_id),
            str(eur_revenue_id),
        ],
        "entry_type": ["debit", "credit", "debit", "credit"],
        "amount": ["100.00", "100.00", "50.00", "50.00"],
        "currency": ["USD", "USD", "EUR", "EUR"],
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        response = await client.post("/post-transaction", data=submission)
        overview = await client.get("/")
    assert response.status_code == 302
    # both currencies get their own totals line rather than one summed figure
    assert "USD" in overview.text
    assert "EUR" in overview.text
    assert "100.00" in overview.text
    assert "50.00" in overview.text


async def test_malformed_amount_renders_a_readable_message(database):
    """
    A raw pydantic ValidationError must not reach the form alert.

    `str(ValidationError)` is a multi-line dump — "1 validation error for
    EntryInput", a "[type=..., input_value=...]" block and a docs URL. The
    handler flattens it instead, so the alert gets one sentence.
    """
    cash_id, revenue_id = database
    submission = _submission(cash_id, revenue_id)
    submission["amount"] = ["abc", "100.00"]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/post-transaction", data=submission)
    assert response.status_code == 422
    assert "Cannot post transaction" in response.text
    # the readable form: names the field, says what was wrong
    assert "amount" in response.text
    assert "input should be a valid decimal" in response.text
    # and none of pydantic's machinery leaks through
    for dump_marker in (
        "validation error for",
        "[type=",
        "input_value=",
        "input_type=",
        "errors.pydantic.dev",
        "value_error",
    ):
        assert dump_marker not in response.text, f"pydantic dump leaked: {dump_marker!r}"


async def test_unbalanced_message_is_unchanged_by_the_flattener(database):
    """The other two exception types still render str(exc), as before."""
    cash_id, revenue_id = database
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/post-transaction", data=_submission(cash_id, revenue_id, "101.00")
        )
    assert response.status_code == 422
    assert "does not balance per currency" in response.text


async def test_missing_account_and_currency_mismatch_report_together(database, eur_accounts):
    """
    Both classes of problem in one submission must come back in one response.

    Previously the missing account short-circuited before currencies were
    looked at, so fixing it only revealed the mismatch on the next attempt.
    Line 1 names an id that does not exist; line 2 points at a real EUR
    account but submits USD.
    """
    eur_bank_id, _eur_revenue_id = eur_accounts
    ghost_id = uuid.uuid4()
    submission = {
        "description": "Two problems at once",
        "submission_key": "two-problems-key",
        "account_id": [str(ghost_id), str(eur_bank_id)],
        "entry_type": ["debit", "credit"],
        "amount": ["100.00", "100.00"],
        "currency": ["USD", "USD"],
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/post-transaction", data=submission)
        overview = await client.get("/")
    assert response.status_code == 422
    # problem 1: the nonexistent account, named
    assert "no account exists with id" in response.text
    assert str(ghost_id) in response.text
    # problem 2: the currency mismatch on the OTHER line, in the same response.
    # Matched as one phrase: the bare account name also appears in the form's
    # account dropdown, so asserting on it alone would pass without the fix.
    # Jinja escapes the apostrophes the message wraps the name in.
    assert "&#39;EUR bank&#39; is EUR, but an entry was submitted as USD" in response.text
    # nothing was written
    assert "Two problems at once" not in overview.text


def test_entry_account_error_keeps_both_problem_lists():
    """The merged error still exposes the two kinds separately for callers."""
    exc = EntryAccountError(["abc-123"], ["account 'X' is EUR, but an entry was submitted as USD"])
    assert exc.missing == ["abc-123"]
    assert exc.mismatched == ["account 'X' is EUR, but an entry was submitted as USD"]
    assert "no account exists with id: abc-123" in str(exc)
    assert "account 'X' is EUR" in str(exc)


async def _post(client, cash_id, revenue_id, description, key):
    """Post one balanced transaction through the real form endpoint."""
    data = _submission(cash_id, revenue_id)
    data["description"] = description
    data["submission_key"] = key
    return await client.post("/post-transaction", data=data)


async def _insert_transactions(descriptions, *, base):
    """
    Insert transaction rows straight into the read model.

    Bypasses the posting flow deliberately: these exist to fill pages, and
    26 real submissions would make the test slow for no extra coverage.
    created_at is set explicitly and spaced a minute apart — the column
    defaults to CURRENT_TIMESTAMP, which Postgres evaluates at transaction
    start, so a bulk insert would give every row the same timestamp and
    leave "newest first" ordering arbitrary.
    """
    async with main_module.engine.begin() as conn:
        await conn.execute(
            insert(transactions),
            [
                {
                    "id": uuid.uuid4(),
                    "description": description,
                    "created_at": base + timedelta(minutes=index),
                }
                for index, description in enumerate(descriptions)
            ],
        )


async def test_transactions_page_lists_a_posted_transaction(database):
    cash_id, revenue_id = database
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _post(client, cash_id, revenue_id, "Invoice 9001 settled", "listing-key")
        response = await client.get("/transactions")
    assert response.status_code == 200
    assert "Invoice 9001 settled" in response.text
    # the row links to its detail page, as the overview's table does
    assert "/transaction-detail/" in response.text
    assert "1 transaction," in response.text


async def test_transactions_description_filter_narrows_results(database):
    cash_id, revenue_id = database
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _post(client, cash_id, revenue_id, "Zephyr consulting fee", "filter-a")
        await _post(client, cash_id, revenue_id, "Quokka hardware purchase", "filter-b")
        unfiltered = await client.get("/transactions")
        filtered = await client.get("/transactions", params={"q": "zephyr"})
    # both present without a filter
    assert "Zephyr consulting fee" in unfiltered.text
    assert "Quokka hardware purchase" in unfiltered.text
    # only the match survives the filter — and ILIKE means case doesn't matter
    assert filtered.status_code == 200
    assert "Zephyr consulting fee" in filtered.text
    assert "Quokka hardware purchase" not in filtered.text
    assert "1 transaction matching" in filtered.text


async def test_transactions_date_range_filter(database):
    base = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    await _insert_transactions(["March first entry"], base=base)
    june = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    await _insert_transactions(["June entry"], base=june)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        march = await client.get(
            "/transactions", params={"date_from": "2026-03-01", "date_to": "2026-03-31"}
        )
    assert march.status_code == 200
    # date_to is inclusive of the whole closing day, not midnight on it
    assert "March first entry" in march.text
    assert "June entry" not in march.text


async def test_transactions_pagination_beyond_page_one(database):
    # 30 rows against a page size of 25 => 25 on page 1, 5 on page 2
    base = datetime(2026, 4, 1, 9, 0, tzinfo=UTC)
    await _insert_transactions([f"Bulk transaction {i:02d}" for i in range(30)], base=base)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        page1 = await client.get("/transactions")
        page2 = await client.get("/transactions", params={"page": 2})
    assert page1.status_code == page2.status_code == 200
    assert "30 transactions," in page1.text
    assert page1.text.count("/transaction-detail/") == 25
    assert page2.text.count("/transaction-detail/") == 5
    # newest first: 29 is the most recent, so it heads page 1 and 00 falls to page 2
    assert "Bulk transaction 29" in page1.text
    assert "Bulk transaction 29" not in page2.text
    assert "Bulk transaction 00" in page2.text
    assert "Bulk transaction 00" not in page1.text
    # and the pager offers a way forward from page 1
    assert "/transactions?page=2" in page1.text


async def test_transactions_filter_survives_pagination(database):
    base = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    await _insert_transactions([f"Widget order {i:02d}" for i in range(30)], base=base)
    await _insert_transactions(["Unrelated thing"], base=base + timedelta(days=1))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        page1 = await client.get("/transactions", params={"q": "widget"})
        page2 = await client.get("/transactions", params={"q": "widget", "page": 2})
    # the count reflects the filtered set, not the whole table
    assert "30 transactions matching" in page1.text
    # the pager link carries the filter forward rather than dropping it
    assert "page=2&q=widget" in page1.text
    # and page 2 is still filtered
    assert page2.text.count("/transaction-detail/") == 5
    assert "Unrelated thing" not in page2.text
    assert "Unrelated thing" not in page1.text


async def test_listings_order_by_sequence_not_created_at(database):
    """
    Ordering must follow `sequence`, not `created_at`.

    The three rows are inserted with created_at values in the OPPOSITE
    order to their insertion, so the two columns disagree: under
    `created_at DESC` the newest-looking row is "Oldest inserted", under
    `sequence DESC` it is "Newest inserted". Sorting by the wrong column
    is therefore visible rather than merely possible.
    """
    base = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    async with main_module.engine.begin() as conn:
        for offset, description in enumerate(
            ["Oldest inserted", "Middle inserted", "Newest inserted"]
        ):
            await conn.execute(
                insert(transactions),
                [
                    {
                        "id": uuid.uuid4(),
                        "description": description,
                        # inverted: the first row inserted gets the LATEST timestamp
                        "created_at": base - timedelta(hours=offset),
                    }
                ],
            )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listing = await client.get("/transactions")
        overview = await client.get("/")

    def order_of(text):
        return sorted(
            ["Oldest inserted", "Middle inserted", "Newest inserted"], key=text.index
        )

    expected = ["Newest inserted", "Middle inserted", "Oldest inserted"]
    # what created_at DESC would have produced, i.e. the bug
    wrong = ["Oldest inserted", "Middle inserted", "Newest inserted"]
    assert order_of(listing.text) == expected
    assert order_of(listing.text) != wrong
    assert order_of(overview.text) == expected
    assert order_of(overview.text) != wrong


async def test_transactions_written_together_get_distinct_sequences(database):
    """
    The collision this fixes: rows written in ONE database transaction share
    a created_at, because Postgres evaluates CURRENT_TIMESTAMP at
    transaction start. sequence is assigned per insert, so it stays distinct.
    """
    async with main_module.engine.begin() as conn:
        await conn.execute(
            insert(transactions),
            [{"id": uuid.uuid4(), "description": f"Batch {i}"} for i in range(10)],
        )
        rows = (
            (
                await conn.execute(
                    select(transactions.c.sequence, transactions.c.created_at)
                )
            )
            .mappings()
            .all()
        )
    assert len({row["created_at"] for row in rows}) == 1, "created_at should collide"
    assert len({row["sequence"] for row in rows}) == 10, "sequence must not collide"

