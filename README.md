# Keel

**A double-entry ledger built on an append-only event log: every posting
must balance, and every balance can be rebuilt from history.**

**Live demo: [keel-htwz.onrender.com](https://keel-htwz.onrender.com)**.
It runs on Render's free tier, so the first visit after a quiet spell can
take about a minute to wake up. The demo data resets every night.

| | |
|---|---|
| ![The overview: the accounting equation, once per currency, with the totals on each side](docs/screenshots/overview.png) | ![The posting form, filled in with a balanced transaction: a debit and a credit of 12.00 USD](docs/screenshots/post-transaction.png) |
| **Overview.** The accounting equation for each currency, recomputed from the ledger | **Posting form.** Debits and credits must net to zero, per currency, before anything is written |
| ![The Cash account as a T-account: debits on the left, credits on the right, with each entry's other side](docs/screenshots/account-detail.png) | ![The event log: how it works, then the newest event, a posted transaction with its debit and credit](docs/screenshots/event-log.png) |
| **T-account.** Every entry on an account, with its normal side and the other side of each transaction | **Event log.** The source of truth: append-only, and every balance can be rebuilt from it |

## What makes this interesting

- **Rules enforced twice.** Balance and currency rules are checked in
  Python for readable errors, and by Postgres triggers, so they also hold
  for writes that bypass the app.
- **History can't be rewritten.** A trigger refuses UPDATE, DELETE and
  TRUNCATE on the event log. The balance tables are a projection of the
  log and can be dropped and replayed.
- **Retries are safe under concurrency.** Each idempotency key maps to
  exactly one committed effect, using a claim-first
  `INSERT … ON CONFLICT DO NOTHING`.
- **Money is exact.** Amounts are `NUMERIC(18,2)` and `Decimal`, never
  floats, and every transaction must balance per currency.
- **It teaches as it runs.** Glossary terms are explained inline, a Learn
  page works through examples, and account pages are drawn as T-accounts.
- **It runs like a real service.** Images are pinned by digest, CI checks
  are required, the server refuses to start with unsafe settings, and the
  demo resets itself every night.
- **Tested and gated.** 480+ automated tests, required CI checks on every
  change, and a before-and-after screenshot comparison on every PR.

## Tech stack

Python 3.12 · FastAPI on Starlette · SQLAlchemy 2.1 Core with asyncpg ·
Alembic · PostgreSQL 16 · Jinja2 server-rendered pages with two small
scripts · Docker · GitHub Actions · deployed on Render with Neon Postgres

## Why I built this

To explore the invariants a payments system has to get right: money never
appears or disappears, every state change is auditable and replayable, and
a retried request never double-charges.

## Quick start

### Running locally

```bash
cp .env.example .env
docker compose up --build
docker compose exec app python -m scripts.seed_demo_data   # optional: the demo data
```

The container runs the migrations before it starts serving. Then open
http://localhost:8000 (UI), http://localhost:8000/docs (OpenAPI) or
http://localhost:8000/health. More, including dev mode and the shipped
image: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#running-locally).

### Running tests

```bash
pip install -r requirements-dev.txt
docker compose up -d db
docker compose exec db createdb -U ledger ledger_test
export TEST_DATABASE_URL=postgresql+asyncpg://ledger:ledger@localhost:5432/ledger_test
pytest -v
ruff check .
```

483 tests. Without `TEST_DATABASE_URL`, the 161 that need PostgreSQL are
skipped, not failed. What each test file covers:
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#running-tests).

## How it's built

- **The event log is the source of truth.** Every change is appended to an
  append-only `events` table. The account, transaction and entry tables are
  a projection written in the same database transaction, and can be rebuilt
  from the log at any time.
  ([Why event sourcing](docs/HOW-IT-WORKS.md#why-event-sourcing),
  [rebuilds](docs/HOW-IT-WORKS.md#how-replay--rebuild-works))
- **Double-entry, enforced twice.** Debits must equal credits per currency,
  checked in Python for readable errors and again by PostgreSQL triggers.
  ([The guarantees](docs/HOW-IT-WORKS.md#the-guarantees-and-where-each-one-is-enforced))
- **Claim-first idempotency.** One key, one committed posting, even under
  concurrent retries. ([How idempotency works](docs/HOW-IT-WORKS.md#how-idempotency-works))
- **Plain layers.** FastAPI routes, a domain layer that never commits,
  SQLAlchemy Core, PostgreSQL.
  ([Architecture](docs/HOW-IT-WORKS.md#architecture),
  [database model](docs/HOW-IT-WORKS.md#database-model))
- **Two interfaces, one domain.** Server-rendered pages and a JSON API under
  `/api/`. ([docs/API.md](docs/API.md))
- **One container.** The image migrates, then serves. It runs on Render with
  Neon Postgres, deploys from `main` on every merge, and the demo resets
  nightly. ([docs/DEPLOYING.md](docs/DEPLOYING.md))

## Documentation

| Document | What it covers |
|---|---|
| [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md) | the design in brief: the guarantees, event sourcing, double-entry, idempotency, rebuilds, the layers and tables, the limitations |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | the full walkthrough: every design decision, file by file |
| [docs/API.md](docs/API.md) | the JSON API and the HTML routes |
| [docs/DEPLOYING.md](docs/DEPLOYING.md) | deploying: environment variables, proxies and Cloudflare, scheduled jobs and the nightly reset, the pre-deploy checklist, logs |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | running locally, the tests, the project layout, and the rules for changes in full |
| [CONTRIBUTING.md](CONTRIBUTING.md) | how a change lands, in one screen |
| [CHANGELOG.md](CHANGELOG.md) | releases |
| [docs/STATUS.md](docs/STATUS.md) | build status, and every pull request with its checks |

**Limitations.** No authentication, a minimal JSON API, one instance, and
logs but no metrics: [the full list](docs/HOW-IT-WORKS.md#limitations-and-future-work).
