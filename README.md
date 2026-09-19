
# ledger-service

An event-sourced, double-entry ledger service — the accounting core you'd
put behind a payments platform, built to explore the same invariants a
system like Juspay has to get right: money never appears or disappears,
every state change is auditable, and retried requests never double-charge.

## Why this exists

Most CRUD ledgers store a running `balance` column and update it in place.
That's fast until something goes wrong — a bug, a race, a replayed
webhook — and now there's no way to reconstruct *how* the balance got
wrong, because the history was never kept.

This service takes the opposite approach:

- **Event-sourced**: every change is appended to an immutable `events`
  log first. The ledger's current state is a projection of that log, not
  the source of truth itself — so it's always rebuildable and always
  auditable.
- **Double-entry**: every transaction is a set of debit/credit entries
  that must net to zero per currency, enforced in application code before
  anything is written (see `app/domain/ledger.py`). No transaction is
  ever half-written.
- **Idempotent by construction**: every mutating request carries an
  idempotency key; retries return the original result instead of
  re-applying the transaction.

## Architecture

For a full walkthrough — the accounting and event-sourcing concepts, what
every file does, and a frank list of what's still missing — see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

```
Client
  │
  ▼
FastAPI (app/api)
  │
  ▼
Domain layer (app/domain) ── enforces double-entry invariant
  │
  ▼
SQLAlchemy Core (app/db) ── explicit statements, no ORM session magic
  │
  ▼
PostgreSQL
  ├── events            (append-only source of truth)
  └── transactions /
      ledger_entries     (derived read model, rebuildable from events)
```

## Tech stack

| Piece | Choice | Why |
|---|---|---|
| API | FastAPI | async-native, OpenAPI docs for free |
| DB access | SQLAlchemy 2.0 **Core** (not ORM) | double-entry writes need explicit, predictable statements — no flush-order surprises |
| Driver | asyncpg | fastest async Postgres driver available |
| Validation | Pydantic v2 | request/response schemas + domain input validation in one place |
| DB | PostgreSQL | JSONB for event payloads, real transactions, `NUMERIC` for money |
| Packaging | Docker Compose | one-command local env, matches how this would actually deploy |

## Quickstart

```bash
cp .env.example .env
docker compose up --build
```

The application container runs `alembic upgrade head` before starting Uvicorn, so
the database schema is created automatically. To run it yourself: `alembic upgrade head`.

Optionally populate a demo chart of accounts and a handful of transactions
(including one in EUR, to show the per-currency balance rule) — it's a no-op
if the ledger already has data:

```bash
docker compose exec app python -m scripts.seed_demo_data
```

Then:
- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health

### Dev mode vs. the shipped image

There are two compose files, and which ones are in play changes what is
actually running:

| Command | Files used | What runs |
|---|---|---|
| `docker compose up --build` | base + override | the host's `./app`, bind-mounted over the image — edits appear without a rebuild |
| `docker compose -f docker-compose.yml up --build` | base only | exactly what the image contains |

Compose merges `docker-compose.override.yml` automatically whenever it is
present, so the first form is the default in a checkout and needs no flag.
The second is what CI's `docker-smoke` job runs (via `COMPOSE_FILE`) and what
a deployment would use; it is the only one that proves the image is
self-contained. If you change the Dockerfile and want to know the change
really landed in the image, use the second form — under the first, a stale
`COPY app/` is invisible because the mount covers it.

Both give the `app` service a healthcheck against `/health` that asserts the
exact body `{"status":"ok","db":"up"}`, so a container whose migrations failed
never reports healthy. It is written in Python rather than curl or wget
because `python:3.12-slim` ships neither.

## Running tests locally (without Docker)

```bash
pip install -r requirements.txt
pytest -v
```

The test suite currently covers the double-entry balance invariant
(`tests/test_ledger_domain.py`) and endpoint reachability
(`tests/test_health.py`) without requiring a live database.

## Project layout

```
app/
├── api/       # FastAPI routers — HTTP concerns only
├── domain/    # business logic — double-entry invariant lives here
├── db/        # SQLAlchemy Core table defs + engine
├── config.py  # settings via pydantic-settings
└── main.py    # app wiring
tests/
```

## Status

Working end to end — the ledger can be driven entirely through the
browser, and CI proves the shipped container does it too. What exists
today:

- **Five server-rendered pages** — balance overview, paginated event log,
  transaction-posting form, transaction detail, and account creation.
- **Double-entry posting with idempotency** — the balance invariant is
  enforced per currency before anything is written, and every submission
  carries a key, so a resubmitted form returns the original transaction
  instead of posting it twice.
- **Account creation** with validated account types and currency codes.
- **Alembic migrations**, applied automatically on container start.
- **A demo seed script** (`scripts/seed_demo_data.py`) that writes through
  the domain layer rather than by raw `INSERT`, so a seeded database has
  the same event log a hand-typed one would.
- **CI** — `ruff` and `pytest` against a live PostgreSQL, plus a
  `docker-smoke` job that builds the image, boots the stack, confirms
  migrations ran, and drives the real account-creation → posting →
  overview flow over HTTP.

## Roadmap

**Known gaps in what's built** are tracked in
[docs/ARCHITECTURE.md §8](docs/ARCHITECTURE.md#8-what-still-needs-doing),
not duplicated here — that list is maintained next to the code it
describes, and a second copy would only drift out of date. It's a frank
inventory: correctness limits, robustness work the demo gets away with
skipping, and build/tooling loose ends.

**Deliberately out of scope.** These are the layers a payments platform
puts *around* a ledger. None of them were part of this build; they're
listed to mark the boundary, not as planned work:

- Idempotent webhook ingestion
- Multi-provider payment orchestration
- Reconciliation engine
- Outbox pattern for reliable event publishing
- Observability (structured logs, metrics, tracing)
- CI/CD deployment pipeline
