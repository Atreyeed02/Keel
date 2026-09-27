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
| One idempotency key → one committed ledger effect, even under concurrent retries | claim-first `INSERT … ON CONFLICT DO NOTHING` | `tests/test_idempotency.py` |
| The read model can be thrown away and rebuilt from the log | `rebuild_read_model()` | `tests/test_rebuild.py` |

Enforcing the ledger rules in the database as well as in Python is
deliberate. The Python checks produce readable form errors. The triggers
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

Schema is managed by Alembic (5 migrations, applied automatically on
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
| `docker compose up --build` | base + override | the host's `./app`, bind-mounted over the image — edits appear without a rebuild |
| `docker compose -f docker-compose.yml up --build` | base only | exactly what the image contains |

Compose merges `docker-compose.override.yml` automatically whenever it is
present, so the first form is the default in a checkout. The second is
what CI's `docker-smoke` job runs (via `COMPOSE_FILE`) and what a
deployment would use; it is the only one that proves the image is
self-contained.

Both give the `app` service a healthcheck against `/health` that asserts
the exact body `{"status":"ok","db":"up"}`, so a container whose
migrations failed never reports healthy.

## Running tests

```bash
pip install -r requirements.txt
docker compose up -d db
docker compose exec db createdb -U ledger ledger_test
export TEST_DATABASE_URL=postgresql+asyncpg://ledger:ledger@localhost:5432/ledger_test
pytest -v
ruff check .
```

62 tests. Point `TEST_DATABASE_URL` at a scratch database, not the one the
app runs on: the fixtures drop and recreate every table around each test,
with `metadata.create_all()`, so no migrations need to be applied first.
They refuse to run on a database alembic has migrated (one with an
`alembic_version` table). Dropping the app tables there would leave it
stamped "at head" with nothing in it, and `alembic upgrade head` would then
do nothing.

Without `TEST_DATABASE_URL`, the 45 database-backed tests are **skipped,
not failed**. A green run of the remaining 17 is partial coverage:

```
SKIPPED [1] tests/test_ledger_pages.py: set TEST_DATABASE_URL to run PostgreSQL page integration tests
```

| File | Covers |
|---|---|
| `test_ledger_domain.py` | the balance invariant and entry validation, no database |
| `test_ledger_pages.py` | every page, inline errors, filters, pagination, `sequence` ordering |
| `test_idempotency.py` | retries, 409 on key reuse, key release after rejection, concurrent duplicates and conflicts |
| `test_ledger_invariants.py` | both DB triggers against writes that bypass the app, atomic rollback, log ↔ read-model agreement, trial balance |
| `test_rebuild.py` | round trip, recovery from corruption, repeatability, rebuild alongside a live posting, the CLI |
| `test_observability.py` | request ids, JSON log format, ledger identifiers on log lines |
| `test_health.py` | `/health` always answers |
| `test_schema_guard.py` | the fixtures refuse to wipe a migrated database |

CI (`.github/workflows/ci.yml`) runs ruff and the full suite against a
PostgreSQL service container. A separate `docker-smoke` job builds the
image, boots the stack, confirms migrations ran and drives account
creation → posting → overview over HTTP.

## API

The service is **server-rendered HTML with form posts**. There is no JSON
API yet (see Limitations). FastAPI's generated docs at `/docs` list every
route.

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
reused with a different request.

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
`idempotency.conflict`, `account.created`, `request.completed`. There
are no metrics and no tracing.

## Project layout

```
app/
├── api/health.py         /health
├── domain/               business rules; nothing here commits
│   ├── ledger.py         double-entry validation and posting
│   ├── idempotency.py    claim-first idempotent posting
│   ├── accounts.py       account validation and creation
│   ├── rebuild.py        replay events into the read model
│   └── account_types.py, errors.py
├── db/                   SQLAlchemy Core tables, triggers, engine
├── observability.py      JSON logging, request-id middleware
├── main.py               routes and wiring
└── templates/, static/   Jinja2 pages
alembic/versions/         5 migrations
scripts/                  seed_demo_data.py, rebuild_read_model.py, backfill_account_events.py,
                          prune_idempotency_keys.py
tests/                    62 tests; see above
docs/                     ARCHITECTURE.md (full walkthrough), STATUS.md (build status)
```

## Limitations and future work

What this does **not** do today. The full, maintained list is
[docs/ARCHITECTURE.md §8](docs/ARCHITECTURE.md#8-what-still-needs-doing).

- **No JSON API.** Writes are HTML form posts. A JSON endpoint with an
  `Idempotency-Key` header would reuse `post_transaction_once` as is.
- **No authentication or authorisation.** Every route is public, which
  is also why rebuild is a CLI and not a route.
- **Rebuild is all-or-nothing and in memory.** No snapshots, no
  incremental projection catch-up. Postings wait while a rebuild runs.
- **Key pruning is a script, not a scheduler.** Something has to run
  `scripts.prune_idempotency_keys` periodically; nothing in the stack does.
- **Idempotency covers postings only**, not account creation, since a
  duplicate account moves no money.
- **No FX.** A transaction may touch several currencies, but each must
  balance on its own. Conversion needs a clearing-account pattern.
- **Event payloads are unversioned.**
- **Observability is logs only.** No metrics, no tracing.

**Deliberately out of scope.** These are the layers a payments platform
puts *around* a ledger, listed to mark the boundary, not as planned work:
webhook ingestion, multi-provider payment orchestration, reconciliation,
an outbox for reliable event publishing, and a deployment pipeline.
