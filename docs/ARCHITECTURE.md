# Keel — Architecture and Concepts

A complete walkthrough of what this service is, the accounting and
event-sourcing ideas it is built on, what every file does, and what is
still missing.

Last brought up to date on 2026-09-24.

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
that creates or destroys value: the code rejects it before the database
is touched, and the database itself refuses to commit one even from a
writer that bypasses the code (§5.2).

**History is immutable.** Nothing in the log is ever updated in place.
Every change is appended to an `events` log, which Postgres refuses to
UPDATE, DELETE or TRUNCATE. The tables you query for balances are a
*derived projection* of that log, not the truth itself.

The contrast this is built against: a naive ledger stores a `balance`
column on each account and does `UPDATE balance = balance + 100`. That
works until a bug, a race, or a replayed webhook corrupts it — and then
there is no way to reconstruct how the balance became wrong, because the
history was never kept.

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
  ├─ id, sequence, aggregate_type, aggregate_id
  ├─ event_type ("account.created" | "transaction.posted")
  └─ payload (JSONB — the account, or the full transaction as submitted)

accounts                    ← derived read model
transactions                ← derived read model
ledger_entries              ← derived read model
```

The **event log** records *what happened*, as it happened, forever. It is
never updated and never deleted. `app/db/schema.py` states the rule
explicitly:

```python
# Append-only: no updated_at, no soft-delete flag. If it's wrong,
# a compensating event gets appended, not a mutation.
```

For a long time that comment was the only enforcement. Migration
`7d2e4b9c1a58` added a statement-level trigger, `events_append_only`,
that raises on any UPDATE, DELETE or TRUNCATE of `events`, so the rule
now holds for a `psql` session or a stray migration too. It is
statement-level because TRUNCATE fires no row triggers. DROP TABLE is
not blocked: that is a schema change, not a rewrite of history.

The **read model** (`accounts` + `transactions` + `ledger_entries`) is
shaped for fast queries — balances, transaction detail, account listings.
It is *derived* information: it can be deleted entirely and rebuilt by
replaying every event in order, and `rebuild_read_model()` does exactly
that (§3.3).

### 3.2 Why both are written in one transaction

`post_transaction()` writes the event **and** the read-model rows inside
the caller's database transaction (`create_account_record()` does the
same for accounts, appending `account.created`):

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
`engine.begin()` and calls `post_transaction_once` (§4), which claims the
idempotency key, calls `post_transaction` and records the result. Only
then does the handler commit, so all of it lands atomically or none of it
does. `tests/test_ledger_invariants.py` checks this directly: a failure
raised *after* `post_transaction` has written its rows, but before
commit, leaves no transaction, no entry and no event behind.

### 3.3 Rebuildability: tested, with one known limit

`app/domain/rebuild.py` provides `rebuild_read_model(conn)`. It truncates
`ledger_entries`, `transactions` and `accounts` in one `TRUNCATE ...
RESTART IDENTITY` (listing all three at once is what makes it FK-safe),
then replays `events`: every `account.created` first, then everything
else, each group in `sequence` order (why accounts go first is under
*Accounts from before the event*, below):

| `event_type` | Replay |
|---|---|
| `account.created` | insert into `accounts` with `id = aggregate_id` |
| `transaction.posted` | insert into `transactions` with `id = aggregate_id`, then one `ledger_entries` row per `payload["entries"]` item |
| anything else | raise `UnknownEventError` — a rebuild that silently skipped an event would disagree with the log |

Account and transaction ids are the events' `aggregate_id`s, so they come
back unchanged: every entry still points at the right account, and
anything holding a transaction id (a detail-page URL, a stored
idempotency response) still resolves. Each row takes its event's
`created_at`, which is the value it originally had — row and event were
written in one database transaction and Postgres `now()` is
transaction-start time.

```
events (append-only, ordered by sequence)
  │  seq 1  account.created     aggregate_id = <cash id>
  │  seq 2  account.created     aggregate_id = <revenue id>
  │  seq 3  transaction.posted  aggregate_id = <txn id>  payload: entries[]
  │  ...
  ▼
rebuild_read_model(conn)            one database transaction
  1. TRUNCATE ledger_entries, transactions, accounts RESTART IDENTITY
  2. SELECT * FROM events ORDER BY (event_type <> 'account.created'), sequence
  3. for each event:
       account.created    → INSERT accounts
       transaction.posted → INSERT transactions + ledger_entries
       anything else      → raise UnknownEventError (whole replay rolls back)
  ▼
accounts / transactions / ledger_entries      the rebuilt projection
  (balance trigger re-checks every replayed transaction at commit)
```

It follows the `post_transaction` contract: it issues statements, does
not commit and does not open its own transaction. A replay that fails
part-way rolls back with the caller's transaction and the old read model
survives. It is deliberately **not** exposed over HTTP; who may trigger a
whole-database rewrite is a separate decision.

**Running it.** `python -m scripts.rebuild_read_model` reports the current
row counts and changes nothing. With `--yes` it replays, then prints the
counts before and after plus every account whose balance the rebuild
changed. On a healthy ledger that list is empty. A non-empty list means
the projection had drifted from the log (a row deleted or edited around
the app) and the rebuild corrected it.

**Rebuilding while the app serves.** The `TRUNCATE` takes an ACCESS
EXCLUSIVE lock on the three read-model tables and holds it until the
rebuild commits. A posting that arrives mid-rebuild therefore blocks on
its first read of `accounts`, then continues against the rebuilt tables.
Its event is appended after the replay's snapshot of the log, so it is
neither replayed twice nor lost. The cost is that requests wait for as
long as the replay takes.

**What proves it.** All in `tests/test_rebuild.py`, Postgres-backed, so CI
runs them on every push:

- `test_rebuild_reproduces_the_read_model` builds a ledger through the
  demo seed, `POST /accounts` and `POST /post-transaction` (all five
  account types, three currencies, one transaction that moves two
  currencies at once, one with no description), snapshots it, rebuilds,
  and requires every account, transaction (in posting order), entry,
  per-account balance, per-currency total and the rendered `/` and
  `/transactions` pages to be identical.
- `test_rebuild_repairs_a_corrupted_read_model` damages the projection
  around the app (a whole transaction's rows deleted, an account renamed,
  an account row no event created), checks that the damage is visible,
  then requires the rebuild to restore the original snapshot exactly.
- `test_rebuild_is_repeatable_and_posting_continues_after_it`: two
  rebuilds in a row give identical results, and a posting made afterwards
  continues the renumbered `transactions.sequence` and lists first.
- `test_a_posting_during_a_rebuild_waits_for_it_and_is_not_lost` holds a
  rebuild uncommitted, starts a posting, and watches `pg_stat_activity`
  until that posting is waiting on a lock. It then commits, and requires
  the posting to succeed and to survive a further rebuild.
- `test_rebuild_script_refuses_without_confirmation_then_repairs` covers
  the CLI.
- `test_backfill_makes_a_legacy_ledger_rebuildable` and
  `test_backfill_and_rebuild_scripts_on_a_legacy_ledger` cover the
  backfill, below.

**Accounts from before the event.** Until `account.created` was
introduced, account creation appended nothing, so an older database has
accounts with no event. Replaying its log would fail on the
`ledger_entries.account_id` foreign key, and an unused account would
silently vanish. `backfill_account_events(conn)`
(`python -m scripts.backfill_account_events --yes`) appends an
`account.created` for every account without one, recorded as the read
model describes it now. `scripts/rebuild_read_model.py` checks for
accounts the log uses but never creates, and refuses up front with a
pointer to the backfill instead of failing part-way.

Two details make the backfilled log replay correctly:

- A backfilled event is appended *now*, so its `sequence` is later than
  the transactions that use the account. That is why replay takes
  account events first. Creating an account depends on nothing and
  nothing but creation happens to one, so for a log with no backfilled
  events this gives the same result as strict order. With strict order
  the backfill test fails on the foreign key; that was checked.
- The event's own `created_at` is when it was appended, which keeps the
  log honest. The account's original `created_at` travels in the payload,
  with `"backfilled": true`, and replay restores it from there.

**The limit.**

- **`ledger_entries.id` is not reproduced.** The `transaction.posted`
  payload never carried entry ids, so replay mints new ones. Nothing
  depends on a particular value: no foreign key references
  `ledger_entries`, and no query or template looks an entry up by id. The
  one visible effect is ordering — the transaction-detail page sorts a
  transaction's entries by `(created_at, id)`, all of a transaction's
  entries share `created_at`, so their order within the debit and credit
  columns is decided by the random id and can change across a rebuild.
  (It was already arbitrary rather than submission order.)

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

The logic lives in `app/domain/idempotency.py`. `request_fingerprint()`
hashes the request as submitted:

```python
hashlib.sha256(
    json.dumps({"description": description, "entries": raw_entries}, sort_keys=True).encode()
).hexdigest()
```

`sort_keys=True` matters: it makes the JSON serialisation deterministic,
so the same logical request always hashes identically. The hash is over
the raw form values, so `"100"` and `"100.00"` are different requests.
That is deliberately strict: a client reusing a key should be resending
the same bytes.

Then `post_transaction_once()`, inside the handler's transaction,
**claims the key before posting anything**:

```sql
INSERT INTO idempotency_keys (key, request_hash) VALUES (...)
ON CONFLICT (key) DO NOTHING
RETURNING key
```

- **The claim succeeds** → this request owns the key. Post the
  transaction, store its id on the key row, commit.
- **The claim conflicts, hash matches** → a genuine retry. Return the
  *original* transaction id without posting again.
- **The claim conflicts, hash differs** → the same key was reused for a
  *different* request. `409 Conflict`: this is a client bug, and silently
  accepting it would hide it.

**Why claim first.** The earlier version looked the key up with a plain
`SELECT` and only inserted it after posting. Under concurrency that has a
gap: two requests with the same key can both see "no such key" and both
post. The primary key on `idempotency_keys` still stopped the second one
committing, so the ledger never double-posted. But that request died with
an unhandled `IntegrityError`, a 500, when it should have been answered
with the original result. Reproduced before the fix: five concurrent
duplicates returned `[500, 302, 500, 500, 500]`.

Claiming first closes the gap because of how Postgres handles a
unique-index conflict with a row another transaction has not committed
yet: the second `INSERT` **waits** for the first transaction to end.

```
request A                               request B (same key)
─────────                               ────────────────────
BEGIN                                   BEGIN
INSERT key … ON CONFLICT DO NOTHING
  → claimed                             INSERT key … ON CONFLICT DO NOTHING
post_transaction(...)                     → blocks on A's uncommitted key row
UPDATE key SET response_body = …                    │
COMMIT ─────────────────────────────────────────────┘
                                          → conflict: DO NOTHING
                                        SELECT key   (new snapshot: sees A's row)
                                          hash matches → A's transaction id
                                        COMMIT  (wrote nothing)
```

If A rolls back instead (its entries named a nonexistent account, say),
its claim vanishes with it, B's insert succeeds, and B posts. A rejected
attempt never burns the key. The claim, the posting and the stored result
share one database transaction, so no other request can ever observe a
key that is claimed but has no result.

The form supplies the key via a hidden `submission_key` field generated
when the page is rendered, so a double-submit (double-click, browser
refresh) carries the same key and collapses into one posting.

**What proves it.** `tests/test_idempotency.py`:

| Test | Case |
|---|---|
| `test_retry_writes_the_ledger_exactly_once` | same key, same payload, retried: one transaction, two entries, one event |
| `test_same_key_with_a_different_payload_is_a_409` | conflicting payload: 409, stored result untouched |
| `test_a_rejected_attempt_does_not_consume_the_key` | a 422 releases the key; the fixed resubmission posts |
| `test_concurrent_duplicates_commit_one_effect` | five simultaneous duplicates: all 302 to the same transaction |
| `test_concurrent_conflicting_payloads_post_one_and_reject_the_other` | simultaneous conflicting payloads: one 302, one 409, never a mix |
| `test_fingerprint_is_deterministic_and_strict` | the hash ignores key order and nothing else |
| `test_pruning_deletes_only_expired_keys_and_keeps_the_ledger` | retention removes old keys only; transactions and events stay |
| `test_a_pruned_key_no_longer_recognises_its_retry` | past the window, a retry posts again: the trade-off, pinned down |
| `test_pruning_refuses_a_window_under_a_day` | the retention floor |
| `test_prune_script_counts_then_deletes` | the CLI |

The concurrent tests are deterministic. An `asyncio.Barrier` holds every
request just before it touches the key and releases them together, so
all of them reach the check at the same moment. The two concurrent tests fail against the old
SELECT-then-INSERT code, with the 500s shown above.

**Retention.** `idempotency_keys` gains a row per posting.
`prune_idempotency_keys(conn, older_than)` deletes keys claimed longer
ago than `older_than`, measured on the database clock that stamped
`created_at` and backed by `ix_idempotency_keys_created_at` (migration
`9b1f6e2c8a47`). `python -m scripts.prune_idempotency_keys --yes` runs it
with a 30-day default, meant for a schedule. Transactions and events are
untouched. What goes is the ability to recognise a retry: resubmitting a
pruned key posts again. So the window must outlast any client's retry
window, and the function refuses anything under a day. A claim not yet
committed is invisible to the DELETE, so an in-flight posting never loses
its key.

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
├── domain/accounts.py account input validation + creation
├── domain/account_types.py  the closed set of account types
├── domain/rebuild.py  replays the event log into the read model (§3.3)
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

**`accounts`** — one row per `account.created` event.

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
single meaningful figure by construction. Unlike the balance invariant,
this is enforced only in the domain layer, not by a database constraint.

`account_type` carries a database `CHECK` (`ck_account_type_valid`,
added by migration `de4f1aec2fe6`) restricting it to the five types, so
a write that bypasses `app/domain/accounts.py` is rejected by Postgres
rather than silently mis-signing a balance. `app/db/schema.py` generates
the constraint from the `ACCOUNT_TYPES` tuple in
`app/domain/account_types.py` — the same tuple the validator checks — so
the two cannot drift.

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

**The balance rule is enforced twice.** `assert_balanced` in
`app/domain/ledger.py` checks it before anything is written, and that is
what turns a bad form submission into a readable error. Behind it, since
migration `7d2e4b9c1a58`, sits a constraint trigger,
`ledger_entries_balanced`, which holds for every writer: a migration, a
manual `INSERT`, a future importer.

- It is `DEFERRABLE INITIALLY DEFERRED`, so it runs at **commit**. A
  transaction's entries arrive one row at a time and only balance once
  the last one is in.
- It sums per `(transaction_id, currency)`, so 100 USD against 100 EUR is
  rejected just as `assert_balanced` rejects it.
- An UPDATE re-checks both the old and the new `transaction_id`, because
  moving an entry unbalances the transaction it left.
- It fires per row, so a four-entry transaction runs four checks at
  commit, each an index lookup on `ix_ledger_entries_transaction_id`.
- Its error names the transaction, the amount it is off by and the
  currency, with SQLSTATE `23514` (`IntegrityError` in SQLAlchemy).
- What it cannot catch: a `transactions` row with **no** entries at all,
  because there is no entry row for it to fire on. Such a row moves no
  money, so balances are unaffected.

**So is the account-currency rule.** `assert_accounts_valid` refuses an
entry whose currency differs from its account's, with a readable error.
Since migration `3c9e5a7b2d14`, two triggers hold the rule for every
other writer:

- `ledger_entries_match_account_currency`, a `BEFORE INSERT OR UPDATE`
  row trigger, looks the entry's account up and refuses a mismatch. An
  entry naming a nonexistent account is left to the foreign key, which
  reports that more precisely.
- `accounts_currency_immutable` refuses an UPDATE that changes an
  account's currency. Checking entries alone would miss this: rewriting
  the account would put every entry already on it in violation without
  touching a single entry row. Renaming an account is still allowed.

All of these triggers are declared twice, in the migrations and in
`app/db/schema.py` (as `after_create` listeners, so `metadata.create_all`
in the tests builds them too), for the same reason the indexes are. The
two copies were checked to produce byte-identical function bodies and
trigger definitions.

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

Mirrors the shape of `ledger.py` — a Pydantic model, a validation entry
point, and a write function, all kept out of the route handler.

```python
ACCOUNT_TYPES = ("asset", "liability", "equity", "revenue", "expense")
```

An **ordered tuple**, not a set, because the `<select>` in the form
renders from it and a set has no stable order. It lives in its own
import-free module, `app/domain/account_types.py`, and `accounts.py`
re-exports it: `schema.py` builds its `CHECK` constraint from the tuple,
and `accounts.py` imports `schema.py` for its tables, so keeping the
constant in `accounts.py` would make the two import each other.

`AccountInput` normalises as it validates: `name` is stripped and capped
at 255 (matching the column width, so over-long input is a 422 rather
than a database error), `currency` is stripped and upper-cased and must
be three letters.

`InvalidAccountError(ValueError)` mirrors `UnbalancedTransactionError`.
`validate_account()` catches Pydantic's `ValidationError` and re-raises
this with a flattened one-line message, via the shared helper in
`app/domain/errors.py`:

```python
def describe_validation_error(exc: ValidationError) -> str:
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

`create_account_record(conn, account)` is the write, and has the same
contract as `post_transaction`: it inserts the `accounts` row, appends an
`account.created` event (`aggregate_type="account"`,
`aggregate_id=<account id>`, payload `name` / `account_type` /
`currency`), and does not commit. The event is what lets a rebuild
recreate the account under its original id (§3.3).

It lives in its own module rather than in `accounts.py` because the
transaction form needs it too — `submit_post_transaction` routes
`ValidationError` through the same helper, so a malformed amount renders
`amount input should be a valid decimal` instead of pydantic's dump.
`app/domain/errors.py` imports nothing but pydantic on purpose, so any
domain module can use it without pulling in the database layer.

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

**Setup.** Configures the JSON logger and registers the request-id
middleware (§5.15), creates the FastAPI app, mounts `/static`, points
Jinja2 at `templates/`, and registers a custom filter:

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

**`GET /transactions`** — the full transaction list, paginated on the same
page/page_size/total shape as `/event-log`. Rows come from
`_transaction_rows()`, the shared select the overview's "recent
transactions" table also builds on, so the two cannot drift into showing
different numbers for the same transaction. Optional `q` (description
`ILIKE`), `date_from` and `date_to` filters combine with AND, apply to the
count as well as the page, and are re-encoded into the pager links so
paging does not drop them. `date_to` is compared half-open against the
following day, because `created_at` is a timestamp and a bare `<= date_to`
would exclude everything after midnight on the closing day.

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
rather than silently truncating. Then validation, then
`post_transaction_once` (§4) inside `engine.begin()`. The handler maps
its outcomes to HTTP: `EntryAccountError` becomes the 422 form,
`IdempotencyConflictError` a 409, and success a 302. After the commit it
logs `transaction.posted` or `transaction.replayed`.

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
`fca143a4d6d9_transactions_sequence_identity_column.py` gives
`transactions` its own identity `sequence`, which every listing sorts by.
`7d2e4b9c1a58_enforce_append_only_events_and_balanced_entries.py` adds
the two triggers described in §5.2, and
`3c9e5a7b2d14_enforce_account_currency_in_the_database.py` the two
account-currency triggers. Their SQL is spelled out literally rather than
imported from `schema.py`, because a migration must keep producing the
DDL it produced the day it was written.
`9b1f6e2c8a47_index_idempotency_keys_created_at.py` backs the retention
cleanup (§4).
`5e8d2a1f9c63_trigram_index_on_transaction_descriptions.py` installs
`pg_trgm` and adds a GIN trigram index on `transactions.description`. The
`/transactions` search is `ILIKE '%term%'`, and a leading wildcard rules
out any btree index, so before this every search read the whole table.
On 100,000 rows the planner now uses the index unprompted: a bitmap
index scan, about 1.4 ms. `pg_trgm` is a trusted extension from
PostgreSQL 13 on, so the database owner can install it without superuser
rights. Downgrade drops the index and leaves the extension installed.

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
`validate_account`, `create_account_record` and `post_transaction` —
rather than by raw `INSERT`. Because the two write functions append the
`events` rows, a seeded database has the event log it would have had if a
human had typed everything into the app, and can be rebuilt from it. A
SQL dump would leave `events` empty and the event-log page blank.

It is **idempotent by refusal**: it counts `accounts` first and exits with
a message if any exist, so a second run cannot duplicate data. The whole
dataset is written inside one `engine.begin()` block.

**`scripts/rebuild_read_model.py`** is the operator entry point for §3.3.
Without `--yes` it reports row counts and exits non-zero having changed
nothing. With `--yes` it replays inside one transaction and reports what
changed. Either way it first refuses a log that names accounts it never
created.

**`scripts/prune_idempotency_keys.py`** deletes idempotency keys older
than the retention window (§4), 30 days unless `--older-than-days` says
otherwise. Without `--yes` it only counts them.

**`scripts/backfill_account_events.py`** appends `account.created` for
accounts that predate the event (§3.3). Without `--yes` it lists them;
with `--yes` it appends their events. On a database with nothing to
backfill it does nothing.

### 5.11 `tests/`

| File | Needs a DB | Covers |
|---|---|---|
| `test_ledger_domain.py` | no | the balance invariant, per-currency independence, amount/type validation |
| `test_health.py` | no | health endpoint always answers |
| `test_ledger_pages.py` | **yes** | idempotent retry, inline errors, account creation and its event, overview, transaction list filters (UTC day boundaries under any session time zone) and pagination, `sequence` ordering, the trigram index behind the search |
| `test_idempotency.py` | mostly | retries, 409 on key reuse, key release after a rejected attempt, concurrent duplicates and conflicting payloads, key retention and its CLI (§4) |
| `test_ledger_invariants.py` | **yes** | every database trigger (balance, account currency, append-only log) against writes that bypass the app; atomic rollback; log and read model agree; trial balance nets to zero |
| `test_rebuild.py` | **yes** | rebuild reproduces the read model, repairs a corrupted one, is repeatable, is safe alongside a concurrent posting; unknown event types and event-less accounts fail the replay; the backfill makes a legacy ledger rebuildable; both CLIs |
| `test_observability.py` | partly | request ids (generated, propagated, unsafe ones replaced), the JSON formatter, ledger identifiers on posting log lines |
| `test_schema_guard.py` | **yes** | the fixtures refuse to wipe a database alembic has migrated |

The Postgres-backed tests skip unless `TEST_DATABASE_URL` is set, then
create and drop the whole schema around each test for isolation. They do
that through `tests/support.py`'s `reset_schema`, which refuses to run on
a database with an `alembic_version` table: `metadata.drop_all` would
leave the stamp behind, and the database would claim to be at head with
no tables in it. They
drive the app in-process through `httpx.ASGITransport` — no real network
or server.

`pyproject.toml` sets `asyncio_mode = "auto"`, so `async def` tests run
without needing an explicit marker.

### 5.12 Docker

`Dockerfile` — `python:3.12-slim`, install requirements, copy `app/`,
`alembic/`, `alembic.ini` and `scripts/`, migrate-then-serve on start.

`.dockerignore` — keeps the build context to what the Dockerfile copies.
Note the `**/` prefixes: a bare `__pycache__` would only match one at the
context root, not `app/__pycache__` or `alembic/versions/__pycache__`.

`docker-compose.yml` — the canonical, production-shape config. A `db`
service (postgres:16-alpine) with a `pg_isready` healthcheck, and an `app`
service with `depends_on: condition: service_healthy`, so the app never
starts against a database that is not accepting connections yet. The `app`
service has its own healthcheck asserting the exact body
`{"status":"ok","db":"up"}` — a plain 200 check would pass on a container
whose migrations failed, since `/health` answers 200 with `"degraded"`
rather than raising. It is written in Python because `python:3.12-slim`
ships neither curl nor wget.

`docker-compose.override.yml` — local dev only: bind-mounts `./app` over
the image for live reload. Compose merges it automatically when present,
so `docker compose up` is dev mode by default. The consequence is that a
plain `docker compose up` is *not* running the shipped artifact, so
anything proving the image is self-contained must bypass it — the
`docker-smoke` job pins `COMPOSE_FILE: docker-compose.yml` for exactly
that reason. The mount uses the long syntax with `create_host_path:
false`: the short form would create a missing `./app` as an empty
directory and mount it over the image's code, where this form refuses to
start.

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

The job sets `COMPOSE_FILE: docker-compose.yml` at job level. `checkout`
puts `docker-compose.override.yml` on disk, and a bare `docker compose`
would merge it in and bind-mount the runner's `app/` over the image — so
the job would be testing the checkout, not the artifact, and a broken
`COPY app/ ./app/` would pass. Pinning it at job level rather than adding
`-f` to each step means a step added later inherits the same scoping.

The exact-body assertion is the point. Because `/health` returns 200 even
when the database is down (§5.6), a status-code check would go green over
a failed migration. Verified by stopping the db container: `/health` still
returned **HTTP 200** with `{"status":"degraded","db":"down"}`.

### 5.14 `app/domain/idempotency.py`

`request_fingerprint()` and `post_transaction_once()`, both described in
§4, plus `IdempotencyConflictError`. The code used to live inline in the
route handler. It moved out when the claim-first rewrite made it worth
testing on its own, and so that a future JSON endpoint can reuse it
rather than copy it. It takes the same "caller owns the transaction"
contract as `post_transaction`, and for the same reason: the claim only
protects anything if it commits atomically with the posting it guards.

### 5.15 `app/observability.py`

Structured logging, kept deliberately small: the standard library only,
no metrics, no tracing.

- `JsonFormatter` writes one JSON object per line. Fields passed with
  `extra=` become top-level keys, and the current request id is added
  automatically.
- `request_context_middleware` gives every request an id. It uses the
  client's `X-Request-ID` if that is 1–128 characters of
  `[A-Za-z0-9._-]`, otherwise it generates one. A supplied id goes
  straight into log lines, so one that could break them is not trusted.
  The id is kept in a `ContextVar` for the duration of the request,
  echoed back in the response's `X-Request-ID` header, and a
  `request.completed` line is logged with method, path, status and
  duration.
- `configure_logging()` attaches the handler to the `keel` logger only,
  with `propagate = False` so the lines stay out of uvicorn's own
  handlers. Level comes from `LOG_LEVEL` (default `INFO`).

What gets logged, always after the commit, so a line never describes a
write that rolled back:

| Event | Fields |
|---|---|
| `account.created` | `account_id`, `account_type`, `currency` |
| `transaction.posted` / `transaction.replayed` | `transaction_id`, `idempotency_key`, `entry_count`, `account_ids` |
| `transaction.rejected` | `idempotency_key`, `reason` |
| `idempotency.conflict` (warning) | `idempotency_key` |
| `request.completed` / `request.failed` | `method`, `path`, `status`, `duration_ms` |

The event's own id is not logged: `post_transaction` does not return it.
`transaction_id` is the event's `aggregate_id`, which is enough to find
it.

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
  → engine.begin(): create_account_record(conn, account)
                     ├─ uuid.uuid4() server-side
                     ├─ INSERT accounts
                     └─ INSERT events (account.created)
  → commit  (row and event together)
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
  → request_fingerprint(): sha256 of the canonical request JSON
  → engine.begin(): post_transaction_once(...)
        INSERT idempotency_keys ... ON CONFLICT (key) DO NOTHING
          ├─ conflict (waits for any in-flight holder of the key to finish)
          │    ├─ hash matches  → reuse stored transaction id
          │    └─ hash differs  → IdempotencyConflictError → HTTP 409
          └─ claimed → post_transaction(conn, entries, description)
                          ├─ assert_accounts_valid   (→ 422 on failure,
                          │                            claim rolls back)
                          ├─ INSERT transactions
                          ├─ INSERT ledger_entries (bulk)
                          └─ INSERT events   ← the source of truth
                       → UPDATE idempotency_keys SET response_body
     commit  (all of the above, atomically; the balance trigger runs here)
  → log transaction.posted | transaction.replayed
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
that writes the event log alongside the read model, account validation
and creation (which also writes an event), and `rebuild_read_model()`,
which replays the log into a fresh read model.

**HTTP layer** — health check, and six server-rendered pages: overview
with per-account balances and normal-side signs, event log with
pagination, the filterable paginated transaction list, transaction posting
form with live client-side totals, transaction detail with debit/credit
columns, and account creation.

**Database-enforced invariants** — `events` refuses UPDATE, DELETE and
TRUNCATE; a deferred constraint trigger refuses to commit any
transaction whose entries do not balance per currency; an entry must
carry its account's currency, and an account's currency cannot change
(§5.2).

**Idempotency** — claim-first, so it holds under concurrency as well as
for sequential retries. Includes the 409 on key reuse, and a rejected
attempt releases the key (§4).

**Rebuild tooling** — `python -m scripts.rebuild_read_model`, safe to run
alongside live postings, and `python -m scripts.backfill_account_events`
for databases whose accounts predate `account.created` (§3.3).

**Idempotency key retention** — `python -m scripts.prune_idempotency_keys`
deletes keys past a retention window, 30 days by default (§4).

**Structured logging** — JSON lines on the `keel` logger with a
per-request id, echoed as `X-Request-ID` (§5.15).

**Templates** — shared `base.html`; the four original pages were
refactored onto it with rendered output verified byte-identical, and
every page added since was built on it directly.

**Seed data** — `python -m scripts.seed_demo_data`, domain-layer-driven
and idempotent.

**Testing** — 75 tests (§5.11). 18 run with no database at all: the
balance invariant, entry input validation, the error-aggregation helper,
the idempotency fingerprint and retention floor, request ids and the JSON
formatter, and the health endpoint. The other 57 are Postgres-backed.
They cover the pages, filters and pagination; idempotency, including
concurrent duplicates and key retention; the database triggers against
writes that bypass the app; atomic rollback; agreement between the log
and the read model; and the rebuild, including recovery from a corrupted
projection, a posting made mid-rebuild and a backfilled legacy ledger.

Note that the Postgres-backed tests **skip themselves** unless
`TEST_DATABASE_URL` is set, so a local run without a database reports
"18 passed, 57 skipped" and is not a passing build. See the README for
the command that runs the full suite.

**CI** — ruff and Postgres-backed tests, plus a `docker-smoke` job that
proves the container builds, migrates and serves a real posting flow.

---

## 8. What still needs doing

Ordered roughly by how much they would hurt.

### Correctness

**1. No FX handling.** Per §2.4, currency conversion cannot be expressed.
Needs a clearing-account pattern plus an FX gain/loss account.

### Robustness

**2. No authentication or authorisation anywhere.** Every route is public.
Acceptable for a demo, disqualifying for anything real. It is also why
`rebuild_read_model()` and the other maintenance tasks are CLIs and not
routes.

**3. Key pruning has no scheduler.** `scripts/prune_idempotency_keys.py`
does the cleanup (§4), but nothing in the stack runs it. A deployment
needs a cron job or a scheduled task.

**4. Entry order on the transaction-detail page is arbitrary.** Entries
are sorted by `(created_at, id)`; within one transaction `created_at` is
shared, so a random UUID decides the order, and a rebuild can change it.
Storing each entry's position in the event payload and in
`ledger_entries` would make it submission order and stable.

**5. Replay is all-or-nothing and in memory.** `rebuild_read_model` loads
the whole log at once and replays from `sequence` 1. There are no
snapshots, and no incremental catch-up of a projection from a known
position. Fine at this size, and the first thing to change if it grows.
While a rebuild runs, postings wait on its lock (§3.3).

**6. Event payloads are unversioned.** `transaction.posted` has had one
shape since it was introduced. The first change to it will need either a
version field in the payload or a new event type, with replay rules for
both.

### Missing interfaces

**7. No JSON API.** Every write is a form post that answers with HTML or
a redirect. `post_transaction_once` and `create_account_record` are
already free of HTTP concerns, so a JSON `POST /api/transactions` taking
an `Idempotency-Key` header is mostly wiring.

### Roadmap items not started

Webhook ingestion, multi-provider payment orchestration, reconciliation,
the outbox pattern for reliable event publishing, metrics and tracing
(structured logs exist, §5.15), and a deployment pipeline.
