# Keel — Architecture and Concepts

A complete walkthrough of what this service is, the accounting and
event-sourcing ideas it is built on, what every file does, and what is
still missing.

Written against commit `7a4bb37`.

---

## Table of contents

1. [What this system is](#1-what-this-system-is)
2. [The accounting concepts](#2-the-accounting-concepts)
3. [The event-sourcing concepts](#3-the-event-sourcing-concepts)
4. [Idempotency](#4-idempotency)
5. [The code, layer by layer](#5-the-code-layer-by-layer)
6. [Two request walkthroughs](#6-two-request-walkthroughs)
7. [What has been done](#7-what-has-been-done)
8. [What still needs doing](#8-what-still-needs-doing)

---

## 1. What this system is

Keel is a **double-entry, event-sourced ledger**: the accounting core you
would put behind a payments platform. It answers one question reliably —
*where is all the money, and how did it get there?* — and refuses to let
that answer become unverifiable.

Two design commitments drive everything else:

**Money is conserved.** Every transaction is a set of debit and credit
entries that must net to zero. There is no way to write a transaction
that creates or destroys value, because the code rejects it before the
database is touched.

**History is immutable.** Nothing is ever updated in place. Every change
is appended to an `events` log. The tables you query for balances are a
*derived projection* of that log, not the truth itself.

The contrast this is built against: a naive ledger stores a `balance`
column on each account and does `UPDATE balance = balance + 100`. That
works until a bug, a race, or a replayed webhook corrupts it — and then
there is no way to reconstruct how the balance became wrong, because the
history was never kept.

---

## 2. The accounting concepts

### 2.1 Double-entry bookkeeping

Every financial event touches **at least two accounts**. Money never just
"appears in" an account — it always comes *from* somewhere.

Buying a $2,400 office rental with cash is not one fact ("cash went
down"). It is two halves of one fact:

| Account     | Side   | Amount   |
|-------------|--------|----------|
| Office rent | debit  | 2,400.00 |
| Cash        | credit | 2,400.00 |

The rent expense went **up** by 2,400 and cash went **down** by 2,400.
Written together, they are self-checking: if the two sides do not match,
something was mis-recorded.

In this codebase a transaction is the pair of:

- one `transactions` row — the description and timestamp,
- two or more `ledger_entries` rows — the individual debits and credits.

### 2.2 Debits, credits, and "normal side"

This is the part that confuses everyone, because *debit* and *credit* do
not mean "decrease" and "increase". They are just the **left and right
columns** of the ledger. What they do to a balance depends on the account
type.

| Account type | Normal side | A debit… | A credit… |
|--------------|-------------|----------|-----------|
| Asset        | debit       | increases | decreases |
| Expense      | debit       | increases | decreases |
| Liability    | credit      | decreases | increases |
| Equity       | credit      | decreases | increases |
| Revenue      | credit      | decreases | increases |

An account's **normal side** is the side that makes its balance go up.
Cash (an asset) is debit-normal: you debit cash to add money to it.
Revenue is credit-normal: you credit revenue when you earn.

Why the split? Because of the **accounting equation**:

```
Assets  =  Liabilities  +  Equity
```

Expanded to include operations:

```
Assets + Expenses  =  Liabilities + Equity + Revenue
     ↑ debit-normal          ↑ credit-normal
```

Everything on the left of that equation is debit-normal; everything on
the right is credit-normal. Debits and credits balancing is exactly the
same statement as the equation staying true.

**In the code**, this lives in two places:

`app/main.py` declares which types are debit-normal:

```python
NORMAL_DEBIT_TYPES = {"asset", "expense"}
```

and the overview handler uses it to flip the sign so every account
displays a positive balance when it is in its expected state:

```python
data["normal_side"] = "debit" if row["account_type"] in NORMAL_DEBIT_TYPES else "credit"
data["balance"] = row["raw_balance"] if data["normal_side"] == "debit" else -row["raw_balance"]
```

`raw_balance` is always `debits − credits`. For a credit-normal account
that value is naturally negative when healthy, so it is negated for
display. This is why `account_type` must be one of the five known values
— an unrecognised type would silently fall into the credit-normal branch
and render a **wrong balance sign** rather than raising an error. That is
the real reason `ACCOUNT_TYPES` is a closed set rather than a free string.

### 2.3 The balance invariant — enforced *per currency*

The naive invariant is "debits equal credits". Keel enforces something
stricter: **debits equal credits within each currency, independently.**

`app/domain/ledger.py`:

```python
def assert_balanced(entries: list[EntryInput]) -> None:
    net: dict[str, Decimal] = defaultdict(Decimal)
    for e in entries:
        net[e.currency] += e.amount if e.entry_type == "debit" else -e.amount

    unbalanced = {ccy: total for ccy, total in net.items() if total != 0}
    if unbalanced:
        raise UnbalancedTransactionError(...)
```

A single dictionary keyed by currency code. Every currency present must
net to exactly zero.

This matters because a naive global sum can be fooled. Consider:

| Account | Side   | Amount | Currency |
|---------|--------|--------|----------|
| Cash    | debit  | 100.00 | USD      |
| Revenue | credit | 100.00 | EUR      |

A single total says `100 − 100 = 0`, balanced. It is not — you have
invented 100 USD and destroyed 100 EUR. Per-currency netting catches it:
USD nets to `+100`, EUR to `−100`, both non-zero, rejected.

A transaction *may* legitimately span currencies, as long as each side
balances within itself. `tests/test_ledger_domain.py` covers exactly this
with a four-entry, two-currency transaction that passes, and a variant
where one currency is off by 1.00 that fails.

### 2.4 What this model deliberately cannot express

Because each currency must net to zero on its own, **a foreign-exchange
trade cannot be a single transaction here.** Converting 1,000 EUR into
1,100 USD would leave EUR at −1,000 and USD at +1,100; both non-zero, so
it is rejected.

This is correct behaviour, not a bug — an FX conversion is not a
value-neutral movement, it involves a rate and usually a gain or loss.
The standard modelling is a **currency-exchange clearing account** and two
transactions, each balanced in its own currency, with any difference
posted to an FX gain/loss account. Nothing in the codebase implements
that yet; see §8.

---

## 3. The event-sourcing concepts

### 3.1 Event log vs. read model

The database holds two logically distinct things:

```
events                      ← append-only source of truth
  ├─ id, aggregate_type, aggregate_id
  ├─ event_type ("transaction.posted")
  └─ payload (JSONB — the full transaction as submitted)

transactions                ← derived read model
ledger_entries              ← derived read model
accounts                    ← reference data
```

The **event log** records *what happened*, as it happened, forever. It is
never updated and never deleted. `app/db/schema.py` states the rule
explicitly:

```python
# Append-only: no updated_at, no soft-delete flag. If it's wrong,
# a compensating event gets appended, not a mutation.
```

The **read model** (`transactions` + `ledger_entries`) is shaped for
fast queries — balances, transaction detail, account listings. It is
*derived* information: in principle it could be deleted entirely and
rebuilt by replaying every event in order.

### 3.2 Why both are written in one transaction

`post_transaction()` writes the event **and** the read-model rows inside
the caller's database transaction:

```python
await conn.execute(insert(transactions).values(id=txn_id, description=description))
await conn.execute(insert(ledger_entries), [...])
await conn.execute(insert(events).values(
    aggregate_type="transaction",
    aggregate_id=txn_id,
    event_type="transaction.posted",
    payload={"description": description, "entries": [...]},
))
```

They land together or not at all. If they could diverge — event written,
entries lost — the "rebuildable" property would be a lie. Note the
function **does not commit**; its docstring explains why:

> Caller owns the connection's transaction boundary — this function
> issues statements but doesn't commit, so it composes with an
> idempotency check wrapping it in the same DB transaction.

That is a deliberate composability choice. The route handler opens
`engine.begin()`, does the idempotency lookup, calls `post_transaction`,
writes the idempotency record, and only then commits — all atomic.

### 3.3 Rebuildability: the aspiration vs. the current reality

The README says the read model is "always rebuildable from events". The
*data* supports that — the event payload contains every entry with
account id, side, amount and currency.

**But no replay code exists.** There is no function that reads the
`events` table and reconstructs `transactions` / `ledger_entries`. The
property is currently a claim about the data layout, not a tested
capability. This is listed in §8.

---

## 4. Idempotency

The problem: a user submits a payment, the network drops the response,
the client retries. Without protection, the transaction posts twice and
someone is charged twice.

The solution here is a **client-supplied key** stored with a hash of the
request:

`idempotency_keys` columns:

| Column            | Purpose |
|-------------------|---------|
| `key`             | client-supplied identifier (primary key) |
| `request_hash`    | SHA-256 of the request body |
| `response_body`   | what was returned the first time (JSONB) |
| `response_status` | the status code returned |

The logic in `app/main.py`:

```python
request_hash = hashlib.sha256(
    json.dumps({"description": description, "entries": raw_entries}, sort_keys=True).encode()
).hexdigest()
```

then, inside the transaction:

- **Key not seen** → post the transaction, store key + hash + result.
- **Key seen, hash matches** → a genuine retry. Return the *original*
  transaction id without posting again.
- **Key seen, hash differs** → the same key was reused for a *different*
  request. Raise `409 Conflict` — this is a client bug and silently
  accepting it would hide it.

`sort_keys=True` matters: it makes the JSON serialisation deterministic,
so the same logical request always hashes identically.

The form supplies the key via a hidden `submission_key` field generated
when the page is rendered, so a double-submit (double-click, browser
refresh) carries the same key and collapses into one posting. This is
covered by `test_idempotent_retry_returns_same_transaction_id`.

**Scope note:** idempotency applies only to `POST /post-transaction`. It
deliberately does *not* apply to account creation — it exists to protect
the double-entry invariant against double-posting, and creating a
duplicate account is neither a money movement nor a conservation
violation.

---

## 5. The code, layer by layer

```
app/
├── api/health.py      HTTP: liveness + DB connectivity
├── db/schema.py       SQLAlchemy Core table definitions
├── db/engine.py       async engine, connection helpers
├── domain/ledger.py   double-entry invariant + posting
├── domain/accounts.py account input validation
├── templates/         Jinja2 server-rendered pages
├── config.py          settings via pydantic-settings
└── main.py            app wiring + all page routes
alembic/               schema migrations
scripts/               dev utilities (demo seed)
tests/
```

### 5.1 `app/config.py`

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_name: str = "ledger-service"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://ledger:ledger@db:5432/ledger"
    db_pool_size: int = 5
    db_max_overflow: int = 10
```

`pydantic-settings` reads each field from an environment variable of the
same name (case-insensitive), falling back to `.env`, then to the default.
So `DATABASE_URL` in the environment overrides everything.

The default host is `db` — the service name in `docker-compose.yml`, not
`localhost`. That is why the container works with no configuration, and
why running on the host needs `DATABASE_URL` pointed at `localhost`.

`extra="ignore"` means unknown keys in `.env` are not an error.

### 5.2 `app/db/schema.py` — the tables

SQLAlchemy **Core**, not the ORM. Tables are described as data
(`Table(...)`) and queries are built explicitly. The rationale in the
file:

> This keeps the double-entry invariants enforced in code we control,
> not hidden behind ORM flush semantics.

With an ORM, *when* a write happens depends on flush ordering and session
state. For a ledger, that indirection is a liability.

**`events`** — the append-only log.

| Column | Type | Note |
|---|---|---|
| `id` | UUID PK | |
| `sequence` | bigint identity | the log's total order — see below |
| `aggregate_type` | String(64) | e.g. `"transaction"` |
| `aggregate_id` | UUID | which entity this event concerns |
| `event_type` | String(128) | e.g. `"transaction.posted"` |
| `payload` | JSONB | the full event data |
| `created_at` | timestamptz | server default `now()` |

*Aggregate* is event-sourcing vocabulary for the entity an event belongs
to. `aggregate_type="transaction"`, `aggregate_id=<txn id>` means "this
event happened to that transaction". It allows one log to carry events
for many entity kinds.

> **`sequence` is what orders the log.** A `BigInteger` declared
> `Identity(always=True)` — Postgres `GENERATED ALWAYS AS IDENTITY` — so
> the database assigns it monotonically on each insert and the
> application cannot supply or overwrite it. `/event-log` sorts by it.
>
> It did not always work: `sequence` was originally a `timestamptz` with
> the same `server_default=func.now()` as `created_at`, which ordered
> nothing. Postgres's `CURRENT_TIMESTAMP` returns **transaction start
> time**, so all 10 events from one seed run shared a single value and
> their displayed order came down to a random-UUID tiebreak. Migration
> `b7855ff9a6aa` replaced the column with the identity version.
>
> `created_at` stays as the human-facing timestamp: it is what the event
> log *displays*, while `sequence` is what it *sorts by*.

**`accounts`** — reference data.

| Column | Type | Note |
|---|---|---|
| `id` | UUID PK | server-generated |
| `name` | String(255) | |
| `account_type` | String(32) | asset/liability/equity/revenue/expense |
| `currency` | String(3) | ISO 4217 code |
| `created_at` | timestamptz | |

An account's `currency` is fixed at creation and never updated, which
makes it the authority on what that account holds: `post_transaction()`
rejects any entry whose currency differs from it, so an account cannot
accumulate two currencies and the overview's per-account balance is a
single meaningful figure by construction. This is enforced in the domain
layer, like the balance invariant, not by a database constraint.

`account_type` carries a database `CHECK` (`ck_account_type_valid`,
added by migration `de4f1aec2fe6`) restricting it to the five types, so
a write that bypasses `app/domain/accounts.py` is rejected by Postgres
rather than silently mis-signing a balance. `app/db/schema.py` generates
the constraint from that module's `ACCOUNT_TYPES` tuple, so the two
cannot drift.

**`transactions`** — id, optional description, created_at. Deliberately
thin; all the money lives in the entries.

**`ledger_entries`** — the actual debits and credits.

| Column | Type | Note |
|---|---|---|
| `id` | UUID PK | |
| `transaction_id` | UUID FK → transactions | |
| `account_id` | UUID FK → accounts | |
| `entry_type` | String(6) | `CHECK IN ('debit','credit')` |
| `amount` | Numeric(18,2) | `CHECK amount > 0` |
| `currency` | String(3) | |

Two details worth understanding:

- **`Numeric(18,2)`, never float.** Binary floating point cannot
  represent 0.10 exactly; money arithmetic in floats accumulates error.
  `NUMERIC` is exact decimal. It maps to Python `Decimal`, which is why
  the domain layer uses `Decimal` throughout.
- **`amount > 0` always.** Direction is carried by `entry_type`, not by
  the sign of the number. There are no negative amounts — a reduction is
  a credit, not a negative debit. This removes a whole class of
  sign-confusion bugs.

The file notes what is *not* enforced here:

> Double-entry balance (sum(debits) == sum(credits) per transaction)
> is enforced in app/domain/ledger.py at write time, not here — a
> DB-level trigger is a reasonable v2 hardening step.

**`idempotency_keys`** — described in §4.

### 5.3 `app/db/engine.py`

```python
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    echo=False,
)
```

- **Connection pool**: `pool_size=5` connections kept open, up to
  `max_overflow=10` more under load. Opening a Postgres connection is
  expensive; pooling amortises it.
- **`pool_pre_ping=True`**: before handing out a pooled connection, send
  a cheap liveness check. Prevents handing out connections the database
  has already closed (idle timeouts, restarts).

```python
async def ping() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
```

Returns a boolean rather than raising — the health endpoint must always
answer. **This is the behaviour that made the CI smoke test interesting**
(§5.13).

**`connect()` vs `begin()` — the single most important async-SQLAlchemy
distinction in this codebase:**

| | Opens transaction | Commits on exit | Use for |
|---|---|---|---|
| `engine.connect()` | no | no | reads |
| `engine.begin()` | yes | **yes** | writes |

Every read handler uses `connect()`. Every write path uses `begin()`, so
the whole unit of work commits atomically at the end of the block, or
rolls back if an exception escapes.

### 5.4 `app/domain/ledger.py` — the heart

**`EntryInput`** — a Pydantic model validating one entry:

```python
class EntryInput(BaseModel):
    account_id: uuid.UUID
    entry_type: str        # "debit" | "credit"
    amount: Decimal
    currency: str
```

with `@field_validator`s rejecting `entry_type` outside
`("debit", "credit")` and any `amount <= 0`. Pydantic also coerces types:
a form string `"100.00"` becomes a `Decimal`, and a UUID string becomes a
`uuid.UUID`, failing loudly if it cannot.

**`UnbalancedTransactionError(ValueError)`** — a named domain exception.
Subclassing `ValueError` means generic handlers still catch it, while
code that cares can catch this specific case.

**`assert_balanced()`** — §2.3.

**`post_transaction(conn, entries, description)`** — the write path:

1. Reject fewer than two entries.
2. `assert_balanced(entries)`.
3. Generate `txn_id = uuid.uuid4()` server-side.
4. Insert the `transactions` row.
5. Bulk-insert all `ledger_entries` rows in one statement.
6. Append the `events` row with the full payload.
7. Return the transaction id.

Validation happens **before** any write, so a rejected transaction never
touches the database.

### 5.5 `app/domain/accounts.py`

Mirrors the shape of `ledger.py` — a Pydantic model plus a validation
entry point, kept out of the route handler.

```python
ACCOUNT_TYPES = ("asset", "liability", "equity", "revenue", "expense")
```

An **ordered tuple**, not a set, because the `<select>` in the form
renders from it and a set has no stable order.

`AccountInput` normalises as it validates: `name` is stripped and capped
at 255 (matching the column width, so over-long input is a 422 rather
than a database error), `currency` is stripped and upper-cased and must
be three letters.

`InvalidAccountError(ValueError)` mirrors `UnbalancedTransactionError`.
`validate_account()` catches Pydantic's `ValidationError` and re-raises
this with a flattened one-line message:

```python
def _describe(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"]) or "input"
        message = err["msg"].removeprefix("Value error, ")
        parts.append(f"{field} {message[0].lower()}{message[1:]}")
    return "; ".join(parts)
```

The reason: rendering `str()` of a raw `ValidationError` into a form's
alert box produces a multi-line internal dump. This produces
`account_type must be one of: asset, liability, equity, revenue, expense`.

### 5.6 `app/api/health.py`

```python
@router.get("/health")
async def health() -> dict:
    db_ok = await ping()
    return {"status": "ok" if db_ok else "degraded",
            "db": "up" if db_ok else "down"}
```

**It returns HTTP 200 in both cases.** A monitoring system reads the
body, not the status code. This is a defensible design — but it means any
check that only asserts "200" will pass against a completely broken
database. See §5.13.

### 5.7 `app/main.py` — wiring and routes

**Setup.** Creates the FastAPI app, mounts `/static`, points Jinja2 at
`templates/`, and registers a custom filter:

```python
def _money(value: Decimal | None) -> str:
    return f"{(value or Decimal('0')):,.2f}"

templates.env.filters["money"] = _money
```

`{{ total_debits|money }}` in a template renders `61,056.50`. Centralising
formatting means every page shows money identically.

**`GET /` — the overview.** The most interesting query. It computes
per-account debits, credits and balance in **one** SQL statement using
conditional aggregation:

```python
debit = func.coalesce(
    func.sum(case((ledger_entries.c.entry_type == "debit", ledger_entries.c.amount), else_=0)), 0)
```

`case(...)` becomes SQL `CASE WHEN entry_type='debit' THEN amount ELSE 0 END`;
summing that gives the debit total. `coalesce(..., 0)` turns `NULL` (an
account with no entries) into `0`.

It uses an **`outerjoin`** from `accounts` to `ledger_entries`, so
accounts with no activity still appear with a zero balance — an inner
join would hide them.

Then Python groups rows by `account_type` into a `defaultdict(list)` and
applies the normal-side sign flip from §2.2.

**`GET /event-log`** — paginated, 25 per page, newest first, with a total
count for the pager.

**`GET /accounts/new` / `POST /accounts`** — the form and its handler.
Validates via `validate_account`, inserts with a server-generated UUID,
redirects `302` to `/`. On `InvalidAccountError`, re-renders the form
with the submitted values preserved and status **422**.

**`GET /post-transaction` / `POST /post-transaction`** — the posting form.
FastAPI receives repeated form fields as lists:

```python
account_id: list[str] = Form(...),
entry_type: list[str] = Form(...),
amount:     list[str] = Form(...),
currency:   list[str] = Form(...),
```

zipped with `strict=True` (Python 3.10+) so mismatched lengths raise
rather than silently truncating. Then validation, then the idempotency
logic of §4.

**`GET /transaction-detail/{id}`** — loads the transaction, joins entries
to account names, splits them into debit and credit lists, totals each
side, and finds the linked event. A `404` if the transaction is missing.

### 5.8 `app/templates/` — Jinja2 inheritance

`base.html` holds the shared chrome; each page declares
`{% extends "base.html" %}` and fills blocks.

```
base.html
├── {% block title %}     page title
├── {% block content %}   main body
└── {% block scripts %}   anything after </main>
```

Three mechanics worth knowing:

**`{% block %}`** — a named hole a child fills.

**`{% set %}` at child top level propagates to the parent.** Pages that
need different chrome set a variable before the content block:

```jinja
{% extends "base.html" %}{% set main_class = "max-w-5xl mx-auto p-8" %}
```

and `base.html` reads it with a fallback:

```jinja
<main class="{{ main_class|default('max-w-6xl mx-auto p-8') }}">
```

**`{% block scripts %}` exists for a specific reason.** On
`post_transaction.html`, the `<template id="line-template">` and the
totals `<script>` live *after* `</main>`. An earlier refactor that moved
only the contents of `<main>` into the content block silently dropped
both — the page still rendered, but "Add line", "Remove" and the live
debit/credit totals stopped working with nothing in the page source to
show it. The block is what keeps them attached.

**`{% for %}…{% else %}`** — Jinja's `else` on a loop runs when the
sequence was empty. Used for "No accounts yet." fallbacks.

### 5.9 `alembic/` — migrations

Migrations version the schema so it can be recreated deterministically.
`d8b553ce7776_initial_ledger_schema.py` creates all five tables;
`downgrade()` drops them in reverse dependency order.
`b7855ff9a6aa_events_sequence_identity_and_indexes.py` then replaces
`events.sequence` with the identity column described in §5.2 and adds the
three `events` indexes.
`de4f1aec2fe6_read_model_indexes_and_account_type_check.py` indexes the
read model — `ledger_entries(account_id)`, `ledger_entries(transaction_id)`
and `transactions(created_at DESC)` — and adds the `account_type` `CHECK`.

The interesting part is `alembic/env.py`:

```python
config.set_main_option("sqlalchemy.url",
                       settings.database_url.replace("+asyncpg", "+psycopg"))
```

Alembic runs **synchronously**, but the app's URL specifies the async
driver `asyncpg`. This rewrites it to the sync `psycopg` driver — which
is why `requirements.txt` carries *both* drivers. The `sqlalchemy.url`
value in `alembic.ini` is therefore dead configuration; `env.py` always
overwrites it.

Migrations run automatically on container start, from the Dockerfile:

```dockerfile
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```

`&&` matters: if migrations fail, uvicorn never starts.

### 5.10 `scripts/seed_demo_data.py`

Populates an empty ledger with a one-person consultancy's books: 8
accounts across all five types and 10 transactions, including one in EUR
and one three-legged entry.

The key decision is that it writes **through the domain layer** —
`validate_account` and `post_transaction` — rather than by raw `INSERT`.
Because `post_transaction` appends the `events` row, a seeded database
has the event log it would have had if a human had typed every
transaction into the app. A SQL dump would leave `events` empty and the
event-log page blank.

It is **idempotent by refusal**: it counts `accounts` first and exits with
a message if any exist, so a second run cannot duplicate data. The whole
dataset is written inside one `engine.begin()` block.

### 5.11 `tests/`

| File | Needs a DB | Covers |
|---|---|---|
| `test_ledger_domain.py` | no | the balance invariant, per-currency independence, amount/type validation |
| `test_health.py` | no | health endpoint always answers |
| `test_ledger_pages.py` | **yes** | idempotent retry, inline errors, account creation, overview |

`test_ledger_pages.py` skips unless `TEST_DATABASE_URL` is set, then
creates and drops the whole schema around each test for isolation. It
drives the app in-process through `httpx.ASGITransport` — no real network
or server.

`pyproject.toml` sets `asyncio_mode = "auto"`, so `async def` tests run
without needing an explicit marker.

### 5.12 Docker

`Dockerfile` — `python:3.12-slim`, install requirements, copy `app/`,
`alembic/`, `alembic.ini` and `scripts/`, migrate-then-serve on start.

`docker-compose.yml` — a `db` service (postgres:16-alpine) with a
`pg_isready` healthcheck, and an `app` service with
`depends_on: condition: service_healthy`, so the app never starts against
a database that is not accepting connections yet.

### 5.13 `.github/workflows/ci.yml`

Two independent jobs:

**`lint-and-test`** — ruff, then pytest against a real Postgres service
container.

**`docker-smoke`** — proves the shipped image works: build, `up -d`, poll
`/health` until it answers (with a deadline, not a fixed sleep), assert
the body is **exactly** `{"status":"ok","db":"up"}`, assert
`alembic_version` is stamped, then drive a real flow — create two
accounts, read their ids back from the container's database, post a
balanced transaction, and require the balance to appear on the overview
page. Teardown runs under `if: always()`.

The exact-body assertion is the point. Because `/health` returns 200 even
when the database is down (§5.6), a status-code check would go green over
a failed migration. Verified by stopping the db container: `/health` still
returned **HTTP 200** with `{"status":"degraded","db":"down"}`.

---

## 6. Two request walkthroughs

### Creating an account — `POST /accounts`

```
Browser form
  → FastAPI parses name / account_type / currency
  → validate_account({...})
      → AccountInput validators strip, upper-case, check membership
      → on failure: InvalidAccountError with a flat message
          → re-render form, values preserved, HTTP 422
  → uuid.uuid4() server-side
  → engine.begin(): INSERT INTO accounts
  → commit
  → 302 redirect to /
```

### Posting a transaction — `POST /post-transaction`

```
Browser form (repeated fields → lists)
  → zip(..., strict=True) into raw entry dicts
  → EntryInput.model_validate per row   (types, side, amount > 0)
  → assert_balanced(entries)            (per-currency netting)
  → require >= 2 entries
      → any failure: re-render with values + inline error, HTTP 422
  → sha256 of the canonical request JSON
  → engine.begin():
        lookup submission_key in idempotency_keys
          ├─ found, hash matches  → reuse stored transaction id
          ├─ found, hash differs  → HTTP 409
          └─ not found → post_transaction(conn, entries, description)
                            ├─ INSERT transactions
                            ├─ INSERT ledger_entries (bulk)
                            └─ INSERT events   ← the source of truth
                         → INSERT idempotency_keys
     commit  (all of the above, atomically)
  → 302 redirect to /transaction-detail/{id}
```

---

## 7. What has been done

**Schema and migrations** — all five tables, with `CHECK` constraints on
entry type, amount and account type, exact-decimal money columns, a
database-generated identity column giving the event log a real total
order, indexes behind every query the pages actually run, and Alembic
migrations that run automatically on container boot.

**Domain layer** — the per-currency double-entry invariant, entry
validation against the accounts an entry names (the account must exist,
and its currency is the only one that entry may carry), atomic posting
that writes the event log alongside the read model, and account input
validation.

**HTTP layer** — health check, and five server-rendered pages: overview
with per-account balances and normal-side signs, event log with
pagination, transaction posting form with live client-side totals,
transaction detail with debit/credit columns, and account creation.

**Idempotency** — fully wired for transaction posting, including the
409-on-key-reuse case.

**Templates** — shared `base.html`; the four original pages were
refactored onto it with rendered output verified byte-identical.

**Seed data** — `python -m scripts.seed_demo_data`, domain-layer-driven
and idempotent.

**Testing** — 12 tests: the invariant and validation without a database,
plus Postgres-backed page tests for idempotent retry, inline validation
errors, account creation and overview rendering.

**CI** — ruff and Postgres-backed tests, plus a `docker-smoke` job that
proves the container builds, migrates and serves a real posting flow.

---

## 8. What still needs doing

Ordered roughly by how much they would hurt.

### Correctness

**1. No replay/rebuild path.** The read model is described as rebuildable
from `events`, but no code rebuilds it. Until a replay function exists and
is tested, that is an untested claim.

**2. No FX handling.** Per §2.4, currency conversion cannot be expressed.
Needs a clearing-account pattern plus an FX gain/loss account.

### Robustness

**3. No balance enforcement at the database level.** Noted in the schema
comments as a deliberate v2 item. A constraint trigger would make a
half-written transaction impossible even from outside the app.

**4. `idempotency_keys` grows forever.** No TTL or cleanup job.

**5. No authentication or authorisation anywhere.** Every route is public.
Acceptable for a demo, disqualifying for anything real.

**6. Raw validation errors on `POST /post-transaction`.** It still renders
`str(ValidationError)` for non-imbalance failures (e.g. a malformed
amount), producing a multi-line internal dump in the alert box. The
`_describe()` helper in `accounts.py` already solves this and should be
shared.

### Build and tooling

**7. The compose bind mount shadows the image.** `docker-compose.yml`
mounts `./app:/app/app` for live reload, so the container runs the host's
`app/` rather than the copy baked into the image — meaning the CI smoke
test would not catch a broken `COPY app/ ./app/`. Consider a compose
override so CI tests the image as shipped.

**8. No `.dockerignore`.** The whole directory is sent as build context,
including `.git/`, `.pytest_cache/` and `.ruff_cache/`.

**9. No `app` healthcheck in compose.** Only `db` has one.

### Documentation

**10. The README's "Running tests locally" section is out of date.** It
claims the suite runs "without requiring a live database", which is no
longer true of `test_ledger_pages.py`. ("Status" and "Roadmap" have since
been rewritten to match what is actually built.)

### Roadmap items not started

Webhook ingestion, multi-provider payment orchestration, reconciliation,
the outbox pattern for reliable event publishing, structured logging and
metrics, and a deployment pipeline.
