# Keel — Build Status & Handoff

**As of 2026-09-24.** A snapshot of what is actually built, what is
verified, and where the next piece of work starts. For the *why* behind
the design — the accounting concepts, the event-sourcing rationale, a
file-by-file walkthrough — read `ARCHITECTURE.md` first; this document
does not repeat it.

---

## 1. Verified state, right now

Everything below was checked against the working tree, not read off the
older prose in `ARCHITECTURE.md`.

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `pytest` collection | **62 tests** |
| `pytest` (no database available) | 17 passed, 45 skipped |
| `pytest` (local Postgres 16) | 62 passed |
| `alembic upgrade head` → `downgrade base` → `upgrade head` | clean, on a scratch database |
| App booted with uvicorn on a migrated database | health, posting, retry, 409 and rebuild CLI all verified over HTTP |
| `docker compose config` (base, and base + override) | valid |
| Docker image build | **not run**: the Docker daemon was not running on this machine |

The 45 skips are not failures. Every database-backed test skips itself
unless `TEST_DATABASE_URL` is set, so **a green local run of 17 tests
means barely a quarter of the suite actually executed.** Do not read it
as a passing build. See §5 for the command that runs the real thing.

> **Drift, now corrected:** `ARCHITECTURE.md` §7 said the "409 on key
> reuse" was covered by a test. No such test existed.
> `tests/test_idempotency.py::test_same_key_with_a_different_payload_is_a_409`
> now covers it.

---

## 2. What exists

### Schema — complete, 5 migrations

Five tables in `app/db/schema.py`, with `alembic/versions/` at head
`7d2e4b9c1a58`:

- `events` — append-only log. `sequence` is `BigInteger Identity(always=True)`,
  so the log has a real database-generated total order that the
  application cannot supply or fudge. This matters more than it looks:
  Postgres evaluates `CURRENT_TIMESTAMP` at *transaction start*, so
  timestamps cannot order events written in one transaction.
- `accounts` — with a `CHECK` on `account_type` generated from
  `ACCOUNT_TYPES`, so the database and the Python validator cannot drift.
- `transactions` — carries its own `sequence` identity column for the
  same ordering reason; all listings sort by it, which also keeps
  pagination stable.
- `ledger_entries` — `Numeric(18,2)`, `CHECK`s on side and positivity.
- `idempotency_keys` — key, request hash, stored response.

Two triggers enforce the ledger's rules in Postgres itself: `events`
refuses UPDATE, DELETE and TRUNCATE, and a deferred constraint trigger
refuses to commit a transaction whose entries do not balance per
currency. Both hold for writes that bypass the app.

Indexes and triggers are declared in both the migration *and*
`schema.py`, so `metadata.create_all` (used by the tests) and
`alembic upgrade head` produce the same schema. For the triggers this was
checked directly: the function bodies and trigger definitions are
byte-identical.

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
`/health`, which reports `degraded` rather than raising.

### Idempotency — correct under concurrency

Every posting carries a `submission_key`. A replay with the same key
returns the original transaction id; a *different* body under the same
key is a **409**, not a silent overwrite.

`app/domain/idempotency.py` claims the key with `INSERT … ON CONFLICT DO
NOTHING` *before* posting. The earlier SELECT-then-INSERT version never
double-posted, but concurrent duplicates got a 500 instead of the
original result: reproduced as `[500, 302, 500, 500, 500]` for five at
once. Now all five get the same 302. A rejected attempt releases the
key.

### Observability — structured logs

JSON lines on the `keel` logger, each tagged with a request id that is
echoed as `X-Request-ID`. Posting lines carry `transaction_id`,
`idempotency_key` and `account_ids`. No metrics, no tracing.

### CI — two jobs

`lint-and-test` (ruff + pytest against a live Postgres service) and
`docker-smoke`, which is the more interesting one: it builds the image,
waits for `/health`, asserts the exact healthy body `{"status":"ok","db":"up"}`
(a bare 200 check would go green on a stack whose migrations failed),
confirms `alembic_version` was actually stamped, then drives a real
account-creation → posting → overview flow through the container. It
pins `COMPOSE_FILE` so it cannot accidentally pick up the dev bind-mount
and test host code instead of the image.

---

## 3. Rebuildability — closed on 2026-09-24

This section used to record that "the read model is rebuildable from the
event log" was a claim with no code behind it. It now has both halves:
account creation appends `account.created` (previously the log contained
no accounts at all, so no replay could have worked), and
`app/domain/rebuild.py`'s `rebuild_read_model()` replays the log, with a
Postgres-backed round-trip test. `ARCHITECTURE.md` §3.3 has the details
and the two known limits — entry ids are not reproduced, and databases
with accounts created before the event existed cannot be rebuilt until
those accounts are backfilled into the log.

Since then: `python -m scripts.rebuild_read_model --yes` runs it from the
command line and reports any balance it corrected. Tests now also show
that a rebuild repairs a deliberately corrupted read model, is
repeatable, and is safe alongside a concurrent posting, which waits on
the rebuild's lock and is not lost.

---

## 4. Where to start building

Ordered so that earlier items unblock or de-risk later ones.

**1. Backfill `account.created` for pre-existing accounts**
Replay now exists (§3). Any database with accounts created before
2026-09-24 has no events for them and fails a rebuild on the foreign
key. A one-off migration or script that appends an `account.created`
event for each account lacking one closes that.

**2. An HTTP API alongside the pages**
Everything today is form-posted HTML. The domain layer is already clean
enough to expose directly: `post_transaction_once` takes a connection, a
key, a fingerprint and `EntryInput`s, nothing HTTP-shaped. A JSON
`POST /api/transactions` taking an `Idempotency-Key` header is mostly
wiring, and it is what makes the service consumable by anything other
than a browser.

**3. Account-currency rule in the database**
The balance rule now has a database backstop; the rule that an entry
carries its account's currency does not. A trigger comparing
`ledger_entries.currency` with `accounts.currency` closes it.

**4. Idempotency key retention**
`idempotency_keys` grows without bound. No TTL, no cleanup job.

**5. Authentication**
Every route is public. Fine for a demo, disqualifying otherwise.

**Done since the previous version of this list:** balance enforcement in
the database (deferred constraint trigger, migration `7d2e4b9c1a58`) and
structured logging.

**Not started at all:** FX handling (needs a clearing-account pattern
plus an FX gain/loss account — see `ARCHITECTURE.md` §2.4 for why the
current model *cannot* express conversion), webhook ingestion, the
outbox pattern, reconciliation, metrics and tracing.

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
you are running 17 of 62 tests:

```bash
docker compose up -d db
export TEST_DATABASE_URL=postgresql+asyncpg://ledger:ledger@localhost:5432/ledger
pytest -v
```

The fixture **drops and recreates every table**, so point it only at a
database you do not mind losing.

A note on ports: the compose database is on **5432**. This machine also
has a native PostgreSQL 16 on **5433**. An earlier version of this note
called it unusable; that was wrong. It accepts `postgres`/`postgres`,
and its `keel_test` database is what the 2026-09-24 test runs used,
since Docker was not running:

```bash
export TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5433/keel_test
```

Rebuild the read model from the log (without `--yes` it only reports):

```bash
docker compose exec app python -m scripts.rebuild_read_model --yes
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
