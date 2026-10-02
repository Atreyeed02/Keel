# Keel — Architecture and Concepts

A complete walkthrough of what this service is, the accounting and
event-sourcing ideas it is built on, what every file does, and what is
still missing.

Last brought up to date on 2026-09-29.

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
that yet. It is deferred future work, not out of scope; see §8.

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
| `transaction.posted` | insert into `transactions` with `id = aggregate_id`, then one `ledger_entries` row per `payload["entries"]` item, with its `position` |
| anything else | raise `UnknownEventError` — a rebuild that silently skipped an event would disagree with the log |

**Payload versions.** Every `account.created` and `transaction.posted`
payload written now carries `"schema_version": 1`
(`app/domain/event_versions.py`). Before replaying an event, the rebuild
checks it. A payload with no `schema_version`, which is every event written
before the field existed, is read as version 1, because that is what it
is. A version replay has no rule for, including a non-integer such as `"1"`
or `true`, raises `UnsupportedEventVersionError`, a subclass of
`UnknownEventError`, and the whole replay rolls back, for the same reason an
unknown event type does.

A version changes only for a change an existing reader could not handle: a
field renamed, removed or given a new meaning. Adding an optional field is
not one. Entries gained `position` that way and are still version 1,
because replay already knows what an entry without one means (below). The
first incompatible change will add a version to `SUPPORTED_VERSIONS` and a
branch in `rebuild_read_model`, with the old version still readable.
`test_new_payloads_carry_their_schema_version`,
`test_payloads_without_a_schema_version_replay_as_version_1` and
`test_replay_refuses_a_schema_version_it_does_not_know` cover the three
cases.

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
- `test_entries_are_shown_in_submission_order_and_a_rebuild_keeps_it` and
  `test_a_rebuild_orders_an_event_without_positions_by_its_payload` cover
  entry order, on the detail page and in the API, for new and old events.
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

**Entry order survives a rebuild.** Each entry's place in the transaction
as submitted is stored as `ledger_entries.position` and in the
`transaction.posted` payload, and the detail page and
`GET /api/transactions/{id}` sort by it (migration `c2e8f4a61b07`).
Before that, entries were sorted by `(created_at, id)`. All of a
transaction's entries share `created_at`, so a random UUID decided the
order, and a rebuild, which mints new ids, could change it.

Data written before `position` existed is handled on both sides:

- **Read-model rows** stay `NULL`. The read model cannot recover their
  submission order; the arbitrary order they show is all it knows. So
  reads sort `NULL` after numbered positions and then by
  `(created_at, id)`, and those rows keep exactly the order they had.
- **Events** are better off. `post_transaction` has always written the
  payload's `entries` array in submission order, and JSONB keeps array
  order, so an entry's index in that array *is* its submission position.
  Replay uses it when the entry has no `position`. That is a deliberate
  step past "fall back to the current ordering": the current ordering
  comes from random ids, and falling back to it on replay would scramble
  those transactions again on every rebuild. A rebuild therefore gives old
  transactions their real order back.

This was checked on a database migrated with pre-existing entries: before
a rebuild they render in the old order, `position` all `NULL`; after it,
in submission order, positions `0..n`.

**The limit.**

- **`ledger_entries.id` is not reproduced.** The `transaction.posted`
  payload never carried entry ids, so replay mints new ones. Nothing
  depends on a particular value: no foreign key references
  `ledger_entries`, and no query or template looks an entry up by id.
  Ordering no longer depends on it either.

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

**The JSON API fingerprints meaning, not bytes.** `POST /api/transactions`
(§5.16) uses the same `post_transaction_once` and the same table, but a
different fingerprint, `entries_fingerprint()`. The form's raw-string hash
suits a browser, which resends a form byte for byte. A JSON client
re-serialises its request on every retry, and nothing obliges it to
produce the same bytes: another key order, `"100"` for `"100.00"`, `usd`
for `USD`, other whitespace. Hashing the raw body would turn
such a genuine retry into a 409, which is exactly what idempotency exists
to prevent. So the hash is taken over the *validated* request in
canonical form:

- each amount at exactly two decimal places (amounts are validated to
  have at most two), currency upper-cased, account id as a canonical UUID;
- entries in the order they were sent;
- the description exactly as sent, except that an empty one counts as
  none, which is how both are stored.

The rule is that two requests share a fingerprint exactly when they would
post the same transaction. Anything that changes what would be stored
changes the hash, and a key reused for it is a 409. The canonical form
also carries a `"format": "json-v2"` marker, which keeps API hashes
disjoint from form hashes: a key first used by the form and then sent to
the API is a conflict, never a replay of a request made through the other
door. The form fingerprint is unchanged, so every stored form key stays
valid.

**Entry order is part of the fingerprint.** The first version sorted the
entries before hashing, because the ledger kept no order: two requests
listing the same entries differently posted the same transaction. Since
§3.3 gave each entry a stored `position`, they no longer do; the order is
kept and shown. Under the rule above, order therefore has to count, and
a key reused with the entries reordered is a 409, not a replay that would
hand back a transaction in an order the client did not send. It costs a
genuine retry nothing. The re-serialisation the canonical form absorbs is
about object keys, number spelling and case; JSON arrays are ordered, so
a client resending a request keeps its entries in the same order. The
marker went from `json-v1` to `json-v2` with this change. An API key
stored under `json-v1` whose retry arrived afterwards would get a 409;
nothing was deployed with `json-v1`, and keys are pruned after 30 days
anyway. `test_the_same_entries_in_another_order_under_one_key_is_a_409`
pins the behaviour.

**Scope note:** idempotency applies to `POST /post-transaction` and
`POST /api/transactions`. It deliberately does *not* apply to account
creation — it exists to protect
the double-entry invariant against double-posting, and creating a
duplicate account is neither a money movement nor a conservation
violation.

---

## 5. The code, layer by layer

```
app/
├── api/health.py      HTTP: liveness + DB connectivity
├── client_address.py  who the client is, behind proxies and Cloudflare (§5.21)
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

**Whatever URL a host hands out.** Hosts give `DATABASE_URL` as
`postgres://…` or `postgresql://…`, often with `?sslmode=require`.
`database_target()` accepts any of those, or `postgresql+<driver>://`, and
spells the URL twice: `postgresql+asyncpg://` for the app and
`postgresql+psycopg://` for Alembic. `sslmode` needs separate handling,
because asyncpg refuses it in the URL. It is taken out of the asyncpg URL
and passed as asyncpg's own `ssl` argument (`app/db/engine.py`), while
psycopg, which is libpq underneath, keeps it in the URL. `DATABASE_SSL`
(one of libpq's modes, from `disable` to `verify-full`) and the URL's
`sslmode` are both honoured: when they differ, the stricter one is used,
so a leftover setting in one place can never weaken the other. With
`DATABASE_SSL=require` and `?sslmode=verify-full`, the connection verifies
the certificate and host name. A malformed URL or unknown mode stops the process at start.

**No other query parameters.** SQLAlchemy passes every query parameter in
the asyncpg URL to `asyncpg.connect()` as a keyword argument, and asyncpg
accepts none of libpq's. So a URL with `?channel_binding=require` (Neon's
default) used to let the migrations connect through psycopg while every
connection the app made failed with a `TypeError`. The live demo went down
that way: `/health` reported `degraded` and every page returned 500. The
asyncpg URL now carries no query parameters. `channel_binding=prefer` or
`disable` reaches psycopg only, which is no weaker, since asyncpg never
binds and `prefer` allows that. `channel_binding=require` stops the process
at start: asyncpg cannot honour it, and dropping it would quietly weaken
every runtime connection. Any other parameter stops the process too. Each
refusal names the parameter, never the URL. `Settings` sets
`hide_input_in_errors`, so pydantic's error doesn't print DATABASE_URL either.
Alembic stores the URL in an ini-style config where `%` is special, so it
gets `alembic_url`, the psycopg URL with `%` escaped; a percent-encoded
password would otherwise break every migration.

**Production refuses an unsafe configuration.** With
`ENVIRONMENT=production`, `Settings` raises at import, so the process never
starts, if any of these holds:

- `DATABASE_URL` was not set, or is the `ledger:ledger@db` default in any
  spelling.
- The database connection is not encrypted: the effective SSL mode (the
  stricter of `DATABASE_SSL` and the URL's `sslmode`) is not `require`,
  `verify-ca` or `verify-full`. No mode, `disable`, `allow` and `prefer` are all
  refused; the last two fall back to plaintext when the server offers no
  TLS. An `sslmode` in the URL counts, because managed databases put one
  in the URLs they hand out.
- `FORWARDED_ALLOW_IPS` contains `*`, alone or in a list, or names nothing.
  With `*` the leftmost `X-Forwarded-For` entry, which a client writes,
  would be believed (§5.21).

`ENVIRONMENT=demo`, the public demo, is hosted too and gets the same guard. It also turns on the demo notice (§5.8) and is the only
environment the reset script (§5.10) runs in.

**`PORT`** (default 8000) and `HOST` (default `0.0.0.0`) are where
`python -m app.serve` listens (§5.12).

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
    except Exception as exc:
        log.warning(
            "db.ping_failed",
            extra={"error": type(exc).__name__, "detail": _without_password(str(exc))},
        )
        return False
```

Returns a boolean rather than raising — the health endpoint must always
answer. It logs a `db.ping_failed` warning with the error's type and message,
with the password masked and no traceback, because a bare `degraded` says
nothing about the cause. **This is the behaviour that made the CI smoke test interesting**
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
    amount: Decimal = Field(max_digits=18, decimal_places=2)
    currency: str
```

with `@field_validator`s rejecting `entry_type` outside
`("debit", "credit")` and any `amount <= 0`. The `Field` constraint
matches the column, `Numeric(18, 2)`. Without it Postgres rounds rather
than refuses: `100.005` used to be posted and stored as `100.01`, a
different amount from the one submitted, and `0.001` rounded to `0.00`
and failed the `amount > 0` CHECK as a 500. `100.000` is still accepted,
since it is exactly `100.00`. Pydantic also coerces types:
a form string `"100.00"` becomes a `Decimal`, and a UUID string becomes a
`uuid.UUID`, failing loudly if it cannot.

**`UnbalancedTransactionError(ValueError)`** — a named domain exception.
Subclassing `ValueError` means generic handlers still catch it, while
code that cares can catch this specific case.

**`assert_balanced()`** — §2.3.

**`post_transaction(conn, entries, description)`** — the write path:

1. Reject fewer than two entries, and a description longer than the
   512 characters `transactions.description` holds (`validate_description`;
   an overlong one used to fail in Postgres as a 500).
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
middleware (§5.15), creates the FastAPI app, includes the JSON API's
routers and error handlers (§5.16), wraps the app as `served` behind
`ClientAddressMiddleware` (§5.21), mounts `/static`, points Jinja2 at
`templates/`, and registers a custom filter:

```python
def _money(value: Decimal | None) -> str:
    return f"{(value or Decimal('0')):,.2f}"

templates.env.filters["money"] = _money
```

`{{ total_debits|money }}` in a template renders `61,056.50`. Centralising
formatting means every page shows money identically.

**`GET /` — the overview.** The most interesting query, now in
`app/domain/reads.py` (§5.17) as `account_balances`, which
`GET /api/accounts` shares. It computes per-account debits, credits and
balance in **one** SQL statement using conditional aggregation:

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

`account_balances` applies the normal-side sign flip from §2.2, and the
route groups the rows by `account_type` into a `defaultdict(list)`.

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
logs `transaction.posted` or `transaction.replayed` through the shared
helpers (§5.15).

**`GET /transaction-detail/{id}`** — loads the transaction and its
entries with their account names through `transaction_with_entries`
(§5.17), splits them into debit and credit lists, totals each
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

**The demo notice.** With `ENVIRONMENT=demo`, `base.html` puts one line
above the navigation on every page, a re-rendered form included: this is a
public demo, anyone can write to it, and it resets periodically. The
template calls `is_demo()`, a global `main.py` registers, rather than
reading a value fixed at import, so the page follows the setting. It is
given that one flag rather than the settings object, which holds the
database URL and its password.

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
`a4c7e2d9b813_drop_duplicate_idempotency_key_constraint.py` fixes the
difference `alembic check` used to report. The initial schema declared
both `PRIMARY KEY (key)` and `UNIQUE (key)` on `idempotency_keys`, but
Postgres's `CREATE TABLE` folds a unique constraint identical to the
primary key into it, so no database ever had two constraints: it had one
primary key named `uq_idempotency_key`. `schema.py` now declares only the
primary key, and the migration renames the constraint (and its index) to
`idempotency_keys_pkey`, the name `create_all` gives it.
`c2e8f4a61b07_ledger_entries_position.py` adds the nullable
`ledger_entries.position` column (§3.3). Existing rows stay `NULL`.

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
CMD ["python", "-m", "app.serve"]
```

`app/serve.py` runs `alembic upgrade head` with `check=True`: if migrations
fail, uvicorn never starts (§5.12).

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

**`scripts/reset_demo_data.py`** puts the public demo back as a fresh seed
leaves it. It deletes every account, transaction, entry, event and
idempotency key, restarts the identity sequences, and calls the seed
script's `seed()`. Two guards stand between it and a real ledger. It
refuses unless `ENVIRONMENT=demo`, checked before it connects to anything,
so neither a production ledger (`production`) nor a laptop's
(`development`, the default) can be wiped by a command run in the wrong
shell. Without `--yes` it
only counts what it would delete.

It is the one sanctioned exception to the append-only log. The
`events_append_only` trigger refuses TRUNCATE, so the script disables it,
truncates, and enables it again, all in the same transaction as the reseed.
Postgres DDL is transactional, so no other session ever sees the log
unguarded, and a failure anywhere, reseeding included, rolls the ledger back
to what it was, trigger on. `ALTER TABLE ... DISABLE TRIGGER` needs the
table's owner, which is the role that ran the migrations. The tables are
locked first, in the order a posting takes them, so a reset waits for
postings in flight rather than deadlocking with them. A page read can still
deadlock with it. Postgres then aborts one of the two, and a reset that
loses rolls back whole and can be run again. Nothing schedules it yet.

### 5.11 `tests/`

| File | Needs a DB | Covers |
|---|---|---|
| `test_ledger_domain.py` | no | the balance invariant, per-currency independence, amount/type validation, amounts and descriptions the columns cannot store |
| `test_health.py` | no | health endpoint always answers |
| `test_ledger_pages.py` | **yes** | idempotent retry, inline errors, account creation and its event, overview, transaction list filters (UTC day boundaries under any session time zone) and pagination, `sequence` ordering, the trigram index behind the search |
| `test_idempotency.py` | mostly | retries, 409 on key reuse, key release after a rejected attempt, concurrent duplicates and conflicting payloads, key retention and its CLI (§4) |
| `test_ledger_invariants.py` | **yes** | every database trigger (balance, account currency, append-only log) against writes that bypass the app; atomic rollback; log and read model agree; trial balance nets to zero |
| `test_rebuild.py` | **yes** | rebuild reproduces the read model, repairs a corrupted one, is repeatable, is safe alongside a concurrent posting; unknown event types and event-less accounts fail the replay; the backfill makes a legacy ledger rebuildable; entry order before and after a rebuild, for new and old events; payload schema versions, missing and unknown; both CLIs |
| `test_observability.py` | partly | request ids (generated, propagated, unsafe ones replaced), the JSON formatter, ledger identifiers on posting log lines |
| `test_schema_guard.py` | **yes** | the fixtures refuse to wipe a database alembic has migrated |
| `test_api.py` | partly | every JSON API status code, the error shape and its scoping, string amounts, replays (including reformatted retries), form/API key separation, key release after a 422, concurrent duplicate and conflicting requests (§5.16) |
| `test_deploy_config.py` | no | `DATABASE_URL` in every host spelling, TLS modes, `PORT`, the production and demo guards, the start command and the dev override (§5.1, §5.12) |
| `test_hardening.py` | mostly no | the body size limit, declared and chunked; security headers; the posting form's CSP nonce (§5.18) |
| `test_proxy_headers.py` | no | forwarded headers from trusted and untrusted peers; the Render chain behind Cloudflare, `CF-Connecting-IP` believed only when it can be, forged headers changing nothing; separate write limits per client, none gained by forging (§5.21) |
| `test_rate_limit.py` | no | 429 with an exact `Retry-After`, the shared form/API allowance, refused writes not counted and never reaching the app, reads unlimited, per-address and per-/64 keys, the 429 logged and with security headers, `0`, idle clients forgotten (§5.19) |
| `test_capacity.py` | mostly | both caps through both interfaces, nothing written on refusal, replays at the cap, a refused key posting once there is room, `0`, uncapped scripts (§5.20) |
| `test_demo.py` | partly | the notice on every page only with `ENVIRONMENT=demo`; the reset script refusing outside the demo before connecting and without `--yes`, restoring exactly a fresh seed, re-enabling the append-only trigger, and rolling back whole on failure (§5.8, §5.10) |

`tests/conftest.py` clears the write rate limit's counts around every test,
so each test starts with a client's full allowance under the real default.

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
`alembic/`, `alembic.ini` and `scripts/`, then switch to an unprivileged
user, `keel` (uid 10001). The copied files stay owned by root, so the app can
read its code but not change it. `PYTHONUNBUFFERED` gets log lines to the
host as they are written.

The start command is `python -m app.serve` (`app/serve.py`): `alembic
upgrade head`, then, only if that succeeded, uvicorn on `$PORT` (default
8000), serving `app.main:served` with uvicorn's own proxy handling off
(§5.21), and never `--reload`.
**Migrating on start assumes a single instance.** Alembic takes no lock, so
two instances starting together would both migrate. A host that scales out,
or starts the new instance before stopping the old one during a deploy,
should run `alembic upgrade head` as a release step and start instances with
`python -m app.serve --no-migrate`.

Checked locally: the image, run with `ENVIRONMENT=production`, `PORT=9090`
and a `postgres://…?sslmode=disable` URL against a throwaway Postgres,
migrated to head, served `{"status":"ok","db":"up"}` on 9090, and ran as uid
10001. Without `DATABASE_URL` it refused to start. CI's `docker-smoke` job
now also fails if the container runs as root.

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
the image and starts the server with `python -m app.serve --reload`, so an
edit under `app/` restarts it within a couple of seconds. This used to be
described as live reload without being one: the image's server never
reloaded, so an edit needed `docker compose restart app`. It now polls for
changes (`WATCHFILES_FORCE_POLLING=true`), because a bind mount from a
Windows or macOS host usually delivers no file-change events into Docker's
Linux VM and an event-based watcher would never fire. Checked on Windows in
an isolated copy of the dev stack: an edit to `app/api/health.py` was being
served about two seconds later, and so was its revert. Only the override
passes `--reload`; the image's own `CMD`, which CI and production run,
never does, and a test pins both. Compose merges it automatically when present,
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

**`lint-and-test`** — ruff; `pip-audit --strict -r requirements.txt`, which
fails the build if any pinned dependency has a known vulnerability (or
cannot be audited); then `alembic upgrade head` and `alembic check`
on a database of their own, which fails the build if the migrations and
`app/db/schema.py` have drifted apart in any way autogenerate can see
(the tests build their schema from `schema.py`, the app from the
migrations, so drift would mean the tests exercise a different schema);
then pytest against a real Postgres service container.

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
rather than copy it, which `POST /api/transactions` now does, with
`entries_fingerprint()` in place of `request_fingerprint()` (§4). It
takes the same "caller owns the transaction"
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
write that rolled back. The ledger events go through `log_account_created`,
`log_transaction`, `log_transaction_rejected`, `log_idempotency_conflict`
and `log_ledger_full`, which the form routes and the JSON API both call,
so each event has one definition of its fields:

| Event | Fields |
|---|---|
| `account.created` | `account_id`, `account_type`, `currency` |
| `transaction.posted` / `transaction.replayed` | `transaction_id`, `idempotency_key`, `entry_count`, `account_ids` |
| `transaction.rejected` | `idempotency_key`, `reason` |
| `idempotency.conflict` (warning) | `idempotency_key` |
| `ledger.full` (warning) | `reason`: which cap refused the write (§5.20) |
| `request.completed` | `method`, `path`, `status`, `client`, `scheme`, `duration_ms` |
| `request.failed` | `method`, `path`, `duration_ms` |

**Behind a host's proxy**, the socket peer is the proxy, not the client,
and the connection to the container is plain HTTP even when the visitor used
HTTPS. The `client` and `scheme` in the log line are what
`app/client_address.py` made of the forwarded headers (§5.21): the same
client the write rate limit counts (§5.19), and the scheme of any absolute
URL the app builds, such as FastAPI's trailing-slash redirect.

The event's own id is not logged: `post_transaction` does not return it.
`transaction_id` is the event's `aggregate_id`, which is enough to find
it.

### 5.16 `app/api/` — the JSON API

Four endpoints, alongside the HTML pages and on the same domain layer.
Nothing in `app/api/` validates an entry, checks a balance, claims a key or
writes a row itself; it calls the functions the form routes call.

| Endpoint | Domain calls | Answers |
|---|---|---|
| `POST /api/accounts` | `validate_account`, `create_account_record` | `201` with the account; `422` |
| `GET /api/accounts` | `account_balances` (§5.17) | `200` with every account |
| `POST /api/transactions` | `EntryInput`, `assert_balanced`, `validate_description`, `post_transaction_once` | `201` first post; `200` replay; `400`; `409`; `422` |
| `GET /api/transactions/{id}` | `transaction_with_entries` (§5.17) | `200`; `404` |

**Posting.** `POST /api/transactions` takes

```json
{"description": "Invoice 1001",
 "entries": [{"account_id": "…", "entry_type": "debit",  "amount": "250.00", "currency": "USD"},
             {"account_id": "…", "entry_type": "credit", "amount": "250.00", "currency": "USD"}]}
```

with an `Idempotency-Key` header. The checks run cheapest first, so a
request that can be refused without the database never opens a
connection: the header (`400`), then the body's shape and the entry
validators (`422`), then the balance, entry count and description length
(`422`). Only then does it open a transaction and call
`post_transaction_once`, exactly as the form route does (§4). A first
post answers `201`; a genuine retry answers `200` with the same
transaction. Both carry `Location: /api/transactions/{id}` and
`Idempotent-Replayed: false` or `true`, so a client can tell them apart
without comparing bodies. The key reused for a different request is a
`409`. Entries naming a missing account or the wrong currency are a `422`
with both problems in one message, and roll the claim back, so the same
key can be sent again once the request is fixed.

The key must be 1–255 visible ASCII characters with no spaces: the
column holds 255, and anything else is more likely a client bug than an
identifier. A UUID is the obvious choice.

**Errors.** Everything under `/api/` that fails answers

```json
{"error": {"code": "idempotency_conflict", "message": "Idempotency-Key was already used for a different request"}}
```

| Status | `code` |
|---|---|
| 400 | `missing_idempotency_key`, `invalid_idempotency_key` |
| 404 | `not_found` (also an unknown `/api/` path) |
| 405 | `method_not_allowed` |
| 409 | `idempotency_conflict` |
| 422 | `validation_error`, `unbalanced_transaction`, `invalid_accounts` |

`app/api/errors.py` installs the handlers, including for the framework's
own 404 and 405, and scopes them to `/api/`. The HTML routes keep
FastAPI's default errors, which is what they have always returned. The
routes read and parse their own bodies rather than letting FastAPI
validate a body model, so every validation message comes from
`describe_validation_error` and the domain's validators and reads exactly
as it does on the forms, for example `entries.0.amount decimal input
should have no more than 2 decimal places`.

**Money is a string.** Amounts go out as strings with exactly two decimal
places and must come in as strings. Most JSON parsers turn a number into
a binary float, which cannot represent 0.10; a number arriving here has
probably been through one already. `ApiEntryInput` adds that rule, plus
upper-casing the currency as the form route does, to `EntryInput` and
nothing else. Unknown fields are refused rather than ignored: in a money
API, a misspelled field silently dropped is worse than an error.

**Responses.** An account carries `normal_side`, `debits`, `credits` and
a `balance` signed exactly as the overview page signs it. A transaction
carries its entries with account names, but not entry ids: a rebuild
mints new ones (§3.3).

**Logging.** The same events as the form routes, after the commit,
through the shared helpers in `app/observability.py` (§5.15), so the
names and fields cannot drift apart. The request id ties a line back to
the path it came from.

What it does not do yet is listed in §8.

### 5.17 `app/domain/reads.py` — shared queries

The read-side queries that both a page and an API endpoint need:
`account_balances` (the overview's per-account totals, with the
normal-side sign), `currency_totals` (the overview's trial balance) and
`transaction_with_entries` (the detail page's lookup). They moved here
from `main.py` when the API needed them. The pages were rendered from a
seeded ledger before and after the move and are byte-identical.

### 5.18 `app/security.py` — body size limit and security headers

Two ASGI middlewares, neither of them about the ledger.

**Body size limit** (`MAX_REQUEST_BODY_BYTES`, default 64 KiB). A request
whose `Content-Length` is over the limit is answered `413` before the app
runs, which is the only protection for a route that never reads its body.
A chunked request, which declares no length, is counted as it arrives and
cut off with a `413` once it passes the limit. Under `/api/` the 413 uses the
API's error shape. The middleware sits *inside* the request log, so a 413 is
logged as an ordinary `request.completed`, not a `request.failed` with a
traceback.

**Security headers**, on every response, a 413 included (this middleware
is outermost):

| Header | Value | Why |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | a browser never guesses a content type |
| `X-Frame-Options` | `DENY` | no framing, so no clickjacking; CSP `frame-ancestors 'none'` says the same to newer browsers |
| `Referrer-Policy` | `same-origin` | other sites never see which Keel URL a visitor came from |
| `Content-Security-Policy` | below | limits what a page may load and run |

The page policy allows scripts from Keel itself, from
`https://cdn.tailwindcss.com`, and inline only with this response's nonce.
A fresh nonce is made per request, stored in `request.state.csp_nonce`, and
put on the posting form's one inline script. Styles need `'unsafe-inline'`,
because the Tailwind Play CDN builds its CSS in the browser and injects it
as `<style>` elements; without it the pages lose their styling. FastAPI's
`/docs` (Swagger UI) and `/redoc` load their UI from jsDelivr and bootstrap
it with an inline script FastAPI writes, so those two paths get a separate
policy that allows that CDN and inline scripts.

Checked in headless Chrome, against the branch running on real data, over
the DevTools protocol: every page (overview, transactions, event log, both
forms, a transaction's detail, `/docs`, `/redoc`) loaded with no CSP
violation, the Tailwind styles applied, Swagger UI and ReDoc rendered, and
typing an amount into the posting form updated its live total. As a
control, removing the nonce from the form's script made Chrome block it and
the total stay at `0.00`.

The Tailwind Play CDN is meant for development, not production: it ships
the whole compiler to every visitor and is the reason styles need
`'unsafe-inline'`. Building the CSS at image build time would remove both
(§8).

### 5.19 `app/ratelimit.py` — the write rate limit

Once the demo is public, anyone can write to it. The rate limit caps how
fast one client can write. The caps in §5.20 cap how much everyone can
write in total, which is what actually protects the database.

**What counts.** Every request whose method is not `GET`, `HEAD` or
`OPTIONS`: the forms and the JSON API share one allowance per client. A
write the app rejects with a 400 or 422 still counts, because it still
costs a request. The default is 30 writes in any 60 seconds
(`WRITE_RATE_LIMIT`, `WRITE_RATE_WINDOW_SECONDS`). A person using the forms
never gets near that; a script posting in a loop does. `WRITE_RATE_LIMIT=0`
turns it off. Reads are not limited. They write nothing, and the database's
connection pool already bounds how many run at once.

**The window slides.** Each client keeps the times of its last
`WRITE_RATE_LIMIT` writes. A write is allowed if fewer than that many fall in
the last window. That avoids the burst a fixed window lets through at its
boundary, where a client could make the full allowance just before a minute
turns and again just after. A refused write is not recorded, so a client that
waits out `Retry-After` is let in. Memory is bounded by the clients active in
one window: once a window, any client idle for a whole window is forgotten.

**The answer.** `429 Too Many Requests` with `Retry-After`: the whole
seconds, rounded up, until the oldest write in the window leaves it, which
is exactly when a write is next allowed. Under `/api/` the body is
`{"error": {"code": "rate_limited", ...}}`; for the forms it is one line of
plain text, like the 413. The middleware answers before the app runs, so a
refused write never reads its body or opens a connection. It sits inside the
request log, so the 429 is logged as an ordinary `request.completed` with the
`client` it applied to, and inside the security headers, so the 429 has them.

**Who the client is.** `scope["client"]`, as `app/client_address.py`
set it (§5.21), the same address the log shows. IPv6 addresses are
counted per /64, the block one subscriber is normally given, so cycling
through the addresses of one connection earns no extra allowance. An
IPv4-mapped IPv6 address counts as its IPv4 address.

**One instance.** The counts are in process memory, like the migrate-on-start
assumption in §5.12. Two instances would each allow the full rate, and a
restart or deploy forgets every count. Scaling out needs a shared store
(Redis, or a table in Postgres) instead.

`tests/test_rate_limit.py` drives the real app on a fake clock, with every
database connection replaced by one that fails the test if used.

### 5.20 `app/domain/capacity.py` — caps on accounts and transactions

The rate limit slows one client down; it does nothing about many clients,
or one with many addresses. On a free 0.5 GB database that leaves the disk
to fill. `MAX_ACCOUNTS` (default 200) and `MAX_TRANSACTIONS` (default 2000)
are a ceiling that holds however the writes arrive.

**Sized from measurements.** On Postgres 16, counting every table and index
a write touches, including the event row and the idempotency key, an
account took about 1.1 KB and a two-entry transaction about 1.7 KB. The
largest transaction the 64 KiB body limit admits, 470 entries under a
512-character description, took about 91 KB. That worst case is what the
cap has to be sized for. 2000 of them come to under 200 MB, less than half
the database. A demo that is used normally will hold a few megabytes when
it reaches the cap.

**Where it is checked.** In the domain, inside the caller's transaction:
`create_account_record(..., max_accounts=)` counts accounts before
inserting, and `post_transaction_once(..., max_transactions=)` counts
transactions after claiming the key and before posting. Either raises
`LedgerFullError`, and the rollback takes the idempotency claim with it,
so a refused key can be used again once there is room. Because the count
comes after the claim, a replay of a posting that got in is still answered
at the cap. It adds nothing, and a client retrying a posting that
succeeded should hear that it succeeded. The routes pass the settings. The
scripts pass nothing, so seeding, backfilling and rebuilding are never
capped. `0` means no cap, which is what a real ledger wants.

**The answer.** A `409` with code `ledger_full` from the API, and the form
re-rendered with an inline error and a `409` from the pages, both saying
which maximum was reached, plus a `ledger.full` warning in the log, which is
the signal that the demo wants resetting. 409 rather than 507 Insufficient
Storage because nothing is wrong with the server: the ledger is in a state
that refuses the write, and retrying will not help until that state changes.

**Not a lock.** The count takes no lock, so writes that run at the same
moment can all see room and all commit, overshooting by at most the number
of connections writing at once (15 with the default pool). Serialising
every write to make a size limit exact would cost more than the few extra
rows are worth.

`tests/test_capacity.py` covers each cap through both interfaces, that a
refused write leaves nothing behind, replays at the cap, a refused key
posting once there is room, `0`, and the uncapped scripts.

### 5.21 `app/client_address.py` — who the client is

Every request's client address and scheme are decided in one place:
`ClientAddressMiddleware`, which wraps the whole app as `app.main.served`.
`python -m app.serve` runs `served` with uvicorn's own proxy handling off,
because that handling rewrites the peer before the app sees it, and the
rule below needs the peer as it connected.

**The chain on Render**, as the live service showed it on 2026-10-03, using a
temporary diagnostic since removed:

```
visitor ──▶ Cloudflare ──▶ Render load balancer ──▶ Render proxy, 127.0.0.1 ──▶ Keel
            sets CF-Connecting-IP                     (inside the container)
            appends visitor to XFF
                           appends the edge to XFF
                                                    appends a 10.x hop to XFF
```

So `X-Forwarded-For` arrives as `[whatever the client sent..., visitor,
Cloudflare edge, 10.x]`. The peer of every public request is `127.0.0.1`.
Render's health checks connect from a `10.x` address and send only
`X-Forwarded-Proto`. A client-set `CF-Connecting-IP` gets a 403 from
Cloudflare. `True-Client-IP` and `X-Real-IP` are rewritten or passed through
by layers Keel cannot see, so neither is read.

**Two steps.**

1. uvicorn's own `ProxyHeadersMiddleware`, unchanged. For a peer
   `FORWARDED_ALLOW_IPS` trusts, `X-Forwarded-Proto` becomes the scheme, and
   `X-Forwarded-For` is read from the right, skipping trusted hops. The
   first untrusted hop becomes the client. With
   `FORWARDED_ALLOW_IPS=127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`
   that skips the `10.x` hop and lands on the Cloudflare edge, which every
   visitor behind that edge shares. That was the live behaviour before this
   section existed.
2. If the hop step 1 chose is in Cloudflare's published ranges
   (`CLOUDFLARE_NETWORKS`), and the peer was loopback, and there is exactly
   one `CF-Connecting-IP` holding exactly one address, that address is the
   client. Otherwise step 1's answer stands.

Step 2 reads the address step 1 already chose, so the two can never
disagree about where the walk along `X-Forwarded-For` ended.

**Why a visitor cannot pick their own address:**

| Attempt | What happens |
|---|---|
| Forge `X-Forwarded-For` | The forged entries sit left of the hops the proxies appended, and step 1 stops at the first untrusted hop. |
| Forge `CF-Connecting-IP` through Cloudflare | Cloudflare answers 403, and it sets the header itself on every request it forwards. |
| A Worker on another Cloudflare account | Cloudflare sets `CF-Connecting-IP` to its fixed Worker address, `2a06:98c0:3600::103`, so all such Workers share one allowance. |
| Reach Render's load balancer around Cloudflare | The hop it records is the caller's own address, not a Cloudflare one, so step 2 does not apply and the caller is the client. Render's public addresses (`216.24.57.0/24`) are announced only through Cloudflare's network (AS13335), so no such route is known, but nothing relies on that. |
| Another service on Render's private network, or a health check | The peer is `10.x`, not loopback: step 2 does not apply. |
| Forge `True-Client-IP` or `X-Real-IP` | Never read. |

**What is left:** someone who reaches Render's load balancer from inside
Cloudflare's address space, without going through Cloudflare's proxy, and
knows an address of it that Render does not publish. A Worker's raw TCP
socket is one way. Their forged `CF-Connecting-IP` would be believed. That
buys a fresh write allowance and nothing more: the caps (§5.20) still bound
the database.

**Keeping `CLOUDFLARE_NETWORKS` current.** It is a copy of
<https://www.cloudflare.com/ips-v4> and `/ips-v6`, with the fetch date in the
code. `scripts/check_cloudflare_ranges.py` compares it with the live lists,
and `.github/workflows/cloudflare-ranges.yml` runs that weekly and on demand.
GitHub emails a failed scheduled run to whoever last changed its schedule.
A stale copy fails safe: an edge in a new range is not recognised, so its
visitors share the edge's address, as before. It never lets a client choose
an address.

`tests/test_proxy_headers.py` replays the Render chain. It checks the
visitor Cloudflare names, forged `X-Forwarded-For`, `True-Client-IP` and
`X-Real-IP` changing nothing, a bypass of Cloudflare with a forged
`CF-Connecting-IP`, peers that aren't loopback (a health check, the private
network), an untrusted loopback, malformed or duplicated `CF-Connecting-IP`,
IPv6, and the rate limit: separate allowances for two visitors, one
allowance however the headers are forged, and none gained by rotating a
forged `CF-Connecting-IP` around Cloudflare. Each of step 2's three
conditions was removed in turn, and a test failed every time.

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

**JSON API** — `POST`/`GET /api/accounts`, `POST /api/transactions` with a
required `Idempotency-Key` (201 first post, 200 replay marked
`Idempotent-Replayed`, 409 on reuse) and `GET /api/transactions/{id}`, on
the same domain functions as the pages, with one error shape and string
amounts (§5.16). Its fingerprint compares requests by meaning (§4).

**Database-enforced invariants** — `events` refuses UPDATE, DELETE and
TRUNCATE; a deferred constraint trigger refuses to commit any
transaction whose entries do not balance per currency; an entry must
carry its account's currency, and an account's currency cannot change
(§5.2).

**Idempotency** — claim-first, so it holds under concurrency as well as
for sequential retries. Includes the 409 on key reuse, and a rejected
attempt releases the key (§4).

**Entry order and payload versions** — each entry's submission position
is stored and shown, and survives a rebuild; `account.created` and
`transaction.posted` payloads carry a `schema_version`, and replay refuses
one it does not know (§3.3).

**Schema drift check** — `alembic check` runs in CI on a freshly migrated
database (§5.13).

**Rebuild tooling** — `python -m scripts.rebuild_read_model`, safe to run
alongside live postings, and `python -m scripts.backfill_account_events`
for databases whose accounts predate `account.created` (§3.3).

**Idempotency key retention** — `python -m scripts.prune_idempotency_keys`
deletes keys past a retention window, 30 days by default (§4).

**Structured logging** — JSON lines on the `keel` logger with a
per-request id, echoed as `X-Request-ID` (§5.15).

**Deploy readiness** — `DATABASE_URL` in whatever form a host hands out,
TLS to a managed Postgres, `PORT`, and a refusal to start a hosted
environment on the development database (§5.1); `python -m app.serve`,
which migrates then serves, in an image that runs as an unprivileged user
(§5.12); forwarded headers believed only from `FORWARDED_ALLOW_IPS`, and
`CF-Connecting-IP` only from Cloudflare via Render's proxy (§5.21); a request body limit and security headers with a nonce CSP
(§5.18); `pip-audit` in CI (§5.13); and a Deploying section in the README.

**Public writes, bounded** — a per-client write rate limit with `429` and
`Retry-After`, keyed on the forwarded client address (§5.19), and hard caps
on total accounts and transactions sized from measured bytes per write
(§5.20).

**The public demo** — `ENVIRONMENT=demo`, a notice on every page that the
demo is public and resets (§5.8), and `python -m scripts.reset_demo_data`,
which restores the demo data and refuses to run anywhere else (§5.10).

**Templates** — shared `base.html`; the four original pages were
refactored onto it with rendered output verified byte-identical, and
every page added since was built on it directly.

**Seed data** — `python -m scripts.seed_demo_data`, domain-layer-driven
and idempotent.

**Testing** — 291 tests (§5.11). 190 run with no database at all: the
balance invariant, entry input validation, the error-aggregation helper,
the idempotency fingerprints and retention floor, request ids and the JSON
formatter, the health endpoint, every JSON API rejection that happens
before the database is touched, the deployment configuration, the body
limit and security headers, the proxy headers, the write rate limit, the
demo notice's absence and the reset script's refusals. The other 101 are
Postgres-backed. They cover the pages, filters and pagination; idempotency,
including concurrent duplicates and key retention; the database triggers
against writes that bypass the app; atomic rollback; agreement between the
log and the read model; the rebuild, including recovery from a corrupted
projection, a posting made mid-rebuild and a backfilled legacy ledger; the
caps; and the demo reset. Each test added for the rate limit, the caps,
the notice and the reset was checked to fail with the behaviour it covers
broken.

Note that the Postgres-backed tests **skip themselves** unless
`TEST_DATABASE_URL` is set, so a local run without a database reports
"149 passed, 101 skipped" and is not a passing build. See the README for
the command that runs the full suite.

**CI** — ruff and Postgres-backed tests, plus a `docker-smoke` job that
proves the container builds, migrates and serves a real posting flow.

---

## 8. What still needs doing

Ordered roughly by how much they would hurt.

### Correctness

**1. No FX handling (deferred future work).** Per §2.4, currency
conversion cannot be expressed. Needs a clearing-account pattern plus an
FX gain/loss account.

### Robustness

**2. No authentication or authorisation anywhere.** Every route is public.
For the public demo that is the point, and what makes it safe to leave open
is bounded: a per-client write rate limit (§5.19), caps on what the ledger
will hold (§5.20), and a reset (§5.10). For anything real it is
disqualifying. It is also why `rebuild_read_model()` and the other
maintenance tasks are CLIs and not routes.

**3. Nothing is scheduled.** `scripts/prune_idempotency_keys.py` (§4) and,
for the demo, `scripts/reset_demo_data.py` (§5.10) do their jobs, but
nothing in the stack runs them. The Render deployment needs a daily prune
and a periodic reset.

**4. The forwarded client address, residual risk.** Settled on the live
service (§5.21): the client is the visitor Cloudflare names. One gap is left.
Someone who connects to Render's load balancer from inside Cloudflare's
address space without going through Cloudflare's proxy, and who knows an
unpublished address of it, can choose their own address. That costs only the
rate limit.

**5. One instance only.** Migrating on start (§5.12) and the in-memory
rate limit (§5.19) both assume a single instance. Scaling out needs
migrations as a release step and a shared store for the counts.

**6. Replay is all-or-nothing and in memory.** `rebuild_read_model` loads
the whole log at once and replays from `sequence` 1. There are no
snapshots, and no incremental catch-up of a projection from a known
position. Fine at this size, and the first thing to change if it grows.
While a rebuild runs, postings wait on its lock (§3.3).

**7. The pages load Tailwind's Play CDN.** It ships the whole compiler to
every visitor, is meant for development, and is why the CSP allows inline
styles (§5.18). Building the CSS when the image is built would remove both.

### Missing interfaces

**8. The JSON API is minimal.** It has what a client needs to create
accounts, post transactions safely and read one back (§5.16). It has no
single-account read, no transaction listing, and no pagination:
`GET /api/accounts` returns every account at once. There is no event-log
endpoint and no API version in the path. Like every route, it is
unauthenticated (item 2).

### Future work, not started

**FX handling** (item 1) and **metrics and tracing** (structured logs
exist, §5.15). Both are planned: they belong inside the ledger service and
are deferred, not ruled out.

### Out of scope

These are the layers a payments platform puts *around* a ledger. They are
listed to mark the boundary of this project, not as planned work: webhook
ingestion, multi-provider payment orchestration, reconciliation, the
outbox pattern for reliable event publishing, and a deployment pipeline.
A hosted demo instance is different from a deployment pipeline and is
still wanted. The app is now ready for one; what remains is the Render +
Neon setup (`STATUS.md` §4).
