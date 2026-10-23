# Developing Keel

For contributors: running Keel locally, the tests, the project layout,
and the rules every change follows, in full.
[CONTRIBUTING.md](../CONTRIBUTING.md) is the short version.

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

## Running tests

```bash
pip install -r requirements-dev.txt
docker compose up -d db
docker compose exec db createdb -U ledger ledger_test
export TEST_DATABASE_URL=postgresql+asyncpg://ledger:ledger@localhost:5432/ledger_test
pytest -v
ruff check .
```

483 tests. `requirements-dev.txt` installs everything in `requirements.txt`
(what the image runs) plus the test and lint tools, which the image never
contains. Point `TEST_DATABASE_URL` at a scratch database, not the one the
app runs on: the fixtures drop and recreate every table around each test,
with `metadata.create_all()`, so no migrations need to be applied first.
They refuse to run on a database alembic has migrated (one with an
`alembic_version` table). Dropping the app tables there would leave it
stamped "at head" with nothing in it, and `alembic upgrade head` would then
do nothing.

Without `TEST_DATABASE_URL`, the 161 database-backed tests are **skipped,
not failed**. A green run of the remaining 322 is partial coverage:

```
SKIPPED [1] tests/test_ledger_pages.py: set TEST_DATABASE_URL to run PostgreSQL page integration tests
```

| File | Covers |
|---|---|
| `test_ledger_domain.py` | the balance invariant and entry validation, no database |
| `test_ledger_pages.py` | every page, inline errors, filters (UTC day boundaries whatever the session time zone), pagination, `sequence` ordering, the search's trigram index |
| `test_event_log_page.py` | each event in words from its own payload: a posted transaction's entries by name in their recorded order and its totals per currency, an opened account's type and normal side; the raw event folded away; the explainer, its example and the reset sentence only on the demo; the pager |
| `test_transactions_pages.py` | the list's amount per currency (the debit total, never both sides added), its accounts by side, the filter chips, the pager's line and a page past the end, the form's empty dates; a transaction's page: what each entry does, totals and the balance per currency, the notes, the link to its event |
| `test_account_detail.py` | an account's page as a T-account: oldest first across both sides, each column's count and total, each entry's other side, the sum that gives the balance (below zero included), paging, the 404 page for an unknown id |
| `test_idempotency.py` | retries, 409 on key reuse, key release after rejection, concurrent duplicates and conflicts, key retention and its CLI |
| `test_ledger_invariants.py` | every DB trigger against writes that bypass the app, atomic rollback, log ↔ read-model agreement, trial balance |
| `test_rebuild.py` | round trip, recovery from corruption, repeatability, rebuild alongside a live posting, backfilling a legacy ledger, entry order across a rebuild, payload schema versions, both CLIs |
| `test_observability.py` | request ids, JSON log format, ledger identifiers on log lines |
| `test_health.py` | `/health` always answers |
| `test_schema_guard.py` | the fixtures refuse to wipe a migrated database |
| `test_api.py` | every JSON API status code, the error shape, string amounts, replays, concurrent duplicate requests |
| `test_deploy_config.py` | `DATABASE_URL` in every host spelling, TLS, `PORT`, the production and demo guards, the start command, the base images from the mirror pinned by digest, and the mirror workflow |
| `test_head.py` | `HEAD` answered like `GET`, without the body, on every page and API read |
| `test_hardening.py` | the body size limit (a page for a form, chunked or not, JSON for the API), security headers, the page CSP with no nonce, no inline scripts or styles on any page, static file types |
| `test_stylesheet.py` | every animation stops by itself; reduced motion stops all of them; no selector is styled twice by accident |
| `test_learn.py` | `/learn` without a database, every term's section there, the quiz, the `term` macro, each page's terms defined once |
| `test_posting_form.py` | every refusal of the posting form worded by its rule, the balance panel as the server draws it, add and remove without JavaScript, the resubmission notice, typed values never logged |
| `test_overview.py` | the accounting equation per currency, and saying so if it didn't hold; on the demo only, the "try it" steps' links to the filled-in form, their notes kept through a redraw and a refusal, and the first step posting with the equation still holding |
| `test_proxy_headers.py` | which forwarded headers are believed; the Render chain behind Cloudflare, with `CF-Connecting-IP` believed only when it can be; forged headers, including around Cloudflare, change neither the client nor the write limit |
| `test_rate_limit.py` | the write rate limit: 429 and an exact `Retry-After`, forms and API sharing one allowance, reads unlimited, IPv6 per /64 |
| `test_capacity.py` | the account and transaction caps through both interfaces, replays at the cap, uncapped scripts |
| `test_demo.py` | the demo notice on every page, and the reset script's refusals, restore and rollback |
| `test_demo_maintenance.py` | the database host check, and the scheduled reset's workflow: its triggers, permissions, secret handling, step order, pinned actions and Python version |

CI (`.github/workflows/ci.yml`) runs ruff and the full suite against
`docker-compose.yml`'s PostgreSQL. A separate `docker-smoke` job builds the
image, boots the stack, confirms migrations ran and drives account
creation → posting → overview over HTTP. Both pull their base images from
the repository's mirror on ghcr.io, which the "Mirror base images" workflow
(`.github/workflows/mirror-base-images.yml`) fills from Docker's official
images every Sunday, a day before Dependabot looks for new digests.

## Project layout

```
app/
├── api/                  JSON API: accounts.py, transactions.py, errors.py, serialize.py; health.py
├── domain/               business rules; nothing here commits
│   ├── ledger.py         double-entry validation and posting
│   ├── idempotency.py    claim-first idempotent posting
│   ├── accounts.py       account validation and creation
│   ├── rebuild.py        replay events into the read model
│   ├── reads.py          balances, entries and transaction lookups shared by pages and API
│   ├── capacity.py       the caps on total accounts and transactions
│   └── account_types.py, errors.py
├── db/                   SQLAlchemy Core tables, triggers, engine
├── observability.py      JSON logging, request-id middleware
├── security.py           body size limit, security headers
├── head.py               HEAD on every route that answers GET
├── ratelimit.py          per-client write rate limit
├── client_address.py     who the client is, behind Render and Cloudflare
├── glossary.py           the terms the pages define in place, each linked to /learn
├── posting_messages.py   the posting form's wording: each refusal, the live balance panel
├── overview.py           the overview's equation per currency, and the demo's "try it" steps
├── event_log.py          each event in the event log in words, from its own payload
├── account_view.py       an account's page in words: why its normal side, its other sides, its sum
├── transactions_view.py  each transaction's amount per currency and accounts by side; the list's
│                         filter chips and pager; what each entry does, on a transaction's page
├── main.py               routes and wiring
└── templates/, static/   Jinja2 pages; the stylesheet, self-hosted fonts and icons
alembic/versions/         10 migrations
scripts/                  seed_demo_data.py, reset_demo_data.py, rebuild_read_model.py,
                          backfill_account_events.py, prune_idempotency_keys.py,
                          check_cloudflare_ranges.py, check_database_host.py
tests/                    483 tests; see above
requirements.txt          what the image runs
requirements-dev.txt      that, plus pytest, pytest-asyncio, httpx and ruff
docs/                     HOW-IT-WORKS.md (the design in brief), ARCHITECTURE.md (full
                          walkthrough), API.md, DEPLOYING.md, DEVELOPMENT.md (this file),
                          STATUS.md (build status), screenshots/ (the README's)
CHANGELOG.md              releases
CONTRIBUTING.md           how a change lands, in short
```

## Deployment constraints

Every rule below exists because of how the live demo runs: one container on
a small instance, migrating its own database when it starts, redeployed on
every merge.

1. **Migrations must work with the previous version of the code.**
   Migrations run when the new version starts, while the previous version
   is still serving requests. Adding a table, a column or an index is fine.
   Renaming or dropping something, or adding `NOT NULL` to an existing
   column without a default, must be split across two pull requests: the
   first stops using the old shape, and a later one removes it. If a
   migration can't be made backward compatible, raise it in an issue or a
   draft pull request before writing it.
2. **One instance only.** Startup migrations and the in-memory write rate
   limit (`app/ratelimit.py`) both assume exactly one running instance.
   Don't add anything that assumes several. If a change would need more
   than one instance, raise it before building it.
3. **Keep the startup contract.**
   - The image starts with `python -m app.serve`.
   - The app binds to `$PORT` on `0.0.0.0` and serves `GET /health` at
     exactly `/health`.
   - The `Dockerfile` stays at the repository root, with every `COPY` path
     relative to the root.
   - The app runs as the unprivileged `keel` user.

   The host's build and health check depend on each of these.
4. **Call out host configuration changes.** A new required environment
   variable, a change to the startup safety checks
   (`Settings.refuse_unsafe_hosted_config` in `app/config.py`), or a change
   to what an existing variable must contain goes at the top of the pull
   request description as **"Render action needed: set X before
   merging"**. Otherwise the deploy that follows the merge refuses to
   start.
5. **Stay within 512 MB of memory.** Say so in the pull request when a new
   dependency or feature noticeably increases memory use or startup time.
6. **Remove temporary diagnostics completely.** When a diagnostic has served
   its purpose, delete its code, setting and tests. Don't leave it behind a
   flag. This matters most for anything that touches who the client is, TLS
   or authentication. A second implementation that only runs occasionally
   is likely to break without anyone noticing.

## Reviewing Dependabot pull requests

Dependabot (`.github/dependabot.yml`) opens pull requests every Monday. They
go through the same checks and the same approval as any other, and none
merges itself. Remember that merging one deploys, like any merge.

| Pull request | Check before approving | After merging |
|---|---|---|
| `actions` group | CI green | nothing more |
| compose's postgres digest | CI green; the new digest is Docker's (below) | nothing more |
| The Dockerfile's python digest | CI green; `docker-smoke` boots the new image; the new digest is Docker's (below) | `/health` and the pages on the live site |
| pip `runtime` group (`requirements.txt`, including pyyaml, which uvicorn[standard] installs anyway), or a runtime major version | CI green, including `pip-audit`; the changelogs, especially for a major; every page compared with `main`, since jinja2, starlette or fastapi can change what a page renders | `/health` and the pages on the live site |
| uvicorn, on its own | as for the `runtime` group, and its changelog for anything about proxy headers, `forwarded_allow_ips`, `ProxyHeadersMiddleware` or access logging, which `app/client_address.py` depends on | as for the `runtime` group, and the forged-header check ("Check it after a deploy", in [DEPLOYING.md's `FORWARDED_ALLOW_IPS` section](DEPLOYING.md#behind-render-and-cloudflare-forwarded_allow_ips)) |
| sqlalchemy, on its own | as for the `runtime` group, and its changelog for anything affecting the async engine, the queries or Alembic | `/health` and the pages on the live site |
| pip `dev-tools` group (`requirements-dev.txt`; none of it is in the image) | CI green. If a new ruff flags existing code, fix it in a pull request of its own first | nothing more |

**Is a base image's new digest Docker's?** Both base images come from this
repository's mirror on ghcr.io, which the "Mirror base images" workflow
fills from Docker's official images (`docs/ARCHITECTURE.md` §5.12). Before
approving a digest bump, ask Docker Hub for that digest in Docker's own
repository, `library/python` or `library/postgres`. It needs Docker with
Buildx, but not a running Docker engine:

```bash
d=sha256:<the new digest from the pull request>
docker buildx imagetools inspect "docker.io/library/python@$d" --format "{{.Manifest.Digest}}"
```

For compose's postgres, use `library/postgres`. If the image is Docker's,
it prints the same digest. If it isn't, the command fails with `not found`:
don't approve, and find out how the mirror came to hold it.

Pushing a commit to a Dependabot branch stops Dependabot from rebasing it,
so make fixes in a separate pull request and let Dependabot rebase its own.

## Secrets and personal data

- **Never commit secrets.** `DATABASE_URL` and any other credentials live
  only in the host's settings. `.env` is ignored by git; `.env.example`
  holds placeholders only.
- **Never put real personal data from live testing into the repository.**
  That covers real IP addresses, email addresses, tokens, request
  identifiers tied to a person, and anything else that identifies someone.
  It applies to code, tests, docs, commit messages, and pull request
  titles, descriptions and comments. Use the reserved documentation values
  instead:

  | Kind | Use |
  |---|---|
  | IPv4 addresses | `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24` (RFC 5737) |
  | IPv6 addresses | `2001:db8::/32` (RFC 3849) |
  | Hostnames and email addresses | `example.com`, `example.org`, `example.net` (RFC 2606) |
  | Tokens and passwords | an obvious placeholder, such as `<token>` |

  When you paste log output into a pull request or a test, replace the real
  values first. For a test, choose replacements that keep what it depends
  on. For example, an address standing in for a visitor must not be private
  or in a proxy's published ranges.
- **If something slips through,** replace it in the files with a follow-up
  pull request, and edit any pull request description that contains it.
  Rewriting `main`'s history is a separate decision, for a maintainer to
  make.
