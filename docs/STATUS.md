# Keel — Build Status & Handoff

**As of 2026-09-27.** A snapshot of what is actually built, what is
verified, and where the next piece of work starts. For the *why* behind
the design — the accounting concepts, the event-sourcing rationale, a
file-by-file walkthrough — read `ARCHITECTURE.md` first; this document
does not repeat it.

---

## 1. Verified state, right now

Both feature branches are merged, with regular merge commits, and deleted:

| PR | Branch | Merge commit on `main` |
|---|---|---|
| [#1](https://github.com/Atreyeed02/Keel/pull/1) | `feat/event-sourced-accounts-and-rebuild` (18 commits) | `104f6a1` |
| [#2](https://github.com/Atreyeed02/Keel/pull/2) | `feat/json-api` (4 commits) | `bd9db76` |

CI passed both jobs on each PR and on `main` after each merge. No
feature branches remain.

Everything below was checked against the working tree at migration head
`c2e8f4a61b07`, not read off the older prose in `ARCHITECTURE.md`.

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `pytest` collection | **134 tests** |
| `pytest` (no database available) | 48 passed, 86 skipped |
| `pytest` (local Postgres 16) | 134 passed |
| `pytest -W error::DeprecationWarning` on the pinned stack | 134 passed |
| `pip-audit -r requirements.txt` | no known vulnerabilities |
| `alembic upgrade head` → `downgrade base` → `upgrade head` | clean (10 migrations each way), on a scratch database |
| `alembic check` | no differences; also runs in CI's `lint-and-test` job |
| Migrated schema vs. `metadata.create_all` | identical functions, triggers and indexes (only alembic's own table differs) |
| App booted with uvicorn on a migrated database | health, account creation, posting, retry, 409, the transaction filter and all three maintenance CLIs verified; the JSON API's 201, 200 replay (`Idempotent-Replayed`), 400, 409, balances and `/docs` verified over real HTTP |
| `docker compose config` (base, and base + override) | valid |
| Docker image build | built and smoke-tested by CI's `docker-smoke` on both PRs and on `main` at `bd9db76`: healthy `/health`, migrated to `5e8d2a1f9c63`, a real posting through the container. Not run locally, where Docker was not running. |
| CI on `main` at `bd9db76` | `lint-and-test` (119 passed, 0 skipped) and `docker-smoke` both pass |

The 86 skips are not failures. Every database-backed test skips itself
unless `TEST_DATABASE_URL` is set, so **a green local run of 48 tests
means barely a third of the suite actually executed.** Do not read it
as a passing build. See §5 for the command that runs the real thing.

> **`alembic check` passes and runs in CI.** It used to report one
> difference: `schema.py` declared a unique constraint on
> `idempotency_keys.key`, its primary key. Postgres had folded the two
> into one primary key named `uq_idempotency_key`, so no database had a
> duplicate; the model did. Migration `a4c7e2d9b813` removes it from the
> model and renames the key to `idempotency_keys_pkey`.
>
> One cosmetic difference remains that `alembic check` does not see:
> migrations spell the `created_at` defaults `CURRENT_TIMESTAMP`, and
> `schema.py` spells them `now()`. Postgres evaluates both as the
> transaction's start time.

---

## 2. What exists

### Schema — complete, 10 migrations

Five tables in `app/db/schema.py`, with `alembic/versions/` at head
`c2e8f4a61b07`:

- `events` — append-only log. `sequence` is `BigInteger Identity(always=True)`,
  so the log has a real database-generated total order that the
  application cannot supply or fudge. This matters more than it looks:
  Postgres evaluates `CURRENT_TIMESTAMP` at *transaction start*, so
  timestamps cannot order events written in one transaction.
- `accounts` — with a `CHECK` on `account_type` generated from
  `ACCOUNT_TYPES`, so the database and the Python validator cannot drift.
- `transactions` — carries its own `sequence` identity column for the
  same ordering reason; all listings sort by it, which also keeps
  pagination stable. A `pg_trgm` GIN index serves the description search.
- `ledger_entries` — `Numeric(18,2)`, `CHECK`s on side and positivity, and
  `position`, each entry's place in its transaction as submitted, which is
  the order entries are shown in (`NULL` for rows written before it).
- `idempotency_keys` — key (primary key, `idempotency_keys_pkey`), request
  hash, stored response; `created_at` is indexed for the retention cleanup.

Four triggers enforce the ledger's rules in Postgres itself, for writes
that bypass the app as well as for the app:

- `events` refuses UPDATE, DELETE and TRUNCATE;
- a deferred constraint trigger refuses to commit a transaction whose
  entries do not balance per currency;
- an entry must carry its account's currency;
- an account's currency cannot change once created.

Indexes and triggers are declared in both the migrations *and*
`schema.py`, so `metadata.create_all` (used by the tests) and
`alembic upgrade head` produce the same schema. This was checked
directly: functions, triggers, indexes and constraints are byte-identical,
and `alembic check` finds no difference in CI.

### Domain — the invariants live here

`app/domain/ledger.py` is the part worth knowing:

- `assert_balanced` — debits and credits must net to zero **per
  currency**, not in aggregate. Summing USD into EUR produces a real
  number that means nothing.
- `assert_accounts_valid` — one query for all entries. An account's
  currency is fixed at creation and is the sole authority; an entry may
  not name a different one. Missing accounts and currency mismatches are
  collected and reported *together*, so a bad submission does not reveal
  its problems one resubmit at a time.
- `post_transaction` — writes `transactions`, `ledger_entries` and the
  `events` row, and deliberately **does not commit**. The caller owns the
  transaction boundary, which is what lets the idempotency check wrap it
  in the same database transaction.

### HTTP — six server-rendered pages

All in `app/main.py`, Jinja2 + Tailwind (CDN) on a shared `base.html`:
overview with per-account balances and normal-side signs, paginated
event log, filterable + paginated transaction list, posting form,
transaction detail with debit/credit columns, account creation. Plus
`/health`, which reports `degraded` rather than raising. The
`/transactions` date filters are UTC days, matching the UTC timestamps
the pages show, whatever time zone the database session uses.

### JSON API — four endpoints

`POST /api/accounts`, `GET /api/accounts`, `POST /api/transactions`
(requires `Idempotency-Key`; `201` first post, `200` replay with
`Idempotent-Replayed: true`, `409` on reuse, `400` without a valid key)
and `GET /api/transactions/{id}`. They live in `app/api/` and call the
same domain functions as the pages; the shared read queries moved to
`app/domain/reads.py`. Errors share `{"error": {"code", "message"}}`
under `/api/` only, amounts are strings in and out, and the fingerprint
compares requests by meaning rather than bytes (`ARCHITECTURE.md` §4 and
§5.16 have the reasoning). `tests/test_api.py` has 33 tests, 22 of them
database-free, including concurrent duplicate requests; each was checked
to fail when the code it covers is broken.

Found on the way and fixed for the forms too: an amount with a third
decimal place was silently rounded on storage (`100.005` stored as
`100.01`), one that rounded to zero was a 500, and so was a description
over 512 characters. All three are now `422`s.

### Idempotency — correct under concurrency, with retention

Every posting carries a `submission_key`. A replay with the same key
returns the original transaction id; a *different* body under the same
key is a **409**, not a silent overwrite.

`app/domain/idempotency.py` claims the key with `INSERT … ON CONFLICT DO
NOTHING` *before* posting. The earlier SELECT-then-INSERT version never
double-posted, but concurrent duplicates got a 500 instead of the
original result: reproduced as `[500, 302, 500, 500, 500]` for five at
once. Now all five get the same 302. A rejected attempt releases the
key.

`python -m scripts.prune_idempotency_keys --yes` deletes keys over 30
days old. Past that window a retry is no longer recognised and posts
again; the function refuses windows under a day.

### Observability — structured logs

JSON lines on the `keel` logger, each tagged with a request id that is
echoed as `X-Request-ID`. Posting lines carry `transaction_id`,
`idempotency_key` and `account_ids`. No metrics, no tracing.

### Dependencies

FastAPI 0.141.1 on Starlette 1.7.0 (pinned directly), pytest 9 with
pytest-asyncio 1.4, python-dotenv 1.2.2, jinja2 3.1.6. Moving off
Starlette 0.38.6 closed all 7 of its advisories and needed no
application change. Starlette 1.x also imports `python_multipart`
directly, so form parsing no longer goes through the deprecated
`import multipart` shim.

### CI — two jobs

`lint-and-test` (ruff, `alembic check` on a freshly migrated database,
then pytest against a live Postgres service) and
`docker-smoke`, which is the more interesting one: it builds the image,
waits for `/health`, asserts the exact healthy body `{"status":"ok","db":"up"}`
(a bare 200 check would go green on a stack whose migrations failed),
confirms `alembic_version` was actually stamped, then drives a real
account-creation → posting → overview flow through the container. It
pins `COMPOSE_FILE` so it cannot accidentally pick up the dev bind-mount
and test host code instead of the image. The dev bind mount itself sets
`create_host_path: false`, so a checkout without `./app` refuses to start
instead of mounting an empty directory over the image.

---

## 3. Rebuildability — complete

`app/domain/rebuild.py`'s `rebuild_read_model()` replays the log into a
fresh read model, and `python -m scripts.rebuild_read_model --yes` runs it
from the command line, reporting any balance it corrected. Tests show a
rebuild reproduces the ledger exactly, repairs a deliberately corrupted
read model, is repeatable, and is safe alongside a concurrent posting,
which waits on the rebuild's lock and is not lost.

The last gap, databases whose accounts predate `account.created`, is
closed. `python -m scripts.backfill_account_events --yes` appends the
missing events, and the rebuild script refuses such a log up front with a
pointer to it. Replay takes account events before the rest, because
backfilled ones sit later in the log than the transactions that use their
accounts. Entries keep their submission order through a rebuild, old
events included, and every payload's `schema_version` is checked, a
missing one read as version 1. `ARCHITECTURE.md` §3.3 has the details and
the one remaining limit: entry ids are not reproduced.

---

## 4. Where to start building

Ordered so that earlier items unblock or de-risk later ones.

**1. A live deployment**
Nothing is hosted, so there is no URL to click without cloning the repo.
This means a hosted demo instance, not a deployment pipeline, which is
out of scope (below).
The image-only `docker-compose.yml` is what a deployment would run. This
needs a hosting decision, and whatever host is chosen also needs to run
`scripts.prune_idempotency_keys` on a schedule.

**2. Authentication**
Every route is public. Fine for a demo, disqualifying otherwise.

**3. Round out the JSON API**
No single-account read, no transaction listing, no pagination and no
event-log endpoint yet (`ARCHITECTURE.md` §8 item 5).

**Done since the previous version of this list:** stable entry order
(each entry's submission position, stored and replayed), payload schema
versions, `alembic check` in CI with the duplicate idempotency-key
constraint removed, the JSON API, the
`account.created` backfill, the account-currency rule in the database, idempotency key
retention, UTC date filtering, the trigram search index, the
FastAPI/Starlette upgrade, and the test fixtures' guard against migrated
databases.

**Future work, not started:** FX handling (needs a clearing-account
pattern plus an FX gain/loss account — see `ARCHITECTURE.md` §2.4 for why
the current model *cannot* express conversion), and metrics and tracing.
Both are deferred, not ruled out.

**Out of scope:** webhook ingestion, multi-provider payment orchestration,
reconciliation, the outbox pattern and a deployment pipeline. These are
the layers a payments platform puts around a ledger; they mark the
project's boundary and are not planned (`ARCHITECTURE.md` §8).

---

## 5. Running it

```bash
# full stack (migrations run automatically on boot)
docker compose up --build

# demo data — writes through the domain layer, so the event log is
# populated too and the event-log page has something to show
docker compose exec app python -m scripts.seed_demo_data
```

**To actually run the test suite**, give it a database — without this
you are running 48 of 134 tests:

```bash
docker compose up -d db
docker compose exec db createdb -U ledger ledger_test
export TEST_DATABASE_URL=postgresql+asyncpg://ledger:ledger@localhost:5432/ledger_test
pytest -v
```

The fixture **drops and recreates every table**, so point it only at a
scratch database. It refuses to run on a database alembic has migrated:
`alembic_version` is not in `metadata`, so the drop would leave that
database stamped at head with no tables, and a later `alembic upgrade
head` would silently do nothing. `tests/support.py` holds the check.

A note on ports: the compose database is on **5432**. This machine also
has a native PostgreSQL 16 on **5433**, which accepts `postgres`/`postgres`.
Its `keel_test` database is what the 2026-09-24 and 2026-09-27 test runs
used, since Docker was not running. That server's session time zone is
`Asia/Calcutta`, which is what surfaced the date-filter bug:

```bash
export TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5433/keel_test
```

Maintenance tasks (each only reports without `--yes`):

```bash
docker compose exec app python -m scripts.rebuild_read_model --yes
docker compose exec app python -m scripts.backfill_account_events --yes
docker compose exec app python -m scripts.prune_idempotency_keys --yes
```

---

## 6. Design assets

`stitch_keel_ledger_audit_console/` holds a generated design kit —
`DESIGN.md` (a full Material-style colour and typography token set,
"Audit Ledger Protocol") plus per-screen `code.html` and `screen.png`
mockups for the overview, event log, posting voucher, T-account detail
and logo.

**The live templates do not use these tokens.** They are plain Tailwind
utility classes. The kit is a reference for a future visual pass, not a
system the app currently implements — worth knowing before assuming the
mockups describe what renders.
