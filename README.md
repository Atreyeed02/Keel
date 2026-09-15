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

Then:
- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health

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

Early scaffold — core ledger schema, double-entry posting logic, and
health check are in place; transaction API endpoints and idempotency
middleware are next.

## Roadmap

- [ ] REST endpoints for posting/querying transactions
- [ ] Idempotency-key middleware (currently modeled in schema, not wired up)
- [ ] Alembic migrations
- [ ] Idempotent webhook ingestion
- [ ] Multi-provider payment orchestration
- [ ] Reconciliation engine
- [ ] Outbox pattern for reliable event publishing
- [ ] Observability (structured logs, metrics)
- [ ] CI/CD deployment pipeline
