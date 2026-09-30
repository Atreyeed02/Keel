"""
The write rate limit (app/ratelimit.py): writes past the limit are a 429
with Retry-After, form and API alike; reads are not limited; a refused write
never reaches the app.

No database needed. The writes that count are ones the app answers before
opening a connection (a posting with no Idempotency-Key, an account form
with no name), and the clock is a fake one the tests move by hand.
Separate limits for separate clients behind a proxy are in
tests/test_proxy_headers.py.
"""

import json
import logging

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings, settings
from app.main import app, write_limiter
from app.observability import JsonFormatter, log
from app.ratelimit import RateLimiter, client_key

LIMIT = 3
WINDOW = 60


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    """The app's limiter at 3 writes a minute, on a clock the test controls."""
    fake = _Clock()
    monkeypatch.setattr(write_limiter, "clock", fake)
    monkeypatch.setattr(write_limiter, "limit", LIMIT)
    monkeypatch.setattr(write_limiter, "window", WINDOW)
    write_limiter.reset()
    return fake


class _NoDatabase:
    def begin(self):
        raise AssertionError("a refused write must not reach the database")

    connect = begin


@pytest.fixture
def no_database(monkeypatch):
    for target in ("app.main.engine", "app.api.accounts.engine", "app.api.transactions.engine"):
        monkeypatch.setattr(target, _NoDatabase())


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


def _client(address="198.51.100.20"):
    return AsyncClient(
        transport=ASGITransport(app=app, client=(address, 40000), raise_app_exceptions=False),
        base_url="http://test",
        follow_redirects=False,
    )


def _api_write(client):
    """A write the API refuses with a 400 before touching the database."""
    return client.post("/api/transactions", json={"entries": []})


def _form_write(client):
    """A write the account form refuses with a 422 before touching the database."""
    return client.post("/accounts", data={"name": "", "account_type": "asset", "currency": "USD"})


async def test_the_write_over_the_limit_is_a_429_with_retry_after(clock, no_database):
    async with _client() as client:
        allowed = []
        for _ in range(LIMIT):
            allowed.append((await _api_write(client)).status_code)
            clock.now += 10
        refused = await _api_write(client)

    assert allowed == [400] * LIMIT
    assert refused.status_code == 429
    # the first write was 30 seconds ago, so it leaves the window in 30
    assert refused.headers["retry-after"] == "30"
    assert refused.json() == {
        "error": {
            "code": "rate_limited",
            "message": "too many writes from this address: at most 3 every 60 seconds. "
            "Try again in 30 seconds.",
        }
    }


async def test_forms_and_the_api_share_one_allowance(clock, no_database):
    async with _client() as client:
        statuses = [
            (await _form_write(client)).status_code,
            (await _api_write(client)).status_code,
            (await _form_write(client)).status_code,
        ]
        refused = await _form_write(client)

    assert statuses == [422, 400, 422]
    assert refused.status_code == 429
    assert refused.headers["retry-after"] == "60"
    assert refused.headers["content-type"] == "text/plain; charset=utf-8"
    assert refused.text.startswith("too many writes from this address")


async def test_retry_after_is_honest(clock, no_database):
    """Waiting exactly Retry-After seconds is enough; a refused write did not count."""
    async with _client() as client:
        for _ in range(LIMIT):
            await _api_write(client)
            clock.now += 10
        first = await _api_write(client)
        clock.now += 1
        second = await _api_write(client)

        clock.now += int(second.headers["retry-after"])
        admitted = await _api_write(client)
        after = await _api_write(client)

    assert (first.status_code, first.headers["retry-after"]) == (429, "30")
    assert (second.status_code, second.headers["retry-after"]) == (429, "29")
    assert admitted.status_code == 400
    # the other two writes of the first burst are still in the window
    assert after.status_code == 429


async def test_a_refused_write_never_reaches_the_app(clock, no_database):
    async with _client() as client:
        for _ in range(LIMIT):
            await _api_write(client)
        # valid, so the app would open a connection if it saw it
        response = await client.post(
            "/api/accounts", json={"name": "Cash", "account_type": "asset", "currency": "USD"}
        )
    assert response.status_code == 429


async def test_reads_are_not_limited(clock, no_database):
    async with _client() as client:
        for _ in range(LIMIT):
            await _api_write(client)
        reads = [(await client.get("/accounts/new")).status_code for _ in range(50)]
        reads += [(await client.get("/openapi.json")).status_code for _ in range(50)]
        reads.append((await client.head("/openapi.json")).status_code)
        still_refused = await _api_write(client)

    assert set(reads) == {200}
    assert still_refused.status_code == 429


async def test_each_client_address_has_its_own_allowance(clock, no_database):
    async with _client("198.51.100.20") as one, _client("198.51.100.21") as other:
        for _ in range(LIMIT):
            await _api_write(one)
        assert (await _api_write(one)).status_code == 429
        assert (await _api_write(other)).status_code == 400


async def test_an_ipv6_client_is_counted_per_64(clock, no_database):
    async with (
        _client("2001:db8:1:2::1") as first,
        _client("2001:db8:1:2:ffff::9") as same_64,
        _client("2001:db8:1:3::1") as next_64,
    ):
        for _ in range(LIMIT):
            await _api_write(first)
        assert (await _api_write(same_64)).status_code == 429
        assert (await _api_write(next_64)).status_code == 400


@pytest.mark.parametrize(
    ("host", "key"),
    [
        ("203.0.113.7", "203.0.113.7"),
        ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64"),
        ("::ffff:203.0.113.7", "203.0.113.7"),  # an IPv4 client on a dual-stack socket
        (None, "unknown"),
        ("unix-socket", "unix-socket"),
    ],
)
def test_client_key(host, key):
    assert client_key(host) == key


async def test_a_429_is_logged_and_carries_the_security_headers(clock, no_database, captured):
    async with _client() as client:
        for _ in range(LIMIT):
            await _api_write(client)
        refused = await _api_write(client)

    assert refused.status_code == 429
    assert refused.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in refused.headers
    assert "x-request-id" in refused.headers
    (line,) = (
        entry
        for entry in captured
        if entry["event"] == "request.completed" and entry["status"] == 429
    )
    assert (line["method"], line["path"], line["client"]) == (
        "POST",
        "/api/transactions",
        "198.51.100.20",
    )


async def test_a_limit_of_zero_turns_it_off(clock, no_database, monkeypatch):
    monkeypatch.setattr(write_limiter, "limit", 0)
    async with _client() as client:
        statuses = {(await _api_write(client)).status_code for _ in range(LIMIT * 10)}
    assert statuses == {400}


def test_the_app_is_limited_by_the_settings():
    defaults = Settings(_env_file=None)
    assert (defaults.write_rate_limit, defaults.write_rate_window_seconds) == (30, 60)
    assert write_limiter.limit == settings.write_rate_limit
    assert write_limiter.window == settings.write_rate_window_seconds


def test_idle_clients_are_forgotten():
    """Memory holds only the clients seen in the last window."""
    fake = _Clock()
    limiter = RateLimiter(limit=2, window=60, clock=fake)
    for n in range(100):
        limiter.hit(f"198.51.100.{n}")
    fake.now += 61
    limiter.hit("203.0.113.1")
    assert set(limiter._hits) == {"203.0.113.1"}
