"""
The JSON API (app/api/): every status code it answers with, the error
shape, string amounts, replay semantics and concurrent duplicates.

The first group needs no database. Every rejection in it happens before a
connection is opened, and the fixture proves that by replacing the engine
with one that fails the test if it is touched. The rest are Postgres-backed
and skip without TEST_DATABASE_URL, like the other integration tests.
"""

import asyncio
import json
import logging
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.schema import events, idempotency_keys, ledger_entries, transactions
from app.domain.idempotency import entries_fingerprint, request_fingerprint
from app.domain.ledger import EntryInput
from app.main import app
from app.observability import JsonFormatter, log
from tests.support import reset_schema

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

ENGINE_USERS = ("app.main.engine", "app.api.accounts.engine", "app.api.transactions.engine")


def _client():
    # raise_app_exceptions=False: an unhandled error must surface as the 500
    # a real client would see, so a test can assert it did not happen.
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        follow_redirects=False,
    )


def _error(response) -> tuple[str, str]:
    """The (code, message) of an error response, asserting the shared shape."""
    body = response.json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) == {"code", "message"}, body
    return body["error"]["code"], body["error"]["message"]


def _entries(cash_id, revenue_id, amount="100.00"):
    return [
        {"account_id": str(cash_id), "entry_type": "debit", "amount": amount, "currency": "USD"},
        {
            "account_id": str(revenue_id),
            "entry_type": "credit",
            "amount": amount,
            "currency": "USD",
        },
    ]


def _post(client, body, key="key-1", **kwargs):
    headers = {} if key is None else {"Idempotency-Key": key}
    return client.post("/api/transactions", json=body, headers=headers, **kwargs)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.setFormatter(JsonFormatter())
        self.lines: list[dict] = []

    def emit(self, record):
        self.lines.append(json.loads(self.format(record)))


@pytest.fixture
def captured():
    handler = _Capture()
    log.addHandler(handler)
    previous = log.level
    log.setLevel(logging.INFO)
    yield handler.lines
    log.removeHandler(handler)
    log.setLevel(previous)


def _events(lines, name):
    return [line for line in lines if line["event"] == name]


# --- no database: everything refused before a connection is opened ---------


class _NoDatabase:
    def begin(self):
        raise AssertionError("this request should have been refused before touching the database")

    connect = begin


@pytest.fixture
def no_database(monkeypatch):
    for target in ENGINE_USERS:
        monkeypatch.setattr(target, _NoDatabase())


VALID = {"description": "Invoice 7", "entries": _entries(uuid.uuid4(), uuid.uuid4())}


async def test_a_missing_idempotency_key_is_a_400(no_database):
    async with _client() as client:
        response = await _post(client, VALID, key=None)
    assert response.status_code == 400
    assert _error(response) == ("missing_idempotency_key", "the Idempotency-Key header is required")


@pytest.mark.parametrize("key", ["", "has a space", "x" * 256], ids=["empty", "space", "too-long"])
async def test_a_malformed_idempotency_key_is_a_400(no_database, key):
    async with _client() as client:
        response = await _post(client, VALID, key=key)
    assert response.status_code == 400
    assert _error(response)[0] == "invalid_idempotency_key"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "request body is required"),
        (b"{not json", "request body is not valid JSON"),
        (b'["a", "list"]', "request body must be a JSON object"),
    ],
    ids=["empty", "malformed", "not-an-object"],
)
async def test_an_unreadable_body_is_a_422(no_database, content, message):
    async with _client() as client:
        for path, headers in (
            ("/api/transactions", {"Idempotency-Key": "k"}),
            ("/api/accounts", {}),
        ):
            response = await client.post(
                path, content=content, headers={"Content-Type": "application/json", **headers}
            )
            assert response.status_code == 422
            assert _error(response) == ("validation_error", message)


@pytest.mark.parametrize(
    ("change", "code", "fragment"),
    [
        # a JSON number has probably been through a binary float already
        (lambda b: b["entries"][0].update(amount=100.0), "validation_error", "must be a string"),
        (
            lambda b: [e.update(amount="100.005") for e in b["entries"]],
            "validation_error",
            "entries.0.amount decimal input should have no more than 2 decimal places",
        ),
        (
            lambda b: b["entries"][1].update(amount="99.99"),
            "unbalanced_transaction",
            "does not balance",
        ),
        (lambda b: b["entries"].pop(), "validation_error", "at least two entries"),
        (lambda b: b.update(description="x" * 513), "validation_error", "at most 512"),
        (lambda b: b.update(memo="?"), "validation_error", "memo extra inputs are not permitted"),
        (
            lambda b: b["entries"][0].update(entry_type="sideways"),
            "validation_error",
            "entries.0.entry_type entry_type must be 'debit' or 'credit'",
        ),
        (
            lambda b: b["entries"][0].update(account_id="not-a-uuid"),
            "validation_error",
            "entries.0.account_id",
        ),
    ],
    ids=[
        "number-amount",
        "three-decimals",
        "unbalanced",
        "one-entry",
        "long-description",
        "unknown-field",
        "bad-entry-type",
        "bad-account-id",
    ],
)
async def test_an_invalid_transaction_is_a_422_with_a_readable_message(
    no_database, change, code, fragment
):
    body = json.loads(json.dumps(VALID))
    change(body)
    async with _client() as client:
        response = await _post(client, body)
    assert response.status_code == 422
    got_code, message = _error(response)
    assert got_code == code
    assert fragment in message
    # describe_validation_error's flattening, not pydantic's multi-line dump
    assert "validation error for" not in message and "errors.pydantic.dev" not in message


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        (
            {"name": "Cash", "account_type": "cash", "currency": "USD"},
            "account_type must be one of",
        ),
        ({"name": "Cash", "account_type": "asset", "currency": "DOLLARS"}, "3-letter code"),
        ({"name": "Cash", "account_type": "asset"}, "currency field required"),
        ({"name": "Cash", "account_type": "asset", "currency": "USD", "x": 1}, "unexpected field"),
    ],
    ids=["bad-type", "bad-currency", "missing-field", "unknown-field"],
)
async def test_an_invalid_account_is_a_422(no_database, body, fragment):
    async with _client() as client:
        response = await client.post("/api/accounts", json=body)
    assert response.status_code == 422
    code, message = _error(response)
    assert code == "validation_error"
    assert fragment in message


async def test_unknown_api_routes_and_methods_use_the_error_shape(no_database):
    async with _client() as client:
        missing = await client.get("/api/nothing-here")
        wrong_method = await client.delete("/api/accounts")
    assert missing.status_code == 404
    assert _error(missing)[0] == "not_found"
    assert wrong_method.status_code == 405
    assert _error(wrong_method)[0] == "method_not_allowed"


async def test_html_routes_keep_fastapis_own_error_shape(no_database):
    """The /api/ handlers are scoped: a bad query on a page is still FastAPI's default 422."""
    async with _client() as client:
        response = await client.get("/transactions", params={"page": 0})
    assert response.status_code == 422
    assert "detail" in response.json() and "error" not in response.json()


def test_the_json_fingerprint_hashes_meaning_not_bytes():
    cash, revenue = uuid.uuid4(), uuid.uuid4()

    def fingerprint(description, rows):
        return entries_fingerprint(description, [EntryInput.model_validate(r) for r in rows])

    base = fingerprint("Invoice 7", _entries(cash, revenue, "100.00"))
    # same transaction, written differently: another amount spelling, a
    # lower-case currency, an upper-case UUID, entries in another order
    reworded = _entries(cash, revenue, "100")
    reworded[0].update(currency="usd", account_id=str(cash).upper())
    assert fingerprint("Invoice 7", list(reversed(reworded))) == base
    # anything that would store something different is a different request
    assert fingerprint("Invoice 7", _entries(cash, revenue, "100.01")) != base
    assert fingerprint("Invoice 8", _entries(cash, revenue, "100.00")) != base
    assert fingerprint("Invoice 7", _entries(revenue, cash, "100.00")) != base
    # and never equal to the form's fingerprint of the same data
    assert request_fingerprint("Invoice 7", _entries(cash, revenue)) != base


# --- Postgres-backed ------------------------------------------------------------


@pytest.fixture
async def api(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL page integration tests")
    test_engine = create_async_engine(TEST_DATABASE_URL)
    for target in ENGINE_USERS:
        monkeypatch.setattr(target, test_engine)
    async with test_engine.begin() as conn:
        await reset_schema(conn)
    yield test_engine
    async with test_engine.begin() as conn:
        from app.db.schema import metadata

        await conn.run_sync(metadata.drop_all)
    await test_engine.dispose()


async def _create_accounts(client, *specs):
    ids = []
    for name, account_type, currency in specs:
        response = await client.post(
            "/api/accounts", json={"name": name, "account_type": account_type, "currency": currency}
        )
        assert response.status_code == 201, response.text
        ids.append(uuid.UUID(response.json()["id"]))
    return ids


async def _cash_and_revenue(client):
    return await _create_accounts(client, ("Cash", "asset", "USD"), ("Revenue", "revenue", "USD"))


async def _counts(engine) -> dict[str, int]:
    async with engine.connect() as conn:
        return {
            t.name: await conn.scalar(select(func.count()).select_from(t))
            for t in (transactions, ledger_entries, events, idempotency_keys)
        }


async def test_creating_an_account_is_a_201_with_the_account(api, captured):
    async with _client() as client:
        response = await client.post(
            "/api/accounts",
            json={"name": " Petty cash ", "account_type": "asset", "currency": "usd"},
        )
    assert response.status_code == 201
    body = response.json()
    assert body == {
        "id": body["id"],
        "name": "Petty cash",
        "account_type": "asset",
        "currency": "USD",
        "normal_side": "debit",
        "debits": "0.00",
        "credits": "0.00",
        "balance": "0.00",
        "created_at": body["created_at"],
    }
    # through create_account_record, so the event log has it too
    async with api.connect() as conn:
        event = (await conn.execute(select(events))).mappings().one()
    assert (event["event_type"], str(event["aggregate_id"])) == ("account.created", body["id"])
    (line,) = _events(captured, "account.created")
    assert line["account_id"] == body["id"]


async def test_listing_accounts_signs_balances_by_normal_side_as_strings(api):
    async with _client() as client:
        cash, revenue = await _cash_and_revenue(client)
        assert (
            await _post(client, {"entries": _entries(cash, revenue, "250.10")})
        ).status_code == 201
        response = await client.get("/api/accounts")
    assert response.status_code == 200
    by_name = {a["name"]: a for a in response.json()["accounts"]}
    # asset: debit-normal, so a debit is a positive balance
    assert by_name["Cash"] | {"id": None, "created_at": None} == {
        "id": None,
        "name": "Cash",
        "account_type": "asset",
        "currency": "USD",
        "normal_side": "debit",
        "debits": "250.10",
        "credits": "0.00",
        "balance": "250.10",
        "created_at": None,
    }
    # revenue: credit-normal, so a credit is positive too, as on the overview page
    assert (by_name["Revenue"]["normal_side"], by_name["Revenue"]["balance"]) == (
        "credit",
        "250.10",
    )
    # ordered like the overview: by account type, then name
    assert [a["name"] for a in response.json()["accounts"]] == ["Cash", "Revenue"]


async def test_a_first_post_is_a_201_with_the_transaction(api, captured):
    async with _client() as client:
        cash, revenue = await _cash_and_revenue(client)
        response = await _post(
            client, {"description": "Invoice 7", "entries": _entries(cash, revenue)}
        )
    assert response.status_code == 201
    assert response.headers["Idempotent-Replayed"] == "false"
    body = response.json()
    assert response.headers["Location"] == f"/api/transactions/{body['id']}"
    assert body["description"] == "Invoice 7"
    assert sorted(
        (e["account_name"], e["entry_type"], e["amount"], e["currency"]) for e in body["entries"]
    ) == [("Cash", "debit", "100.00", "USD"), ("Revenue", "credit", "100.00", "USD")]
    assert all(isinstance(e["amount"], str) for e in body["entries"])
    assert await _counts(api) == {
        "transactions": 1,
        "ledger_entries": 2,
        "events": 3,  # two account.created, one transaction.posted
        "idempotency_keys": 1,
    }
    (line,) = _events(captured, "transaction.posted")
    assert (line["transaction_id"], line["idempotency_key"]) == (body["id"], "key-1")
    assert line["account_ids"] == sorted([str(cash), str(revenue)])


async def test_a_retry_is_a_200_replay_of_the_same_transaction(api, captured):
    async with _client() as client:
        cash, revenue = await _cash_and_revenue(client)
        request = {"description": "Invoice 7", "entries": _entries(cash, revenue)}
        first = await _post(client, request)
        retry = await _post(client, request)
    assert (first.status_code, retry.status_code) == (201, 200)
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.json() == first.json()
    assert (await _counts(api))["transactions"] == 1
    assert len(_events(captured, "transaction.replayed")) == 1


async def test_a_retry_written_differently_is_still_a_replay(api):
    """The fingerprint compares meaning: order, spelling of amounts, case."""
    async with _client() as client:
        cash, revenue = await _cash_and_revenue(client)
        first = await _post(
            client, {"description": "Invoice 7", "entries": _entries(cash, revenue)}
        )
        reworded = _entries(cash, revenue, "100")
        reworded[1]["currency"] = "usd"
        retry = await client.post(
            "/api/transactions",
            content=json.dumps(
                {"entries": list(reversed(reworded)), "description": "Invoice 7"}, indent=4
            ),
            headers={"Idempotency-Key": "key-1", "Content-Type": "application/json"},
        )
    assert retry.status_code == 200
    assert retry.json()["id"] == first.json()["id"]
    assert (await _counts(api))["transactions"] == 1


async def test_reusing_a_key_for_a_different_request_is_a_409(api, captured):
    async with _client() as client:
        cash, revenue = await _cash_and_revenue(client)
        first = await _post(client, {"entries": _entries(cash, revenue, "100.00")})
        reused = await _post(client, {"entries": _entries(cash, revenue, "50.00")})
    assert first.status_code == 201
    assert reused.status_code == 409
    assert _error(reused)[0] == "idempotency_conflict"
    assert (await _counts(api))["transactions"] == 1
    assert len(_events(captured, "idempotency.conflict")) == 1


async def test_a_form_key_sent_to_the_api_is_a_409_not_a_replay(api):
    async with _client() as client:
        cash, revenue = await _cash_and_revenue(client)
        form = await client.post(
            "/post-transaction",
            data={
                "description": "Invoice 7",
                "submission_key": "shared-key",
                "account_id": [str(cash), str(revenue)],
                "entry_type": ["debit", "credit"],
                "amount": ["100.00", "100.00"],
                "currency": ["USD", "USD"],
            },
        )
        api_post = await _post(
            client,
            {"description": "Invoice 7", "entries": _entries(cash, revenue)},
            key="shared-key",
        )
    assert form.status_code == 302
    assert api_post.status_code == 409


async def test_entries_naming_bad_accounts_are_a_422_and_release_the_key(api, captured):
    async with _client() as client:
        cash, revenue = await _cash_and_revenue(client)
        (eur,) = await _create_accounts(client, ("EUR bank", "asset", "EUR"))
        missing = uuid.uuid4()
        bad = _entries(missing, revenue)
        bad.append(
            {"account_id": str(eur), "entry_type": "debit", "amount": "5.00", "currency": "USD"}
        )
        bad.append(
            {"account_id": str(cash), "entry_type": "credit", "amount": "5.00", "currency": "USD"}
        )
        rejected = await _post(client, {"entries": bad})
        fixed = await _post(client, {"entries": _entries(cash, revenue)})
    assert rejected.status_code == 422
    code, message = _error(rejected)
    assert code == "invalid_accounts"
    # both problems in one answer, as on the form
    assert f"no account exists with id: {missing}" in message
    assert "account 'EUR bank' is EUR" in message
    # the rejected attempt rolled back its claim, so the same key posts once fixed
    assert fixed.status_code == 201
    assert len(_events(captured, "transaction.rejected")) == 1


async def test_concurrent_duplicates_post_once_and_replay_to_the_rest(api, monkeypatch):
    """
    Five identical requests are held at a barrier just before
    post_transaction_once and released together, so they race for the key.
    """
    import app.api.transactions as transactions_api

    parties = 5
    barrier = asyncio.Barrier(parties)
    real = transactions_api.post_transaction_once

    async def gated(*args, **kwargs):
        await barrier.wait()
        return await real(*args, **kwargs)

    async with _client() as client:
        cash, revenue = await _cash_and_revenue(client)
        monkeypatch.setattr("app.api.transactions.post_transaction_once", gated)
        request = {"description": "Double-clicked", "entries": _entries(cash, revenue)}
        responses = await asyncio.gather(*(_post(client, request) for _ in range(parties)))

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 200, 200, 200, 201], [r.text for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1
    replayed = sorted(r.headers["Idempotent-Replayed"] for r in responses)
    assert replayed == ["false", "true", "true", "true", "true"]
    assert await _counts(api) == {
        "transactions": 1,
        "ledger_entries": 2,
        "events": 3,
        "idempotency_keys": 1,
    }


async def test_concurrent_conflicting_requests_post_one_and_reject_the_other(api, monkeypatch):
    import app.api.transactions as transactions_api

    barrier = asyncio.Barrier(2)
    real = transactions_api.post_transaction_once

    async def gated(*args, **kwargs):
        await barrier.wait()
        return await real(*args, **kwargs)

    async with _client() as client:
        cash, revenue = await _cash_and_revenue(client)
        monkeypatch.setattr("app.api.transactions.post_transaction_once", gated)
        responses = await asyncio.gather(
            _post(client, {"entries": _entries(cash, revenue, "100.00")}),
            _post(client, {"entries": _entries(cash, revenue, "70.00")}),
        )
    assert sorted(r.status_code for r in responses) == [201, 409]
    assert (await _counts(api))["transactions"] == 1


async def test_reading_a_transaction_back(api):
    async with _client() as client:
        cash, revenue = await _cash_and_revenue(client)
        posted = await _post(
            client, {"description": "Invoice 7", "entries": _entries(cash, revenue)}
        )
        found = await client.get(posted.headers["Location"])
        missing = await client.get(f"/api/transactions/{uuid.uuid4()}")
        not_a_uuid = await client.get("/api/transactions/abc")
    assert found.status_code == 200
    assert found.json() == posted.json()
    for response in (missing, not_a_uuid):
        assert response.status_code == 404
        assert _error(response)[0] == "not_found"
