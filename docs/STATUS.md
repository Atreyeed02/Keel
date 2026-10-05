# Keel — Build Status & Handoff

**As of 2026-10-05.** A snapshot of what is actually built, what is
verified, and where the next piece of work starts. For the *why* behind
the design — the accounting concepts, the event-sourcing rationale, a
file-by-file walkthrough — read `ARCHITECTURE.md` first; this document
does not repeat it.

---

## 1. Verified state, right now

Eleven PRs are merged into `main`, with regular merge commits, and their
branches deleted:

| PR | Branch | Merge commit on `main` |
|---|---|---|
| [#1](https://github.com/Atreyeed02/Keel/pull/1) | `feat/event-sourced-accounts-and-rebuild` (18 commits) | `104f6a1` |
| [#2](https://github.com/Atreyeed02/Keel/pull/2) | `feat/json-api` (4 commits) | `bd9db76` |
| [#3](https://github.com/Atreyeed02/Keel/pull/3) | `docs/status-after-merges` | `250b7e6` |
| [#4](https://github.com/Atreyeed02/Keel/pull/4) | `chore/small-fixes` | `3f2be79` |
| [#5](https://github.com/Atreyeed02/Keel/pull/5) | `chore/deploy-readiness` (14 commits) | `9f5bf5d` |
| [#6](https://github.com/Atreyeed02/Keel/pull/6) | `fix/asyncpg-url-params` | `16a72ed` |
| [#7](https://github.com/Atreyeed02/Keel/pull/7) | `diag/forwarding-headers` (temporary diagnostic, removed again by #8) | `df01fe4` |
| [#8](https://github.com/Atreyeed02/Keel/pull/8) | `fix/client-ip-cloudflare` (3 commits) | `0ce9bca` |
| [#9](https://github.com/Atreyeed02/Keel/pull/9) | `docs/rate-limit-verified` | `d1cf9f3` |
| [#10](https://github.com/Atreyeed02/Keel/pull/10) | `docs/contributing` | `66cd17d` |
| [#11](https://github.com/Atreyeed02/Keel/pull/11) | `chore/scheduled-demo-reset` (2 commits) | `a083641` |

**Live.** Keel runs on Render (free tier, Singapore) from `main`, with
Auto-Deploy on every commit, against Neon Postgres (Singapore, direct
connection, `sslmode=require`), with `ENVIRONMENT=demo` and
`FORWARDED_ALLOW_IPS=127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`.
After #6 deployed, `/health` returned `{"status":"ok","db":"up"}`.
Render's `DATABASE_URL` and the nightly reset's secret both name the
direct endpoint, not Neon's connection pooler. The reset's first run (below)
stopped on a pooler URL, and on 2026-10-05 both were set to the direct
endpoint; Render redeployed with `/health` up.

**Rate limit verified on the live service** (2026-10-03, on `0ce9bca`). The
README's forged-header check passed. A write sent with forged
`X-Forwarded-For: 192.0.2.1`, `True-Client-IP: 192.0.2.2` and
`X-Real-IP: 192.0.2.3` was answered 400 and logged with the caller's own
public address as `client` (the one `api.ipify.org` reported from the same
shell) and `scheme` `https`. A browser request was logged the same way.
Each visitor gets their own write allowance, and no forged header changes
whose it is. The one remaining risk is in `ARCHITECTURE.md` §5.21.

**Demo reset by hand on the live database** (2026-10-05), from the owner's
own shell against the direct endpoint, after a Neon snapshot branch was
taken. The dry run counted an empty ledger, `--yes` restored 8 accounts
and 10 transactions, and the live site showed both currencies balanced.
So on Neon the app's database role can disable the append-only trigger,
which the reset needs.

**#11, `chore/scheduled-demo-reset`**, schedules that reset daily on GitHub
Actions (§2, "The demo itself"). Checked on that branch, then on the live
database after merging; it changes no app code and adds no migration:

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `pytest` collection | **312 tests** |
| `pytest` (no database available) | 211 passed, 101 skipped |
| `pytest` (Postgres) | 312 passed in CI's `lint-and-test`; not run locally. The change touches no database code |
| New tests fail without their change | 22 deliberate breaks, one at a time, each failed a test: a `push` or `pull_request` trigger, `contents: write`, `cancel-in-progress: true`, another environment, `ENVIRONMENT=production`, the default timeout, `shell: sh`, the secret at job level, in a `run:` line or given to `pip install`, the host check removed or allowed to fail, the reset under `if: always()`, an unpinned action, another Python; and in the host check, each of its four refusals removed, the host printed unmasked, the URL printed |
| `python -m scripts.check_database_host` with `ENVIRONMENT=demo` and made-up URLs | the expected host passes and is printed with its endpoint id masked; a `-pooler` host is refused; an empty secret and `channel_binding=require` are refused by the settings guard; no password or endpoint id is printed |
| "Demo maintenance" run #1, manual, on `a083641` | **refused at the host check:** the environment secret named Neon's connection pooler (`-pooler`), so the reset step was skipped. The secret and Render's `DATABASE_URL` were then set to the direct endpoint |
| Run #2, manual | passed in about 40 seconds. The host check printed the direct endpoint, masked, and the reset deleted 8 accounts, 10 transactions and 18 events, then restored 8 accounts and 10 transactions |
| Run #3, the first scheduled one | passed the same way. It started at 00:10 UTC, 2 h 27 min after its 21:43 slot: GitHub's scheduling delay (README, "Why daily") |

**#8, `fix/client-ip-cloudflare`**, made the client address the visitor's
own behind Render and Cloudflare (§2, "Behind a proxy"), and removed #7's
diagnostic. Checked on that branch before merging; it adds no migration:

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `pytest` collection | **291 tests** |
| `pytest` (no database available) | 190 passed, 101 skipped |
| `pytest` (local Postgres 16) | 291 passed |
| New tests fail without their change | with the `CF-Connecting-IP` rule disabled, 6 tests fail. Removing each of its three conditions in turn (loopback peer, Cloudflare hop, exactly one header) fails 3, 4 and 1 tests |
| `python -m app.serve` under real uvicorn, Render-shaped headers from `127.0.0.1` | the client is the visitor; forged `X-Forwarded-For`, `True-Client-IP` and `X-Real-IP` leave it the visitor; a forged `CF-Connecting-IP` without a Cloudflare hop gives the caller's own address |
| `python -m scripts.check_cloudflare_ranges` | matches Cloudflare's 22 published ranges |
| `pip-audit`, Docker image build and smoke test | CI |

Checked for #5 (deploy readiness), at migration head `c2e8f4a61b07`:

| Check | Result |
|---|---|
| `alembic upgrade head` → `downgrade base` → `upgrade head`, then `alembic check` | clean, on a scratch database; `alembic check` also runs in CI |
| New tests fail without their change | each behaviour of the rate limit, the caps, the demo notice and the reset script was broken on purpose, one at a time, and a test failed every time. The startup guard's TLS and `FORWARDED_ALLOW_IPS` checks: all 22 refusal cases fail against the `app/config.py` without them |
| `scripts.reset_demo_data` on a migrated database | refused with `ENVIRONMENT=production`; only counted without `--yes`; with `--yes` removed a visitor's account, restored 8 accounts and 18 events from sequence 1, and left the append-only trigger enabled |
| Storage per write, Postgres 16 | account ≈ 1.1 KB; two-entry transaction ≈ 1.7 KB; largest transaction the body limit admits (470 entries) ≈ 91 KB. The source of the default caps. |
| `pip-audit`, Docker image build and smoke test | not run locally; CI's `lint-and-test` (ruff, `pip-audit --strict`, `alembic check`, the full suite on Postgres) and `docker-smoke` run them on the PR |

The 101 skips are not failures. Every database-backed test skips itself
unless `TEST_DATABASE_URL` is set, so **a green local run of 211 tests
means about a third of the suite never executed.** Do not read it as a
passing build. See §5 for the command that runs the real thing.

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
§5.16 have the reasoning). `tests/test_api.py` has 34 tests, 22 of them
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
instead of mounting an empty directory over the image. On the
Since #5, `lint-and-test` also runs `pip-audit --strict`, and `docker-smoke`
fails if the container runs as root. A third, scheduled workflow, "Cloudflare
ranges", compares `app/client_address.py`'s copy of Cloudflare's address
ranges with the published lists every Monday. A fourth, "Demo
maintenance", resets the public demo every night (below).

### Deploy readiness — merged (#5), live on Render

What a public demo on a container host needs, with the README's
"Deploying" section as the operator's guide:

- **Configuration a host can supply.** `DATABASE_URL` in any Postgres
  spelling, TLS via `sslmode` or `DATABASE_SSL`, `PORT`. `ENVIRONMENT=production`
  or `demo` refuses to start on the local development database, without
  TLS to Postgres (`require` or stricter), or with `FORWARDED_ALLOW_IPS`
  set to `*` or nothing.
- **A start command for hosts.** `python -m app.serve` migrates, then
  serves, in a container running as an unprivileged user. Migrating on
  start is one reason Keel must run as a single instance.
- **Behind a proxy.** On Render a request goes visitor → Cloudflare →
  Render's load balancer → Render's proxy on `127.0.0.1` → Keel, observed
  on the live service. `X-Forwarded-For` is believed only from
  `FORWARDED_ALLOW_IPS` (on Render
  `127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`, **not `*`**), read
  from the right to the Cloudflare edge. Before #8, that edge was the
  client, shared by every visitor behind it. Since #8, Cloudflare's
  `CF-Connecting-IP` is the client, believed only when the peer was
  loopback and that edge is in Cloudflare's published ranges. Verified on
  the live service with forged headers (§1).
  `app/client_address.py` has the rule, and `ARCHITECTURE.md` §5.21 says why
  no header can be forged and what risk is left.
- **HTTP hardening.** A 64 KiB body limit (413) and security headers,
  including a CSP whose nonce lets the posting form's one inline script run.
- **Public writes, bounded.** Every write, form or API, counts against a
  per-client allowance, 30 a minute by default. Past it the answer is a
  `429` with an exact `Retry-After`. Reads are not limited. The counts are
  in memory, which is a second single-instance assumption. Hard caps of
  200 accounts and 2000 transactions (`409 ledger_full`) keep a free 0.5 GB
  database from filling however many clients write. The defaults were sized
  from measured bytes per write.
- **The demo itself.** With `ENVIRONMENT=demo`, every page says it is a
  public demo that resets periodically, and
  `python -m scripts.reset_demo_data --yes` restores the demo data. It
  refuses in any other environment and counts only without `--yes`. It is
  the one sanctioned exception to the append-only log, done in a single
  transaction that re-enables the trigger. The "Demo maintenance" workflow
  runs it daily at 21:43 UTC on GitHub Actions, with `DATABASE_URL` from
  the `production-demo` environment, after
  `python -m scripts.check_database_host` confirms the host is the live
  endpoint. Key pruning isn't scheduled on the demo: the reset empties the
  keys. The README's "Scheduled maintenance" section has the setup.

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

**1. Finish the live deployment**
Keel is live on Render + Neon (§1). What is left:

- keep the repository active, or GitHub pauses the nightly reset after 60
  days without activity (README, "Scheduled maintenance"). The reset itself
  is set up and verified on the live database (§1);
- try `sslmode=verify-full` with Neon, which needs a CA bundle both drivers
  can find in `python:3.12-slim`;
- optionally, a `render.yaml` blueprint matching the live settings;
- stay on one instance, which both migrate-on-start and the in-memory rate
  limit assume.

**2. Authentication**
Every route is public. Fine for a demo, disqualifying otherwise.

**3. Round out the JSON API**
No single-account read, no transaction listing, no pagination and no
event-log endpoint yet (`ARCHITECTURE.md` §8 item 8).

**Done since the previous version of this list:** deploy readiness
(host-style configuration, a migrate-then-serve start command, a non-root
image, proxy headers, a body limit and security headers, the write rate
limit, caps on accounts and transactions, the demo notice and the reset
script), all on `chore/deploy-readiness`; and, merged, stable entry order
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
you are running 211 of 312 tests:

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
Its `keel_test` database is what the 2026-09-24, 2026-09-27 and
2026-09-29 test runs used, since Docker was not running. That server's session time zone is
`Asia/Calcutta`, which is what surfaced the date-filter bug:

```bash
export TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5433/keel_test
```

Maintenance tasks (each only reports without `--yes`):

```bash
docker compose exec app python -m scripts.rebuild_read_model --yes
docker compose exec app python -m scripts.backfill_account_events --yes
docker compose exec app python -m scripts.prune_idempotency_keys --yes
# the public demo only: refuses unless ENVIRONMENT=demo
python -m scripts.reset_demo_data --yes
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
