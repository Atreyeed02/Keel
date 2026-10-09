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

> **Keel must run as a single instance.** Two things depend on it:
>
> - **Migrations run on startup.** Two instances starting at once would both
>   run `alembic upgrade head`, and Alembic takes no lock to stop them.
> - **The write rate limit is in memory.** Each instance keeps its own
>   counts, so every instance allows the full rate, and a restart forgets
>   them.
>
> Set the host's instance count to 1 and turn off autoscaling. Running more
> than one needs both fixed first: migrations as a release step
> (`alembic upgrade head`, then start instances with
> `python -m app.serve --no-migrate`), and the rate limit's counts in a
> shared store such as Redis, which Keel does not support yet. A host that
> starts the new instance before stopping the old one briefly runs two
> during a deploy. Only the new one migrates, but the old one keeps serving
> on the schema the new one is changing.

**Health check:** path `/health`. It answers **200 even when the database
is down**, with `{"status":"degraded","db":"down"}`, so a check that looks
only at the status code stays green on a broken deployment. Configure the
host to require the body `{"status":"ok","db":"up"}`. If your host can only
check the status code, know that it will not notice a lost database.

### Environment variables

| Variable | Required | What it does |
|---|---|---|
| `DATABASE_URL` | **yes** | The Postgres URL. `postgres://`, `postgresql://` and `postgresql+asyncpg://` all work, with or without `?sslmode=...`. The only query parameters accepted are `sslmode` and `channel_binding`, and `channel_binding` only as `prefer` or `disable`. The app's driver, asyncpg, can't do channel binding, so `channel_binding=require`, which Neon puts in the URLs it gives you, stops startup: remove it or change it to `prefer`. Any other parameter (`application_name`, `connect_timeout`, `options`, ...) also stops startup, because asyncpg would fail on every connection. |
| `ENVIRONMENT` | **yes**: `production`, or `demo` for the public demo | Either refuses to start if `DATABASE_URL` is unset or is the local `ledger:ledger@db` default, if the database connection is not encrypted (`DATABASE_SSL`, below), or if `FORWARDED_ALLOW_IPS` is `*` or empty. `demo` also shows a notice on every page saying this is a public demo that resets nightly, linking to the overview's "try it" steps, and is the only environment `scripts.reset_demo_data` will run in. |
| `PORT` | set by most hosts | Where the server listens. Default 8000. |
| `FORWARDED_ALLOW_IPS` | **yes** behind a proxy | Proxies whose `X-Forwarded-For` / `-Proto` are believed, as addresses and networks. It decides who the client is: in the logs, and for the write rate limit. On Render: `127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16` (below). Default `127.0.0.1`. `production` and `demo` refuse to start on `*`, alone or in a list, or on an empty value. |
| `WRITE_RATE_LIMIT`, `WRITE_RATE_WINDOW_SECONDS` | no | Writes (any method but `GET`, `HEAD`, `OPTIONS`) one client may make in any window, forms and API alike; past that, `429` with `Retry-After`. Reads are not limited. Default 30 per 60 seconds; `WRITE_RATE_LIMIT=0` turns it off. |
| `DATABASE_SSL` | **yes** in `production` and `demo`, unless the URL has `sslmode` | `disable`, `allow`, `prefer`, `require`, `verify-ca` or `verify-full`. If the URL has an `sslmode` too, the stricter of the two is used, so neither can weaken the other. Unset: whatever the URL says, else the driver default. `production` and `demo` refuse to start unless the result is `require`, `verify-ca` or `verify-full`: with no mode, or `disable`, `allow` or `prefer`, the connection can be plaintext. |
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

### Behind Render and Cloudflare: `FORWARDED_ALLOW_IPS`

On Render, a visitor's request goes visitor → Cloudflare → Render's load
balancer → Render's proxy inside the container, on `127.0.0.1` → Keel. Each
hop appends to `X-Forwarded-For`, so it arrives as `[whatever the client
sent..., visitor, Cloudflare edge, Render 10.x hop]`, and Cloudflare sets
`CF-Connecting-IP` to the visitor's address. This was observed on the live
service on 2026-10-03. Set

```
FORWARDED_ALLOW_IPS=127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
```

- `127.0.0.1` is Render's proxy, the peer of every public request.
- `10.0.0.0/8` covers the hop Render's load balancer appends after
  Cloudflare's. The other two private ranges are there for the same kind of
  hop. No internet client connects from any of them.

Keel reads `X-Forwarded-For` from the right, past those hops, and stops at
the first one it doesn't trust: the Cloudflare edge. Because that hop is in
Cloudflare's published ranges and the peer was loopback, Keel takes the client
from `CF-Connecting-IP`, which only Cloudflare sets. A client that sends its
own gets a 403 from Cloudflare. `True-Client-IP` and `X-Real-IP` are never
read. `app/client_address.py` has the rule, and `docs/ARCHITECTURE.md` §5.21
explains why none of these headers can be forged, including by reaching
Render around Cloudflare, and what risk is left.

**Not `*`.** With `*`, the header's *leftmost* address is taken, and the
client writes that itself. Any client could then name a new address with
every request and never be rate limited.

**Cloudflare's ranges** are copied into `app/client_address.py`. The
"Cloudflare ranges" workflow compares them with Cloudflare's published lists
every Monday and fails when they differ. Run
`python -m scripts.check_cloudflare_ranges` to see what changed. A stale
copy fails safe: visitors behind an edge in a new range share that edge's
address until the copy is updated.

**Check it after a deploy.** From PowerShell, find your public address,
then send a write with forged headers:

```powershell
$KEEL = "https://<service>.onrender.com"
curl.exe -s https://api.ipify.org          # your public address
curl.exe -s -o NUL -w "%{http_code}\n" -X POST -H "X-Forwarded-For: 192.0.2.1" -H "True-Client-IP: 192.0.2.2" -H "X-Real-IP: 192.0.2.3" "$KEEL/api/transactions"
```

The write is refused with a 400 (`missing_idempotency_key`). That's fine:
it's still logged, and still counted by the rate limit. Find that request's
`request.completed` line in Render's logs and look at `client`. Compare it
with the address `api.ipify.org` showed from the same machine and shell; a
network with several exits can show different ones to different programs.

| `client` is | Means | Do |
|---|---|---|
| your own public address | **Pass.** | Nothing. |
| `192.0.2.1`, `.2` or `.3` | **Fail: a forged header was believed.** Any client can pick its own address and a fresh write allowance, so the rate limit protects nothing (the caps still bound the database). | Keel needs a code change; Render's chain has changed. |
| a Cloudflare address (e.g. `172.64.x`–`172.71.x`), with `"scheme": "https"` | **Fail: `CF-Connecting-IP` was not used.** Every visitor behind that edge shares one allowance. | Run `python -m scripts.check_cloudflare_ranges`. If the edge's range is missing, update `CLOUDFLARE_NETWORKS`. |
| a `10.x` address | **Fail: `10.0.0.0/8` is not trusted**, so Render's hop is taken for the client. | Set `FORWARDED_ALLOW_IPS` as above, and redeploy. |
| `127.0.0.1`, with `"scheme": "http"` | **Fail: `127.0.0.1` is not trusted**, or the setting is not in effect. Every client shares one allowance. | Set `FORWARDED_ALLOW_IPS` as above, and redeploy. |

Adding `-H "CF-Connecting-IP: 192.0.2.4"` to the request should get a 403
from Cloudflare (`Server: cloudflare`) before it reaches Keel.

A passing line looks like this:

```jsonc
{"ts": "...", "level": "INFO", "logger": "keel", "event": "request.completed", "request_id": "...", "method": "POST", "path": "/api/transactions", "status": 400, "client": "203.0.113.50", "scheme": "https", "duration_ms": 4.1}
```

### Scheduled and one-off jobs

Run these with the same image and environment as the app, from the host's
cron or scheduled-job feature, or its one-off shell:

| Command | When |
|---|---|
| `python -m scripts.prune_idempotency_keys --yes` | **daily**, on a ledger that isn't reset. Deletes idempotency keys older than 30 days; without it the table grows forever. Not scheduled on the public demo, whose daily reset empties the table (below). |
| `python -m scripts.seed_demo_data` | once, on an empty database, if you want the demo data. It refuses to touch a ledger that already has accounts. |
| `python -m scripts.reset_demo_data --yes` | **public demo only**, daily, scheduled by the "Demo maintenance" workflow (below). Deletes everything visitors wrote and restores the demo data, in one transaction. Refuses unless `ENVIRONMENT=demo`, and only counts without `--yes`. |
| `python -m scripts.rebuild_read_model --yes` | only to repair the read model from the event log. Safe while serving; postings wait for it. |

### Scheduled maintenance: the daily demo reset

The "Demo maintenance" workflow (`.github/workflows/demo-maintenance.yml`)
resets the public demo every day at 21:43 UTC by running
`python -m scripts.reset_demo_data --yes` on GitHub Actions, since Render's
free tier has no cron jobs. The job connects to Neon directly, so it neither
needs nor wakes the Render service.

**Why daily.** At the default write limit, one client can fill the
200-account cap in about 7 minutes and the 2000-transaction cap in about an
hour. After that every write is refused with a `409` until the next reset. A
daily reset keeps a full or vandalised demo to less than a day, and a
visitor's own entries last a day at most. A run takes about a minute of
runner time, which is free in a public repository, and wakes the database
briefly. The minute is off the hour because GitHub delays scheduled runs
most at the top of the hour, and under heavy load can drop some. A dropped
run is made up the next day.

**Why key pruning isn't scheduled on the demo.** The reset empties
`idempotency_keys`, and `prune_idempotency_keys` deletes only keys over 30
days old, so on a ledger reset daily it never finds one. A ledger that isn't
reset still needs it daily.

**Setting it up.** The job reads `DATABASE_URL` from a GitHub environment,
not a repository secret. Do this once, before the workflow's first run:

1. In the repository, **Settings → Environments → New environment**. Name it
   `production-demo` and choose **Configure environment**.
2. Under **Deployment branches and tags**, choose **Selected branches and
   tags**, then **Add deployment branch or tag rule**, with the name pattern
   `main`. A run started from any other branch then can't read the secret.
3. Leave **Required reviewers** and **Wait timer** off. Either would hold
   every nightly run until someone approved it.
4. Under **Environment secrets**, choose **Add environment secret**. Name:
   `DATABASE_URL`. Value: the same URL Render uses, for Neon's direct
   endpoint (no `-pooler` in the host), with `sslmode=require` and without
   `channel_binding=require`.
5. Under **Settings → Secrets and variables → Actions**, check that there is
   no repository secret named `DATABASE_URL`. Every workflow could read one.

When the database password changes, update this secret as well as Render's.

**Keeping the URL out of the logs.** The repository is public, and so are
its workflow logs.

- Only the two steps that use `DATABASE_URL` get it, in their own `env:`:
  the host check and the reset. Checkout, Python setup and `pip install` run
  without it.
- No `run:` line mentions it. Python reads it from the environment, so a
  traced shell (`set -x`) shows the command, not the URL. GitHub also masks
  the secret's value in logs, debug logs included, but nothing relies on
  that.
- Before the reset, `python -m scripts.check_database_host` prints the host
  with the Neon endpoint's random ID masked, and stops the run unless the
  host starts with the live endpoint's name, is in `ap-southeast-1` and isn't
  the connection pooler. A secret copied from a snapshot branch or for the
  pooler fails there, before anything connects. So does a missing secret.
- `tests/test_demo_maintenance.py` fails CI if the workflow gains another
  trigger or wider permissions, if the secret reaches any other step or any
  `run:` line, or if the reset stops depending on a passing host check.

**When a run fails.** GitHub emails the person who last changed the
workflow's `cron` line, or, for a manual run, whoever started it. Once a
paused workflow has been re-enabled, it emails whoever re-enabled it
instead. Whether the email arrives depends on that person's **Settings →
Notifications → System → Actions**. A failed reset changes nothing: it is
one transaction, and rolls back whole. Open the run, read the failed step,
and use **Re-run jobs** once the cause is fixed, or leave it to the next
night's run.

**The 60-day pause.** In a public repository, GitHub disables scheduled
workflows after 60 days without repository activity, this one and
"Cloudflare ranges" alike. A disabled workflow doesn't run, so it has no
failure to email: no email doesn't mean the reset ran. Push a commit at
least every 60 days. If it has been paused, re-enable it under **Actions →
Demo maintenance → Enable workflow**, or with
`gh workflow enable demo-maintenance.yml`. Don't keep it alive with a bot
that commits: that needs write access to the repository.

**A visitor during a reset.** The reset is one transaction that starts by
locking the five ledger tables. Until it commits:

- Page loads and writes wait for it rather than fail. That takes a few dozen
  round trips from GitHub's runner to the database in Singapore, so seconds
  rather than milliseconds. The time of the run's "Reset the demo data"
  step is an upper bound.
- A posting already under way finishes first, and is then wiped with
  everything else.
- A posting that waited is refused once the reset commits, with
  `no account exists with id ...` (`422`): the reseeded accounts have new
  IDs. An account created meanwhile lands in the fresh demo. Reloading the
  page shows the fresh demo.
- Links to old transactions return `404`. Resubmitting an old form can't
  post twice, because the accounts it names are gone.
- Rarely, a page load and the reset deadlock, and Postgres aborts one of
  them: the visitor gets one error page, or the reset rolls back and the run
  fails (above).
- The write rate limit's counts are in the app's memory, and aren't reset.

**Running it by hand.** **Actions → Demo maintenance → Run workflow**, with
branch `main`, or `gh workflow run demo-maintenance.yml --ref main` and then
`gh run watch`. It resets the live demo, exactly as the nightly run does.

### Pre-deploy checklist

- [ ] `ENVIRONMENT=production` (or `demo` for the public demo) and `DATABASE_URL` set on the host.
- [ ] `DATABASE_SSL=require` (or stricter), or `sslmode=require` in `DATABASE_URL`. The app refuses to start without one.
- [ ] `DATABASE_URL` has no query parameters other than `sslmode` and `channel_binding=prefer`. Neon's URLs end in `&channel_binding=require`: remove it or change it to `prefer`, or the app refuses to start.
- [ ] `FORWARDED_ALLOW_IPS` set to the proxies' addresses and networks (on Render, `127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`, above). The app refuses to start on `*` or an empty value.
- [ ] Health check on `/health`, checking the body, not just the status.
- [ ] Exactly one instance, with autoscaling off (above: migrations on startup, and the in-memory rate limit).
- [ ] A daily job for `python -m scripts.prune_idempotency_keys --yes`, unless the ledger is reset more often than every 30 days, as the public demo is.
- [ ] Public demo: the `production-demo` environment and its `DATABASE_URL` secret exist, and one manual run of "Demo maintenance" has passed (above).
- [ ] `DB_POOL_SIZE + DB_MAX_OVERFLOW` times the number of instances is under the database's connection limit.
- [ ] CI is green on the commit being deployed: tests, `alembic check`, `pip-audit`, and the image smoke test.
- [ ] After the first deploy: `/health` returns `{"status":"ok","db":"up"}`, and the response headers include `Content-Security-Policy`.
- [ ] After the first deploy: a write with forged `X-Forwarded-For`, `True-Client-IP` and `X-Real-IP` is logged with your own address as `client` (above).

## Running tests

```bash
pip install -r requirements.txt
docker compose up -d db
docker compose exec db createdb -U ledger ledger_test
export TEST_DATABASE_URL=postgresql+asyncpg://ledger:ledger@localhost:5432/ledger_test
pytest -v
ruff check .
```

397 tests. Point `TEST_DATABASE_URL` at a scratch database, not the one the
app runs on: the fixtures drop and recreate every table around each test,
with `metadata.create_all()`, so no migrations need to be applied first.
They refuse to run on a database alembic has migrated (one with an
`alembic_version` table). Dropping the app tables there would leave it
stamped "at head" with nothing in it, and `alembic upgrade head` would then
do nothing.

Without `TEST_DATABASE_URL`, the 133 database-backed tests are **skipped,
not failed**. A green run of the remaining 264 is partial coverage:

```
SKIPPED [1] tests/test_ledger_pages.py: set TEST_DATABASE_URL to run PostgreSQL page integration tests
```

| File | Covers |
|---|---|
| `test_ledger_domain.py` | the balance invariant and entry validation, no database |
| `test_ledger_pages.py` | every page, inline errors, filters (UTC day boundaries whatever the session time zone), pagination, `sequence` ordering, the search's trigram index |
| `test_transactions_pages.py` | the list's amount per currency (the debit total, never both sides added), its accounts by side, the filter chips, the pager's line and a page past the end, the form's empty dates; a transaction's page: what each entry does, totals and the balance per currency, the notes, the link to its event |
| `test_idempotency.py` | retries, 409 on key reuse, key release after rejection, concurrent duplicates and conflicts, key retention and its CLI |
| `test_ledger_invariants.py` | every DB trigger against writes that bypass the app, atomic rollback, log ↔ read-model agreement, trial balance |
| `test_rebuild.py` | round trip, recovery from corruption, repeatability, rebuild alongside a live posting, backfilling a legacy ledger, entry order across a rebuild, payload schema versions, both CLIs |
| `test_observability.py` | request ids, JSON log format, ledger identifiers on log lines |
| `test_health.py` | `/health` always answers |
| `test_schema_guard.py` | the fixtures refuse to wipe a migrated database |
| `test_api.py` | every JSON API status code, the error shape, string amounts, replays, concurrent duplicate requests |
| `test_deploy_config.py` | `DATABASE_URL` in every host spelling, TLS, `PORT`, the production and demo guards, the start command |
| `test_hardening.py` | the body size limit (a page for a form, JSON for the API), security headers, the page CSP with no nonce, no inline scripts or styles on any page, static file types |
| `test_stylesheet.py` | every animation stops by itself; reduced motion stops all of them |
| `test_learn.py` | `/learn` without a database, every term's section there, the quiz, the `term` macro, each page's terms defined once |
| `test_posting_form.py` | every refusal of the posting form worded by its rule, the balance panel as the server draws it, add and remove without JavaScript, the resubmission notice, typed values never logged |
| `test_overview.py` | the accounting equation per currency, and saying so if it didn't hold; on the demo only, the "try it" steps' links to the filled-in form, their notes kept through a redraw and a refusal, and the first step posting with the equation still holding |
| `test_proxy_headers.py` | which forwarded headers are believed; the Render chain behind Cloudflare, with `CF-Connecting-IP` believed only when it can be; forged headers, including around Cloudflare, change neither the client nor the write limit |
| `test_rate_limit.py` | the write rate limit: 429 and an exact `Retry-After`, forms and API sharing one allowance, reads unlimited, IPv6 per /64 |
| `test_capacity.py` | the account and transaction caps through both interfaces, replays at the cap, uncapped scripts |
| `test_demo.py` | the demo notice on every page, and the reset script's refusals, restore and rollback |
| `test_demo_maintenance.py` | the database host check, and the scheduled reset's workflow: its triggers, permissions, secret handling, step order, pinned actions and Python version |

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
| `GET` | `/` | the accounting equation per currency (Assets = Liabilities + Equity + (Revenue − Expenses)), totals per currency, balances per account (normal-side signed), recent transactions with each one's amount per currency; on the demo, three "try it" steps that open the posting form filled in |
| `GET` | `/transactions` | all transactions, newest first, one card each: its number, accounts by side and amount per currency (its debit total, which is also its credit total). `q` (description search), `date_from`, `date_to` (left empty, no date filter), `page`; the filters in force are chips, each a link without it |
| `GET` | `/transaction-detail/{id}` | one transaction's debit and credit entries, what each does to its account, the totals and the balance per currency, and its event, linked to its page of the event log; `?already=1` after a resubmission says it was already posted |
| `GET` | `/event-log` | the raw event log, newest first, paginated |
| `GET` | `/learn` | how double-entry works, with worked examples from the demo's data; needs no database |
| `GET` / `POST` | `/accounts/new`, `/accounts` | create an account: `name`, `account_type`, `currency` |
| `GET` / `POST` | `/post-transaction` | post a transaction: repeated `account_id` / `entry_type` / `amount` / `currency` fields, plus `description` and `submission_key`. `GET` takes the same fields, plus `add_line` or `remove_line`, to draw the form again with a line more or fewer: "Add line" and "Remove" without JavaScript. On the demo, `try=1`, `2` or `3` (from the overview's steps) shows that step's note, kept while the form is redrawn or refused |
| `GET` | `/health` | `{"status":"ok","db":"up"}`, or `degraded`/`down` (never raises) |

`POST /post-transaction` answers `302` to the transaction's detail page on
success, and on an idempotent retry to the same page with `?already=1`. On
invalid input it is a `422`, the form re-rendered with what was typed and
every problem worded by its rule and marked on its line. When a
`submission_key` is reused with a different request it is a `409`, the form
explaining what happened and carrying a new key. Both forms re-render with
an explanation and a `409` when the ledger is at `MAX_ACCOUNTS` or
`MAX_TRANSACTIONS`, and a form refused by the write limit or the body limit
gets a page saying so (`429`, `413`); the JSON API keeps its JSON for all of
these.

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
├── client_address.py     who the client is, behind Render and Cloudflare
├── glossary.py           the terms the pages define in place, each linked to /learn
├── posting_messages.py   the posting form's wording: each refusal, the live balance panel
├── overview.py           the overview's equation per currency, and the demo's "try it" steps
├── transactions_view.py  each transaction's amount per currency and accounts by side; the list's
│                         filter chips and pager; what each entry does, on a transaction's page
├── main.py               routes and wiring
└── templates/, static/   Jinja2 pages; the stylesheet, self-hosted fonts and icons
alembic/versions/         10 migrations
scripts/                  seed_demo_data.py, reset_demo_data.py, rebuild_read_model.py,
                          backfill_account_events.py, prune_idempotency_keys.py,
                          check_cloudflare_ranges.py, check_database_host.py
tests/                    397 tests; see above
docs/                     ARCHITECTURE.md (full walkthrough), STATUS.md (build status)
```

## Limitations and future work

What this does **not** do today. The full, maintained list is
[docs/ARCHITECTURE.md §8](docs/ARCHITECTURE.md#8-what-still-needs-doing).

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
