# How Keel works

The design in brief: what Keel guarantees and where each guarantee is
enforced, why it is built on an event log and double-entry, how
idempotency and rebuilds work, the layers, the tables, and what it does
not do yet. [ARCHITECTURE.md](ARCHITECTURE.md) is the full walkthrough,
file by file.

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
[docs/ARCHITECTURE.md §4](ARCHITECTURE.md#4-idempotency).

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
[§3.3](ARCHITECTURE.md#33-rebuildability-tested-with-one-known-limit).

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
[§5.2](ARCHITECTURE.md#52-appdbschemapy--the-tables).

## Limitations and future work

What this does **not** do today. The full, maintained list is
[docs/ARCHITECTURE.md §8](ARCHITECTURE.md#8-what-still-needs-doing).

- **The JSON API is minimal.** No single-account read, no transaction
  listing, no pagination, no event-log endpoint.
- **No authentication or authorisation.** Every route is public, which
  is also why rebuild is a CLI and not a route. For the public demo, writes
  are bounded instead: a per-client rate limit, caps on accounts and
  transactions, and a reset.
- **Rebuild is all-or-nothing and in memory.** No snapshots, no
  incremental projection catch-up. Postings wait while a rebuild runs.
- **The demo reset depends on GitHub's scheduler.** A daily workflow runs
  `scripts.reset_demo_data`, and GitHub pauses scheduled workflows in a
  public repository after 60 days without activity. Key pruning has no
  scheduler: the demo doesn't need one, but a ledger that isn't reset does.
- **One instance.** Migrating on start and the in-memory rate limit both
  assume it.
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
