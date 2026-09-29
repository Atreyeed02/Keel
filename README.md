# Keel

An event-sourced, double-entry ledger service: the accounting core you'd
put behind a payments platform, built to explore the same invariants a
system like Juspay has to get right. Money never appears or disappears,
every state change is auditable and replayable, and a retried request
never double-charges.

FastAPI · PostgreSQL 16 · SQLAlchemy 2.0 Core · Alembic · Docker Compose

## The guarantees, and where each one is enforced

| Guarantee | Enforced by | Proven by |
|---|---|---|
| Every transaction's debits equal its credits, **per currency** | `assert_balanced` in Python *and* a deferred Postgres constraint trigger | `tests/test_ledger_domain.py`, `tests/test_ledger_invariants.py` |
| Every entry carries its account's currency, and an account's currency never changes | `assert_accounts_valid` in Python *and* two Postgres triggers | `tests/test_ledger_pages.py`, `tests/test_ledger_invariants.py` |
| The event log is never rewritten | a Postgres trigger refusing UPDATE / DELETE / TRUNCATE on `events` | `tests/test_ledger_invariants.py` |
| A posting lands completely or not at all: rows *and* event | one database transaction per posting | `tests/test_ledger_invariants.py` |
| One idempotency key → one committed ledger effect, even under concurrent retries | claim-first `INSERT … ON CONFLICT DO NOTHING`, for the form and the JSON API alike | `tests/test_idempotency.py`, `tests/test_api.py` |
| The read model can be thrown away and rebuilt from the log | `rebuild_read_model()` | `tests/test_rebuild.py` |

Enforcing the ledger rules in the database as well as in Python is
deliberate. The Python checks produce readable errors for the forms and the API. The triggers
make the rules hold for anything that writes around the app: a migration,
a `psql` session, a future importer.

## Why event sourcing

Most CRUD ledgers store a running `balance` column and update it in
place. That is fast until something goes wrong (a bug, a race, a replayed
webhook), and then there is no way to reconstruct *how* the balance got
wrong, because the history was never kept.

Here, every change is first appended to an immutable `events` log:
`account.created` or `transaction.posted`, with the full payload. The
tables you query for balances (`accounts`, `transactions`,
`ledger_entries`) are a **projection** of that log. They are written in
the same database transaction as the event, so the two can never
disagree, and they can be deleted and rebuilt from the log at any time.
The log is the audit trail, and it is the source of truth when the two
are compared.

## Why double-entry

Every transaction is a set of debit and credit entries that must net to
zero. Money moves *between* accounts; it is never created or destroyed.
A payment of 100 USD is a 100 debit to Cash and a 100 credit to Revenue,
never "+100 to Cash" on its own. That makes conservation checkable at
every point: across the whole ledger, per currency, debits minus credits
is exactly zero (the trial balance, asserted in the test suite).

Amounts are `NUMERIC(18,2)` and Python `Decimal`, never floats. They are
always positive: the direction lives in `entry_type`. The balance rule is
applied **per currency**, because 100 USD against 100 EUR only "balances"
if you add dollars to euros.

## How idempotency works

Every posting carries a client-supplied key (the posting form generates
one per page render, so a double-click or a refresh reuses it). Inside
the posting's database transaction, `app/domain/idempotency.py`:

1. **Claims the key** with `INSERT … ON CONFLICT (key) DO NOTHING`,
   before posting anything.
2. If the claim succeeds, posts the transaction and stores its id on the
   key row, all in the same commit.
3. If the key already exists, compares a SHA-256 of the request. A match
   is a genuine retry, so it returns the original transaction and posts
   nothing. A mismatch means the key was reused for a different request:
   `409 Conflict`.

Claiming first is what makes this hold under concurrency. When a second
request tries to insert a key that another transaction holds but has not
committed, Postgres makes it **wait**. Once the first commits, the second
sees the stored result and replays it. If the first rolls back instead
(say its entries named an account that doesn't exist), the claim vanishes
with it and the second proceeds. A rejected attempt never burns a key.

This replaced an earlier SELECT-then-INSERT version. That version never
double-posted, but five concurrent duplicates got `[500, 302, 500, 500,
500]`. They now all get the same 302. The concurrency tests use an
`asyncio.Barrier` to force the race deterministically. Full walkthrough:
[docs/ARCHITECTURE.md §4](docs/ARCHITECTURE.md#4-idempotency).

Keys are not kept forever. `python -m scripts.prune_idempotency_keys
--yes` deletes those over 30 days old (`--older-than-days` to change it,
never under 1). Past that window a retry is no longer recognised and posts
again, so the window has to outlast any client's retries.

## How replay / rebuild works

```
events  (append-only, total order = events.sequence, a DB identity column)
   │
   ▼
rebuild_read_model(conn)                       one database transaction
   1. TRUNCATE ledger_entries, transactions, accounts RESTART IDENTITY
   2. read every event: account.created first, then the rest, each ORDER BY sequence
   3. account.created    → INSERT accounts        (id = event.aggregate_id)
      transaction.posted → INSERT transactions + ledger_entries
      unknown type       → raise; the whole rebuild rolls back
   ▼
rebuilt projection  (the balance trigger re-checks every transaction at commit)
```

```bash
python -m scripts.rebuild_read_model          # report only
python -m scripts.rebuild_read_model --yes    # truncate and replay
```

Accounts are replayed before transactions because of the backfill. A
database from before `account.created` existed has accounts with no
event, and the rebuild refuses to run on it until they are appended:

```bash
python -m scripts.backfill_account_events          # list them
python -m scripts.backfill_account_events --yes    # append their events
```

Those events land after the transactions that use the accounts, so a
strictly sequential replay would insert entries before their account.
Each backfilled event carries the account's original `created_at`, which
the rebuild restores.

With `--yes` it prints every account whose balance the rebuild changed.
On a healthy ledger that list is empty. If the projection has drifted
from the log, the list shows exactly what was corrected.

The tests check that a rebuild:

- reproduces the ledger exactly: every account, transaction, entry and
  balance, and the rendered pages;
- repairs a deliberately corrupted read model;
- is repeatable;
- is safe while postings are arriving. The `TRUNCATE` lock makes a
  concurrent posting wait, and that posting is not lost.

Known limit: entry ids are regenerated. See
[§3.3](docs/ARCHITECTURE.md#33-rebuildability-tested-with-one-known-limit).

## Architecture

```
Client (browser form / curl)
  │
  ▼
FastAPI  app/main.py ─── request-id middleware, JSON logs  (app/observability.py)
  │        routes: HTTP parsing, status codes, templates
  ▼
Domain   app/domain/
  │        ledger.py       assert_balanced, assert_accounts_valid, post_transaction
  │        idempotency.py  post_transaction_once (claim-first)
  │        accounts.py     validate_account, create_account_record
  │        rebuild.py      rebuild_read_model
  ▼
SQLAlchemy Core  app/db/   explicit statements, no ORM session / flush magic
  ▼
PostgreSQL
  ├── events              append-only source of truth       (trigger: no UPDATE/DELETE/TRUNCATE)
  ├── accounts            ┐                                 (trigger: currency never changes)
  ├── transactions        ├ projection, rebuildable         (trigram index behind the search)
  ├── ledger_entries      ┘   from events                   (triggers: balanced per currency at
  │                                                          commit; account's own currency)
  └── idempotency_keys    key → request hash + stored result
```

Domain functions never commit. The route handler owns the transaction
boundary, which is what lets the idempotency claim, the posting and the
event share a single commit.

## Database model

| Table | Role | Key columns |
|---|---|---|
| `events` | source of truth, append-only | `sequence` (identity, total order), `aggregate_type`, `aggregate_id`, `event_type`, `payload` JSONB |
| `accounts` | projection | `name`, `account_type` (CHECK: asset/liability/equity/revenue/expense), `currency` |
| `transactions` | projection | `description`, `sequence` (identity, posting order) |
| `ledger_entries` | projection | `transaction_id`, `account_id`, `entry_type` (CHECK: debit/credit), `amount` (CHECK > 0), `currency` |
| `idempotency_keys` | retry protection | `key` (PK), `request_hash`, `response_body` |

Schema is managed by Alembic (10 migrations, applied automatically on
container start). Every column and index is explained in
[§5.2](docs/ARCHITECTURE.md#52-appdbschemapy--the-tables).

## Running locally

```bash
cp .env.example .env
docker compose up --build
```

The app container runs `alembic upgrade head` before starting Uvicorn, so
the schema is created automatically. Optional demo data (8 accounts, 10
transactions, two currencies), written through the domain layer so the
event log is populated too:

```bash
docker compose exec app python -m scripts.seed_demo_data
```

Then open http://localhost:8000 (UI), http://localhost:8000/docs (OpenAPI)
or http://localhost:8000/health.

### Dev mode vs. the shipped image

| Command | Files used | What runs |
|---|---|---|
| `docker compose up --build` | base + override | the host's `./app`, bind-mounted over the image, with the server reloading itself when a file changes |
| `docker compose -f docker-compose.yml up --build` | base only | exactly what the image contains |

Compose merges `docker-compose.override.yml` automatically whenever it is
present, so the first form is the default in a checkout. The second is
what CI's `docker-smoke` job runs (via `COMPOSE_FILE`) and what a
deployment would use; it is the only one that proves the image is
self-contained.

Both give the `app` service a healthcheck against `/health` that asserts
the exact body `{"status":"ok","db":"up"}`, so a container whose
migrations failed never reports healthy.

## Deploying

Keel is one Docker image plus a Postgres database, so it runs on any host
that runs an image and provides Postgres: Render, Railway, Fly.io and the
like. Nothing here is specific to one of them.

**Build** from the `Dockerfile`. The compose files are for running locally
and are not part of a deployment.

**Start command:** the image's own, `python -m app.serve`. It runs
`alembic upgrade head`, and only if that succeeds starts uvicorn on `$PORT`
(most hosts set `PORT`; the default is 8000). The container runs as an
unprivileged user and never with `--reload`.

> **Migrating on start assumes one instance.** Two instances starting at
> once would both migrate. Before running more than one, or on a host that
> starts the new instance before stopping the old one, run
> `alembic upgrade head` as a release step and start instances with
> `python -m app.serve --no-migrate`.
>
> **So does the write rate limit.** Its counts live in the process's memory,
> so each instance allows the full rate, and a restart forgets them. Running
> more than one instance needs a shared store (Redis, say) instead.

**Health check:** path `/health`. It answers **200 even when the database
is down**, with `{"status":"degraded","db":"down"}`, so a check that looks
only at the status code stays green on a broken deployment. Configure the
host to require the body `{"status":"ok","db":"up"}`. If your host can only
check the status code, know that it will not notice a lost database.

### Environment variables

| Variable | Required | What it does |
|---|---|---|
| `DATABASE_URL` | **yes** | The Postgres URL. `postgres://`, `postgresql://` and `postgresql+asyncpg://` all work, with or without `?sslmode=...`. |
| `ENVIRONMENT` | **yes**, set to `production` | Refuses to start if `DATABASE_URL` is unset or is the local `ledger:ledger@db` default. |
| `PORT` | set by most hosts | Where the server listens. Default 8000. |
| `FORWARDED_ALLOW_IPS` | **yes** behind a proxy | Proxies whose `X-Forwarded-For` / `-Proto` are believed, as addresses and networks. It decides who the client is: in the logs, and for the write rate limit. On Render: `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16` (below). Default `127.0.0.1`. |
| `WRITE_RATE_LIMIT`, `WRITE_RATE_WINDOW_SECONDS` | no | Writes (any method but `GET`, `HEAD`, `OPTIONS`) one client may make in any window, forms and API alike; past that, `429` with `Retry-After`. Reads are not limited. Default 30 per 60 seconds; `WRITE_RATE_LIMIT=0` turns it off. |
| `DATABASE_SSL` | if the database needs TLS | `disable`, `allow`, `prefer`, `require`, `verify-ca` or `verify-full`. Overrides an `sslmode` in the URL. Unset: whatever the URL says, else the driver default. |
| `MAX_ACCOUNTS`, `MAX_TRANSACTIONS` | no | The most accounts and transactions the ledger will hold. A write that would add one more is a `409` (`ledger_full`), form or API; replays are still answered. Default 200 and 2000, sized for a 0.5 GB database (below). `0` means no cap, which a real ledger wants. |
| `MAX_REQUEST_BODY_BYTES` | no | Largest request body accepted; larger is a 413. Default 65536. |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | no | Connections per instance, default 5 + 10. Keep the total under your database plan's connection limit. |
| `LOG_LEVEL` | no | Level of the JSON log lines on stdout. Default `INFO`. |

### Why 200 accounts and 2000 transactions

The caps are there so a public demo cannot fill a free 0.5 GB database,
however many clients write to it. Measured on Postgres 16, including indexes,
the event row and the idempotency key: an account takes about 1.1 KB, a
two-entry transaction about 1.7 KB, and the largest transaction the 64 KiB
body limit admits (470 entries, a 512-character description) about 91 KB.
So even if every one of the 2000 were that large, the ledger would stay
under 200 MB. Concurrent writes can overshoot a cap by the few that were
already in flight when it was reached. Raising `MAX_REQUEST_BODY_BYTES`
raises the worst case with it.

### Behind Render's proxy: `FORWARDED_ALLOW_IPS`

On Render, every request reaches the container from Render's router, so
without help every client has the router's address and they all share one
write allowance. Set

```
FORWARDED_ALLOW_IPS=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
```

The router connects from a private address, which no internet client can
have. uvicorn then reads `X-Forwarded-For` from the right and takes the
first address that is not private: the one Render's router wrote for the
connection it received.

**Not `*`.** With `*`, uvicorn takes the header's *leftmost* address. Render
has said its router appends to an incoming `X-Forwarded-For` rather than
replacing it, and documents no promise to strip one, so the leftmost
address can be whatever the client sent. Any client could then name a new
address with every request and never be rate limited. Reading from the
right holds up whether the router appends or replaces, as long as it adds
its entry at the end, which is what every proxy following the
`X-Forwarded-For` convention does.
`tests/test_proxy_headers.py` shows both: separate limits for two forwarded
clients, and a forged header that buys no fresh allowance.

**Check it after the first deploy.** This setting follows from how Render
says its router behaves and from uvicorn's code. It has not been checked
against a live Render service. Send a write with a forged header,
`curl -X POST -H 'X-Forwarded-For: 192.0.2.1' https://<service>/api/transactions`,
and look at that request's `request.completed` line in Render's logs. Its
`client` must be your own public address. If it is `192.0.2.1`, the setting
is not in effect. If it is some other address that is not yours, such as a
Cloudflare one, there is another proxy in the chain; add its published
ranges to the list.

### Scheduled and one-off jobs

Run these with the same image and environment as the app, from the host's
cron or scheduled-job feature, or its one-off shell:

| Command | When |
|---|---|
| `python -m scripts.prune_idempotency_keys --yes` | **daily.** Deletes idempotency keys older than 30 days; without it the table grows forever. |
| `python -m scripts.seed_demo_data` | once, on an empty database, if you want the demo data. It refuses to touch a ledger that already has accounts. |
| `python -m scripts.rebuild_read_model --yes` | only to repair the read model from the event log. Safe while serving; postings wait for it. |

### Pre-deploy checklist

- [ ] `ENVIRONMENT=production` and `DATABASE_URL` set on the host.
- [ ] `DATABASE_SSL` set if the database requires TLS (most managed ones do).
- [ ] `FORWARDED_ALLOW_IPS` set to the proxy's networks (on Render, the three private ranges above), never `*`.
- [ ] Health check on `/health`, checking the body, not just the status.
- [ ] One instance, or migrations moved to a release step and `--no-migrate` on the start command.
- [ ] A daily job for `python -m scripts.prune_idempotency_keys --yes`.
- [ ] `DB_POOL_SIZE + DB_MAX_OVERFLOW` times the number of instances is under the database's connection limit.
- [ ] CI is green on the commit being deployed: tests, `alembic check`, `pip-audit`, and the image smoke test.
- [ ] After the first deploy: `/health` returns `{"status":"ok","db":"up"}`, and the response headers include `Content-Security-Policy`.
- [ ] After the first deploy: a write with a forged `X-Forwarded-For` is logged with your own address as `client` (above).

## Running tests

```bash
pip install -r requirements.txt
docker compose up -d db
docker compose exec db createdb -U ledger ledger_test
export TEST_DATABASE_URL=postgresql+asyncpg://ledger:ledger@localhost:5432/ledger_test
pytest -v
ruff check .
```

134 tests. Point `TEST_DATABASE_URL` at a scratch database, not the one the
app runs on: the fixtures drop and recreate every table around each test,
with `metadata.create_all()`, so no migrations need to be applied first.
They refuse to run on a database alembic has migrated (one with an
`alembic_version` table). Dropping the app tables there would leave it
stamped "at head" with nothing in it, and `alembic upgrade head` would then
do nothing.

Without `TEST_DATABASE_URL`, the 86 database-backed tests are **skipped,
not failed**. A green run of the remaining 48 is partial coverage:

```
SKIPPED [1] tests/test_ledger_pages.py: set TEST_DATABASE_URL to run PostgreSQL page integration tests
```

| File | Covers |
|---|---|
| `test_ledger_domain.py` | the balance invariant and entry validation, no database |
| `test_ledger_pages.py` | every page, inline errors, filters (UTC day boundaries whatever the session time zone), pagination, `sequence` ordering, the search's trigram index |
| `test_idempotency.py` | retries, 409 on key reuse, key release after rejection, concurrent duplicates and conflicts, key retention and its CLI |
| `test_ledger_invariants.py` | every DB trigger against writes that bypass the app, atomic rollback, log ↔ read-model agreement, trial balance |
| `test_rebuild.py` | round trip, recovery from corruption, repeatability, rebuild alongside a live posting, backfilling a legacy ledger, entry order across a rebuild, payload schema versions, both CLIs |
| `test_observability.py` | request ids, JSON log format, ledger identifiers on log lines |
| `test_health.py` | `/health` always answers |
| `test_schema_guard.py` | the fixtures refuse to wipe a migrated database |
| `test_api.py` | every JSON API status code, the error shape, string amounts, replays, concurrent duplicate requests |

CI (`.github/workflows/ci.yml`) runs ruff and the full suite against a
PostgreSQL service container. A separate `docker-smoke` job builds the
image, boots the stack, confirms migrations ran and drives account
creation → posting → overview over HTTP.

## API

Two interfaces over the same domain layer: a **JSON API** under `/api/`
and the **server-rendered HTML pages**. FastAPI's generated docs at
`/docs` list every route.

### JSON

| Method | Path | Answers |
|---|---|---|
| `POST` | `/api/accounts` | `201` with the account; `422`; `409` (`ledger_full`) at `MAX_ACCOUNTS` |
| `GET` | `/api/accounts` | `200`: every account with `debits`, `credits` and a `balance` signed by its normal side |
| `POST` | `/api/transactions` | `201` first post, `200` replay; `400` without a valid `Idempotency-Key`; `409` key reused for a different request, or (`ledger_full`) at `MAX_TRANSACTIONS`; `422` |
| `GET` | `/api/transactions/{id}` | `200` with the entries; `404` |

```bash
curl -i -X POST http://localhost:8000/api/transactions \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: invoice-1001' \
  -d '{"description": "Invoice 1001", "entries": [
        {"account_id": "'$CASH'",    "entry_type": "debit",  "amount": "250.00", "currency": "USD"},
        {"account_id": "'$REVENUE'", "entry_type": "credit", "amount": "250.00", "currency": "USD"}]}'
```

A retry with the same key answers `200` with the same transaction and
`Idempotent-Replayed: true`. Retries are matched by meaning, not bytes:
`"100"` and `"100.00"`, object key order, currency case and whitespace do
not make a retry a different request. Entry order does, since it is
stored. Amounts are strings in both
directions, never JSON numbers. Every error is
`{"error": {"code": ..., "message": ...}}`. Any write, here or through the
forms, can also be a `429` (`rate_limited`) with `Retry-After` once a client
passes `WRITE_RATE_LIMIT`. Details:
[docs/ARCHITECTURE.md §5.16](docs/ARCHITECTURE.md#516-appapi--the-json-api).

### HTML

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | balances per account (normal-side signed), totals per currency, recent transactions |
| `GET` | `/transactions` | all transactions; `q` (description search), `date_from`, `date_to`, `page` |
| `GET` | `/transaction-detail/{id}` | one transaction's debits and credits, and its event |
| `GET` | `/event-log` | the raw event log, newest first, paginated |
| `GET` / `POST` | `/accounts/new`, `/accounts` | create an account: `name`, `account_type`, `currency` |
| `GET` / `POST` | `/post-transaction` | post a transaction: repeated `account_id` / `entry_type` / `amount` / `currency` fields, plus `description` and `submission_key` |
| `GET` | `/health` | `{"status":"ok","db":"up"}`, or `degraded`/`down` (never raises) |

`POST /post-transaction` answers `302` to the transaction's detail page on
success *and* on an idempotent retry, `422` with the form re-rendered and
an inline error on invalid input, and `409` when a `submission_key` is
reused with a different request. Both forms re-render with an inline error
and a `409` when the ledger is at `MAX_ACCOUNTS` or `MAX_TRANSACTIONS`.

For example:

```bash
curl -i -X POST http://localhost:8000/post-transaction \
  -d submission_key=invoice-1001 -d description="Invoice 1001" \
  -d account_id=$CASH    -d entry_type=debit  -d amount=250.00 -d currency=USD \
  -d account_id=$REVENUE -d entry_type=credit -d amount=250.00 -d currency=USD
```

## Observability

Structured JSON logs, one object per line, on the `keel` logger (level
from `LOG_LEVEL`). Every request gets an id, taken from a well-formed
`X-Request-ID` header or generated, and returned in the response's
`X-Request-ID` header. Ledger writes log after they commit:

```json
{"event": "transaction.posted", "request_id": "7d92…", "transaction_id": "2e1c…",
 "idempotency_key": "invoice-1001", "entry_count": 2, "account_ids": ["39fc…", "e285…"]}
```

Also logged: `transaction.replayed`, `transaction.rejected`,
`idempotency.conflict`, `ledger.full`, `account.created`, `request.completed`. There
are no metrics and no tracing.

## Project layout

```
app/
├── api/                  JSON API: accounts.py, transactions.py, errors.py, serialize.py; health.py
├── domain/               business rules; nothing here commits
│   ├── ledger.py         double-entry validation and posting
│   ├── idempotency.py    claim-first idempotent posting
│   ├── accounts.py       account validation and creation
│   ├── rebuild.py        replay events into the read model
│   ├── reads.py          balances and transaction lookups shared by pages and API
│   ├── capacity.py       the caps on total accounts and transactions
│   └── account_types.py, errors.py
├── db/                   SQLAlchemy Core tables, triggers, engine
├── observability.py      JSON logging, request-id middleware
├── security.py           body size limit, security headers
├── ratelimit.py          per-client write rate limit
├── main.py               routes and wiring
└── templates/, static/   Jinja2 pages
alembic/versions/         10 migrations
scripts/                  seed_demo_data.py, rebuild_read_model.py, backfill_account_events.py,
                          prune_idempotency_keys.py
tests/                    134 tests; see above
docs/                     ARCHITECTURE.md (full walkthrough), STATUS.md (build status)
```

## Limitations and future work

What this does **not** do today. The full, maintained list is
[docs/ARCHITECTURE.md §8](docs/ARCHITECTURE.md#8-what-still-needs-doing).

- **The JSON API is minimal.** No single-account read, no transaction
  listing, no pagination, no event-log endpoint.
- **No authentication or authorisation.** Every route is public, which
  is also why rebuild is a CLI and not a route.
- **Rebuild is all-or-nothing and in memory.** No snapshots, no
  incremental projection catch-up. Postings wait while a rebuild runs.
- **Key pruning is a script, not a scheduler.** Something has to run
  `scripts.prune_idempotency_keys` periodically; nothing in the stack does.
- **Idempotency covers postings only**, not account creation, since a
  duplicate account moves no money.
- **No FX yet** (deferred future work). A transaction may touch several
  currencies, but each must balance on its own. Conversion needs a
  clearing-account pattern.
- **Observability is logs only.** No metrics, no tracing.

**Deliberately out of scope.** These are the layers a payments platform
puts *around* a ledger, listed to mark the boundary, not as planned work:
webhook ingestion, multi-provider payment orchestration, reconciliation,
an outbox for reliable event publishing, and a deployment pipeline.
