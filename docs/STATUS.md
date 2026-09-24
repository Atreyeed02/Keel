# Keel — Build Status & Handoff

**As of 2026-09-22.** A snapshot of what is actually built, what is
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
| `pytest` collection | **27 tests** |
| `pytest` (no database available) | 8 passed, 19 skipped |
| Docker daemon | not running on this machine at time of writing |

The 19 skips are not failures. Every page-level test is Postgres-backed
and skips itself unless `TEST_DATABASE_URL` is set — so **a green local
run of 8 tests means roughly a third of the suite actually executed.**
Do not read it as a passing build. See §5 for the command that runs the
real thing.

> **Drift, now corrected:** `ARCHITECTURE.md` §7 previously said "12
> tests" — the suite had grown to 27 without the count following it (the
> transaction-list, filtering, pagination and sequence-ordering tests
> came later). §7 now records 27 and spells out the skip behaviour. The
> README's claim that the read model is "always rebuildable" was
> likewise softened to match §3.3; see §3 below.

---

## 2. What exists

### Schema — complete, 4 migrations

Five tables in `app/db/schema.py`, with `alembic/versions/` at head
`fca143a4d6d9`:

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

Indexes are declared in both the migration *and* `schema.py`, so
`metadata.create_all` (used by the tests) and `alembic upgrade head`
produce the same schema.

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

### Idempotency — wired, including the hard case

Every posting carries a `submission_key`. A replay with the same key
returns the original transaction id; a *different* body under the same
key is a **409**, not a silent overwrite.

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

## 3. Claimed but not yet true

One item deserves singling out, because the README states it as fact:

> "the read model is always rebuildable from the event log"

**The data supports this. No code does it.** The event payload contains
every entry with account id, side, amount and currency — so a replay is
*possible* — but there is no function that reads `events` and
reconstructs `transactions` / `ledger_entries`, and nothing tests that a
rebuild reproduces the current state. Today it is a property of the
schema layout, not a capability of the system.

This is the single largest gap between what the project says about
itself and what it does.

---

## 4. Where to start building

Ordered so that earlier items unblock or de-risk later ones.

**1. Replay / rebuild (`app/domain/` — new module)**
Write `rebuild_read_model(conn)`: truncate `transactions` and
`ledger_entries`, read `events` ordered by `sequence`, re-apply each
`transaction.posted` payload. The test that makes it real: seed a
ledger, snapshot the read model, rebuild, assert identical. This turns
§3's claim into a tested capability and is the natural foundation for
anything event-sourced that comes after.

**2. Balance enforcement in the database**
The per-transaction balance rule is enforced only in application code.
A deferred constraint trigger checking `sum(debits) = sum(credits)` per
`transaction_id` at commit makes a half-written transaction impossible
even from outside the app — a migration, a manual `INSERT`, a future
importer. The schema comments already flag this as the intended v2 step.

**3. An HTTP API alongside the pages**
Everything today is form-posted HTML. The domain layer is already clean
enough to expose directly — `post_transaction` takes `EntryInput` and a
connection, nothing more. A JSON `POST /api/transactions` reusing the
same idempotency wrapper is mostly wiring, and it is what makes the
service consumable by anything other than a browser.

**4. Idempotency key retention**
`idempotency_keys` grows without bound. No TTL, no cleanup job.

**5. Authentication**
Every route is public. Fine for a demo, disqualifying otherwise.

**Not started at all:** FX handling (needs a clearing-account pattern
plus an FX gain/loss account — see `ARCHITECTURE.md` §2.4 for why the
current model *cannot* express conversion), webhook ingestion, the
outbox pattern, reconciliation, structured logging and metrics.

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
you are running 8 of 27 tests:

```bash
docker compose up -d db
export TEST_DATABASE_URL=postgresql+asyncpg://ledger:ledger@localhost:5432/ledger
pytest -v
```

The fixture **drops and recreates every table**, so point it only at a
database you do not mind losing.

A note on ports: use the compose database on **5432**. A separate
Postgres exists on 5433 on this machine with no usable password — it is
referenced in some older `.claude/settings.json` permission entries and
is a dead end.

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
