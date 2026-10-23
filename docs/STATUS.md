# Keel — Build Status & Handoff

**As of 2026-10-23.** A snapshot of what is actually built, what is
verified, and where the next piece of work starts. For the *why* behind
the design — the accounting concepts, the event-sourcing rationale, a
file-by-file walkthrough — read `ARCHITECTURE.md` first; this document
does not repeat it.

---

## 1. Verified state, right now

Forty-one PRs are merged into `main`, with regular merge commits, and
their branches deleted:

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
| [#12](https://github.com/Atreyeed02/Keel/pull/12) | `docs/status-scheduled-reset` | `2cfdc49` |
| [#13](https://github.com/Atreyeed02/Keel/pull/13) | `chore/pin-workflow-actions` | `deb637e` |
| [#14](https://github.com/Atreyeed02/Keel/pull/14) | `feat/ui-foundation` | `16006d3` |
| [#15](https://github.com/Atreyeed02/Keel/pull/15) | `feat/ui-learn` | `4484adc` |
| [#16](https://github.com/Atreyeed02/Keel/pull/16) | `feat/ui-posting-form` | `370e904` |
| [#17](https://github.com/Atreyeed02/Keel/pull/17) | `feat/ui-overview` | `d1effe4` |
| [#18](https://github.com/Atreyeed02/Keel/pull/18) | `feat/ui-transactions` | `fce1d7e` |
| [#19](https://github.com/Atreyeed02/Keel/pull/19) | `feat/ui-event-log` | `0faa9df` |
| [#20](https://github.com/Atreyeed02/Keel/pull/20) | `fix/reversal-example-class` | `731003f` |
| [#21](https://github.com/Atreyeed02/Keel/pull/21) | `chore/pin-base-images-ecr` | `0505b83` |
| [#22](https://github.com/Atreyeed02/Keel/pull/22) | `feat/ui-account-detail` | `82cdd62` |
| [#23](https://github.com/Atreyeed02/Keel/pull/23) | `chore/dependabot` | `ff84f20` |
| [#25](https://github.com/Atreyeed02/Keel/pull/25) | `dependabot/pip/dev-tools-…` (Dependabot: pytest, httpx, ruff) | `81b3ff8` |
| [#26](https://github.com/Atreyeed02/Keel/pull/26) | `chore/deps-prep` | `50ee2d9` |
| [#27](https://github.com/Atreyeed02/Keel/pull/27) | `dependabot/pip/runtime-…` (Dependabot: the `runtime` group, 7 updates) | `7a1b669` |
| [#29](https://github.com/Atreyeed02/Keel/pull/29) | `dependabot/pip/uvicorn-0.54.0` (Dependabot) | `3663e39` |
| [#30](https://github.com/Atreyeed02/Keel/pull/30) | `chore/mirror-base-images` | `3a8f967` |
| [#31](https://github.com/Atreyeed02/Keel/pull/31) | `chore/base-images-from-ghcr` | `5badfe2` |
| [#32](https://github.com/Atreyeed02/Keel/pull/32) | `dependabot/pip/runtime-…` (Dependabot: the `runtime` group, 3 updates) | `ea13de8` |
| [#33](https://github.com/Atreyeed02/Keel/pull/33) | `dependabot/pip/sqlalchemy-2.1.4` (Dependabot) | `82fba15` |
| [#35](https://github.com/Atreyeed02/Keel/pull/35) | `dependabot/pip/dev-tools-…` (Dependabot: ruff) | `7e00163` |
| [#36](https://github.com/Atreyeed02/Keel/pull/36) | `feat/transaction-missing-page` | `2c80961` |
| [#37](https://github.com/Atreyeed02/Keel/pull/37) | `dependabot/pip/runtime-…` (Dependabot: the `runtime` group, 4 updates) | `cb40f57` |
| [#38](https://github.com/Atreyeed02/Keel/pull/38) | `chore/mirror-on-sunday` | `4d6cfaf` |
| [#39](https://github.com/Atreyeed02/Keel/pull/39) | `docs/readme-showcase` | `06bad85` |
| [#40](https://github.com/Atreyeed02/Keel/pull/40) | `chore/split-requirements` | `a67cd55` |
| [#42](https://github.com/Atreyeed02/Keel/pull/42) | `dependabot/pip/dev-tools-…` (Dependabot: ruff) | `961a60a` |
| [#41](https://github.com/Atreyeed02/Keel/pull/41) | `dependabot/pip/runtime-…` (Dependabot: fastapi) | `14ce483` |
| [#43](https://github.com/Atreyeed02/Keel/pull/43) | `fix/head-413-ssl-warning` | `e06dc63` |
| [#44](https://github.com/Atreyeed02/Keel/pull/44) | `docs/changelog-v1` | `36381e8` |

Dependabot's first `runtime` pull request, #24, was closed unmerged: it
needed #26 first, and Dependabot replaced it with #27. #28, sqlalchemy
2.0.35 → 2.1.3, was closed by Dependabot, superseded by #33 (2.1.4). #34,
pydantic 2.14.0 → 2.14.1, was closed by Dependabot when asked to rebase
("pydantic is updatable in another way"), superseded by #37.

One commit reached `main` without a PR: `7bf196e`, "Update README.md"
(2026-10-07), an edit saved in GitHub's web editor that changed no file.
Its tree is identical to its parent's, `4484adc`.

**`main` is protected** by a repository ruleset, "Protect main", with no
bypass for anyone: every change arrives by pull request, CI's
`lint-and-test` and `docker-smoke` must both pass first, and the branch
cannot be force-pushed or deleted. The ruleset requires no approving
review; the owner approves each merge.

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

**`docs/readme-contributing-polish`** shortens the README and
CONTRIBUTING and moves their reference sections into `docs/`, verbatim.
The README keeps its top (the pitch, the live demo, the screenshots, what
makes Keel interesting, the tech stack, why it was built) and adds a short
quick start, a "How it's built" overview with links, and a table of the
documentation: 746 lines → 117. CONTRIBUTING keeps how a change lands and
the key rules on one screen, with links for the rest: 126 lines → 43. Four
new files take the rest:

| File | What moved there |
|---|---|
| `docs/HOW-IT-WORKS.md` | the guarantees and where each is enforced, why event sourcing, why double-entry, how idempotency works, how replay and rebuild work, the architecture, the database model, the limitations and future work |
| `docs/API.md` | the JSON API and the HTML routes |
| `docs/DEPLOYING.md` | deploying, the environment variables, the caps, `FORWARDED_ALLOW_IPS` and the forged-header check, scheduled and one-off jobs, the nightly demo reset, the pre-deploy checklist, and the logs (the README's "Observability") |
| `docs/DEVELOPMENT.md` | running locally and dev mode, running the tests and the per-file test table, the project layout, and CONTRIBUTING's deployment constraints, Dependabot review table with the digest check, and secrets and personal data, in full |

Pointers that named a README or CONTRIBUTING section now name the new file:
ARCHITECTURE §5.12, §5.13 and §7; this file's §2 and §4; and
comments in `.github/dependabot.yml`, the "Demo maintenance" workflow and
`docker-compose.yml`. ARCHITECTURE's stale "244 passed, 110 skipped" now
reads 322 and 161. Dated records above keep their wording, which was true
when written. No code or test changes; no migration, no host action.
Checked on that branch:

| Check | Result |
|---|---|
| Nothing lost: every non-blank line of the old README and CONTRIBUTING (734) looked up in the new files, link paths aside | 716 found verbatim. The other 18 were changed on purpose: two headings (the README's "Deploying" is now `DEPLOYING.md`'s title; "Observability" is now "Logs"), the project layout's `docs/` lines, which now list the new files, the Dependabot table's pointer to the forged-header check, now a link, and CONTRIBUTING's opening and steps 2–4 of "How a change lands", condensed (their details are in `DEVELOPMENT.md`'s "Running tests") |
| Every link resolves: every relative link and `#anchor` in every Markdown file, by GitHub's heading rules | `main`: 20 links, none broken; the branch: 47 links, none broken. A deliberately bad anchor and a missing file were both caught. The release notes' links point at files as of `v1.0.0`, which can't change |
| `ruff check .` and `pytest` | clean; 483 passed on Postgres 16 (a throwaway container, removed after); 322 passed, 161 skipped without a database. No test reads the README or CONTRIBUTING |
| Every page, `main` (`36381e8`) against the branch, same seeded data | all 15 HTML and JSON responses byte-identical once the posting form's key was masked; 48 screenshot pairs, one differing: the posting form's step 2, light, phone, by the native drop-down's text, the known rendering noise |

**#44, `docs/changelog-v1`**, merged 2026-10-23, prepares v1.0.0. It adds `CHANGELOG.md`, whose
1.0.0 entry (2026-10-23) sums up what Keel v1 includes for a reader new to
the project, by area: ledger and data integrity, event log, API and forms,
educational UI, security and hardening, deployment and operations, testing
and CI. The app reports version `1.0.0` in `/docs` and `/openapi.json`
(it said `0.1.0`), and a new test in `tests/test_deploy_config.py` fails
unless that matches the newest release in `CHANGELOG.md`. The README's
facts are brought up to date: the tests run without a database (321, now
322 with the new test; 161 skipped), "480+" tests, `test_account_detail.py`
in its test table, `db.ping_failed` and `db.ssl_mode_conflict` among the
logged events, and `HEAD` on every `GET` route. Its versions (Python 3.12,
SQLAlchemy 2.1, PostgreSQL 16) were already current. #43's entry below gets
its merge and live check. After merging and deploying, the merge commit is
tagged `v1.0.0`, with a GitHub release whose notes are the 1.0.0 entry.
No migration, no host action. Checked on that branch:

| Check | Result |
|---|---|
| `ruff check .` (0.17.1) | clean |
| `pytest` | 483 passed on Postgres 16 (a throwaway container, removed after); 322 passed, 161 skipped without a database |
| The version test with the app's version set to `0.9.9` | fails |
| Every page, `main` (`e06dc63`) against the branch, same seeded data | all 15 HTML and JSON responses byte-identical once the posting form's key was masked, except `/openapi.json`'s version (`0.1.0` → `1.0.0`). 48 screenshot pairs: 5 phone shots differed by anti-aliasing and the native drop-down's text; a control run of `main` against itself differed on 6 phone shots in the same way and by the same pixel counts, so it is rendering noise |

After merging, as `36381e8` at 11:01:03 UTC, and the release:

| Check | Result |
|---|---|
| Deploy | `/openapi.json` reported `1.0.0` from 11:02:01 UTC, a minute after the merge (keel.css `Last-Modified` 11:01:16 UTC); CI green on the merge commit |
| Live, GET and `HEAD` requests only, 11:02:34–11:02:44 UTC | `/health` ok, the five pages, a transaction's and an account's page, the HTML and JSON `404`s; the 12 static files byte-identical to the commit; `HEAD` on `/`, `/health` and `/api/accounts` a `200` with no body |
| Tag | `v1.0.0`, annotated, on the merge commit `36381e8` |
| Release | [Keel v1.0.0](https://github.com/Atreyeed02/Keel/releases/tag/v1.0.0), published 11:03:19 UTC, the latest release. Its notes are the CHANGELOG's 1.0.0 entry, with the links to the README and ARCHITECTURE pointing at those files as of the tag. As first published, the notes had `…` and `−` garbled in three lines (the entry was read through a Windows text pipe); they were replaced within minutes, and the stored notes now match the entry |

**#43, `fix/head-413-ssl-warning`**, merged 2026-10-23, makes three small fixes.

- **`HEAD` on every route that answers `GET`.** FastAPI's `@app.get` routes
  answered `HEAD` with `405` (Render's first request to a new instance was
  `HEAD /`). A new innermost middleware, `app/head.py`, runs a `HEAD` as
  its `GET` and sends that status and those headers, `Content-Length`
  included, with no body. The log still shows `HEAD`, the rate limit still
  counts it as a read, `/static/` keeps `StaticFiles`' own `HEAD`, and the
  OpenAPI schema gains no `head` operations (ARCHITECTURE §5.22).
- **A form sent without `Content-Length` over the body limit gets the
  `413` page**, as one with `Content-Length` does, not FastAPI's `400`
  JSON. FastAPI's form parsing caught the body limit's error and answered
  `400` itself; `BodySizeLimitMiddleware` now records that the limit was
  passed, drops whatever the app answers, and sends its `413` (§5.18). Both
  form routes, `/post-transaction` and `/accounts`, had the problem.
- **A startup warning when `DATABASE_SSL` and the URL's `sslmode`
  disagree.** The stricter one is still used; `app/main.py` now logs
  `db.ssl_mode_conflict` once at start with both modes and the one used,
  never the URL.

New tests: `tests/test_head.py`, and in `test_hardening.py`,
`test_deploy_config.py` (a fresh process importing the app, with and
without a conflict) and `test_rate_limit.py`. README and ARCHITECTURE
§5.1, §5.18, §5.22 and the test tables describe the changes. No migration,
no host action. Checked on that branch:

| Check | Result |
|---|---|
| `ruff check .` (0.17.1) | clean |
| `pytest` | 482 passed on Postgres 16 (a throwaway container, removed after); 321 passed, 161 skipped without a database |
| 8 deliberate breaks (the `HEAD` middleware not added, added outermost, or keeping the body; the app's own answer to an over-limit body not replaced, or the limit never recorded; no warning at start, a warning for agreeing modes, the URL in the warning) | each fails a test |
| Behaviour on uvicorn serving `app.main:served` as `app.serve` does, `main` (`14ce483`) against the branch, same seeded data: 72 cases (static files with ranges, conditionals and aborted downloads, forms and multipart, the body limit, `HEAD` on 17 paths, headers) | the only differences are the intended ones: 13 `HEAD` routes from `405` to their `GET`'s status and headers with an empty body (`404` for an unknown transaction, page or API), and the two chunked over-limit forms from `400` JSON to the `413` page. `HEAD` on `/accounts` (`405`, `Allow: POST`), `/openapi.json`, `/docs` and an unknown path, the declared-length `413`s, the chunked API `413` and the bad `Content-Length` `400` are unchanged. Static files differed only in `ETag` and `Last-Modified`, from the two checkouts' file times |
| Every page, `main` against the branch, same seeded data | 48 pairs (12 pages, light/dark × 1280/390 px), all identical; a second run differed only in the native drop-down's text on two phone shots (the new-account form, the posting form's step 2), the known rendering flicker; all 15 HTML and JSON responses byte-identical once the posting form's key was masked |

After merging, as `e06dc63` at 10:19:19 UTC: live a minute later (`HEAD /`
a `200` from 10:20:16 UTC; keel.css `Last-Modified` 10:19:26 UTC). Live
check:

| Check | Result |
|---|---|
| GET requests, 10:20:54–10:21:06 UTC | `/health` ok, the five pages, a transaction's and an account's page, the HTML and JSON `404`s; the 12 static files byte-identical to the commit |
| `HEAD` on `/`, `/health`, `/api/accounts`, a transaction from the API, an unknown one | `200`, `200`, `200`, `200`, `404`, each with no body. Through Cloudflare a `HEAD` response carries no `Content-Length` (the app sends one; the edge drops it), and `GET` responses arrive gzipped and chunked |
| One form `POST` to `/post-transaction`, sent at 10:21:24.007 UTC over HTTP/1.1 with `Transfer-Encoding: chunked`: 1,000,012 bytes, sent at 100 KB/s | `413`, the "Not saved" page (65,536-byte limit), with the security headers; nothing written. The response's first byte came at 9.96 s, once the whole body was sent. Render's log has its `request.completed` at 10:21:33 UTC, about 9.5 s after the send, taking 3.0 ms: a proxy in front of the app held the whole body and passed it on with a declared length, so on the live service such a request takes the declared-length path, not the chunked one #43 fixed. That fix still covers a chunked body that reaches the app, as it does with no proxy or a streaming one |

**#42 and #41**, Dependabot, merged 2026-10-23 in that order, each on
`a67cd55` with both checks green (461 passed, `pip-audit` clean, the image
holding all 13 runtime pins and none of the dev tools).

- **#42, ruff 0.17.0 → 0.17.1** (`dev-tools`, `requirements-dev.txt`),
  merged as `961a60a`. Keel selects E, F, I and UP with preview off; the
  108 enabled rules are the same in both versions, `ruff check` is clean
  on both, and `ruff format --diff` is byte-identical. The release's
  E271–E274 change is preview-only, and its other fixes are in rules Keel
  doesn't select. Not in the image.
- **#41, fastapi 0.143.1 → 0.143.2** (`runtime`, `requirements.txt`),
  merged as `14ce483`. The two wheels differ only in the version and in
  `_FrontendStaticFiles._fallback_file_exists`, which now checks the file
  in a thread; that class serves `app.frontend(...)`, which Keel doesn't
  use (it mounts Starlette's `StaticFiles`). Dependencies unchanged. Live
  seven seconds after the merge (keel.css `Last-Modified` 04:59:34 UTC,
  first served at 05:00:26). Live check, GET requests only, at 05:01 UTC:
  `/health` ok, the five pages, a transaction's and an account's page,
  the HTML and JSON `404`s, and the 12 static files byte-identical to the
  commit.

**#40, `chore/split-requirements`**, merged 2026-10-23, splits the requirements in two.
`requirements.txt` is what the image runs: the twelve runtime packages and
pyyaml, which uvicorn[standard] installs anyway and which is pinned there so
the image gets the version CI tests and audits. `requirements-dev.txt` adds
pytest, pytest-asyncio, httpx and ruff through `-r requirements.txt`. The
Dockerfile already installed only `requirements.txt`, so it gains only a
comment. CI's `lint-and-test` installs `requirements-dev.txt` and audits
both files with `pip-audit --strict -r requirements.txt -r
requirements-dev.txt`, the same packages as before. The "Demo maintenance"
and "Cloudflare ranges" workflows keep installing `requirements.txt`, which
no longer carries the test tools; the reset job holds the production secret.
`docker-smoke` gains a step that runs `pip list --format=freeze` in the app
container and passes it to `.github/scripts/check_image_packages.py`, which
fails unless every runtime pin is installed at its version and nothing from
`requirements-dev.txt` is. Dependabot's `runtime` group gains pyyaml and
`dev-tools` loses it, so each group matches one file. Three new tests in
`tests/test_deploy_config.py` pin the files, the groups, every install
command and the image check; the group-coverage test reads both files.
README, CONTRIBUTING and ARCHITECTURE §5.12–5.13 describe the split; the
README's test counts are brought up to date (461; 160 skipped without a
database). No migration, no host action, nothing under `app/` changes.
Checked on that branch:

| Check | Result |
|---|---|
| `ruff check .` (0.17.0) | clean |
| `pytest`, from a fresh virtualenv of `requirements-dev.txt` | 461 passed on local Postgres 16; 301 passed, 160 skipped without a database |
| 13 deliberate breaks (ruff or pyyaml in the wrong file, no `-r`, the Dockerfile, CI, the audit or either scheduled workflow installing the wrong file, the image step removed, pyyaml in no group or two, the check ignoring versions or dev tools) | each fails a test |
| A fresh virtualenv of `requirements.txt` alone | none of the four dev tools installed; `app.main`, `app.serve` and every script import; `alembic upgrade head`, `alembic check` and the demo seed run on a scratch database (dropped after) |
| Image built from `main` (`06bad85`) against the branch's, `pip list` | 10 packages gone, nothing added, every other version the same: pytest, pytest-asyncio, httpx, ruff and what only they need (certifi, httpcore, iniconfig, packaging, pluggy, Pygments). 387 MB → 330 MB on disk. `pip check` clean |
| docker-smoke's new step, run through `docker compose` on a project of its own | the branch's image passes ("all 13 runtime pins installed, none of the 4 dev tools"); the same image with `requirements-dev.txt` installed on top fails on all four tools; `main`'s image fails the same way |
| Every page, `main` (`06bad85`) against the branch, both served from the runtime-only virtualenv, same seeded data | 48 pairs (12 pages, light/dark × 1280/390 px), all identical; all 33 HTML and JSON responses byte-identical once each render's own key was masked |

After merging, as `a67cd55` at 04:51:12 UTC: live about a minute later
(keel.css `Last-Modified` 04:51:24 UTC). Live check, GET requests only:
`/health` ok, the five pages, a transaction's and an account's page, the
HTML and JSON `404`s, and the 12 static files byte-identical to the commit.
"Cloudflare ranges", run by hand on `main`, passed with 0 annotations,
installing `requirements.txt` only ("matches Cloudflare's 22 published
ranges"). The merge changed `dependabot.yml`, so Dependabot ran at once:
all four jobs passed, and the pip job read both files, checking pytest,
pytest-asyncio, httpx and ruff from `requirements-dev.txt` as well as the
runtime pins, sqlalchemy and uvicorn each on their own. It opened #41 and
#42 (above). The first scheduled "Demo maintenance" run since the split is
due the night of 2026-10-23 (21:43 UTC; GitHub has started these runs 1.5 to
5 hours late), and is not checked yet. All 19 scheduled runs before it, for
2026-10-04 to 2026-10-22, passed.

**#39, `docs/readme-showcase`**, merged 2026-10-22, gives the README a new
top: a one-line pitch, the live demo's link (with the free tier's wake-up
delay and the nightly reset), four light screenshots in a 2 × 2 grid (the
overview's accounting equation, the posting form filled in by "Try it" step
1, the Cash account's T-account, the event log down to its newest event), a
"What makes this interesting" list, the tech stack, and a short "Why I built
this". The old opening paragraph and tech line are replaced; everything from
"The guarantees" down is unchanged, except the test count, 447 → 458 in two
places, and `docs/screenshots/` in the project layout. The screenshots are
`docs/screenshots/*.png`, 1280 px wide, 949 to 1,076 px tall, about 100 to
150 KB each, taken from a local server on the seeded demo data, so they hold
no visitor's data. The Docker image copies only `app/`, `alembic/` and
`scripts/`, so they don't reach it. No migration, no host action, nothing
under `app/` changes. Checked on that branch, then on GitHub after merging:

| Check | Result |
|---|---|
| Every page, `main` (`4d6cfaf`) against the branch, same seeded data | 48 pairs (12 pages, light/dark × 1280/390 px). Identical apart from the native drop-down's text on two phone shots (the posting form, the new-account form); all 33 HTML and JSON responses byte-identical once each render's own key was masked |
| The four screenshots | no console error, failed request or blocked style; GET requests only |
| CI on the PR | both checks passed |
| The repository's page on GitHub, after merging | in headless Chrome, GET requests only: the README's headings in order, the live link present, all four images loaded at full size (1280 px wide, shown at about 400) |

**#38, `chore/mirror-on-sunday`**, merged 2026-10-22, moves the "Mirror base
images" workflow from Mondays to Sundays, still at 02:30 UTC, so it runs a
day before Dependabot's Monday run instead of an hour before. GitHub starts
a scheduled run late when it is busy: on 2026-10-19 the Monday 02:30 UTC run
started at 07:58 UTC, after Dependabot's run at 03:36 UTC. Nothing new was
copied that day, but a new official digest would have reached Dependabot a
week late. Dependabot keeps its Monday schedule. The workflow test checks
the triggers, not the time, and is unchanged. No migration, no host action,
nothing under `app/` changes. Checked on that branch:

| Check | Result |
|---|---|
| `ruff check .` (0.17.0) | clean |
| `pytest` (no database available) | 298 passed, 160 skipped |
| `pytest` (local Postgres 16) | 458 passed |
| CI on the PR | both checks passed |
| Every page, `main` (`7e00163`) against the branch, same seeded data | 48 pairs (12 pages, light/dark × 1280/390 px). Identical apart from the native drop-down's text on two phone shots (the posting form, the new-account form); all 33 HTML and JSON responses byte-identical once each render's own key was masked |

**#35, ruff 0.16.10 → 0.17.0** (Dependabot), merged 2026-10-21. Keel's
config names its target (`py312`) and its rule sets (`E`, `F`, `I`, `UP`),
so 0.17's new defaults (Python 3.11, a smaller default rule set) don't
apply: the enabled rules were the same 108 under both versions, and
`ruff check .` was clean, also with 0.17's changed and newly stable rules
(F406, DTZ, ICN001, TID254, TID255, RUF071) turned on. Its first CI run
predated #36, so Dependabot rebased it on request onto #37's merge, and CI
passed again on the new head. The app never imports ruff, but the image
installs all of `requirements.txt`, so the merge redeployed. Render's
Auto-Deploy started about 13 minutes after the merge (16:21 UTC) and
`7e00163` was live at 16:35 UTC, confirmed in Render's events and by
`keel.css`'s `Last-Modified` (16:34:41 GMT). Auto-Deploy can take 10 to 15
minutes to start. A GET-only check at 16:32 UTC, just before that deploy,
passed: `/health` ok, every page as expected, the 12 static files
identical to the commit (unchanged from `cb40f57`).

**#37, the `runtime` group** (Dependabot: fastapi 0.143.0 → 0.143.1,
starlette 1.7.0 → 1.8.0, pydantic 2.14.0 → 2.14.1, pydantic-settings
2.15.0 → 2.16.0), merged 2026-10-21. It replaced #34 (above). The review
read both versions' source for starlette and pydantic-settings, then
compared behaviour on both:

| Check | Result |
|---|---|
| CI on the PR | both checks passed, on `main` as it was after #36 |
| `pytest` (local Postgres 16) | 458 passed |
| Starlette, on uvicorn serving `app.main:served` as `app.serve` does | 59 requests: static files (GET, HEAD, ranges, `If-Range`, `If-None-Match`, traversal, five aborted downloads then 30 prompt GETs, keep-alive), urlencoded and multipart forms with several charsets, the body limit (`413`, `400`), `HEAD` on pages (`405`, as before), the API's `422`, the security headers on every response. Identical but one: a static file's `405` now carries `Allow: GET, HEAD` |
| pydantic-settings, 37 configurations, each started in a fresh process | identical on both versions, error text included: every startup refusal (`DATABASE_URL` unset or the local default in any spelling, no TLS or `prefer`/`allow`/`disable`, `FORWARDED_ALLOW_IPS` of `*`, a list with `*`, empty or `" , "`, `channel_binding=require`, unknown URL parameters, out-of-range numbers), `.env` and environment precedence, lowercase names |
| `hide_input_in_errors` | the startup traceback still shows no input, so `DATABASE_URL` stays out of the logs; with the setting off, the input appears (the check's control) |
| `1E-999999999` and 21 other amounts | the same results and error types on pydantic 2.14.0 and 2.14.1 |
| Every page | HTML and JSON identical, 33 responses; screenshots differed only by the native drop-down's text |

The review also found an older problem, the same on starlette 1.7 and 1.8:
a form sent without `Content-Length` that goes over the body limit gets
FastAPI's `400` ("There was an error parsing the body") instead of the
`413` page. It is still refused unread, and the API answers `413`. It is
fixed by #43 (above). `cb40f57` was first served by a manual deploy at 16:13 UTC,
then by Render's Auto-Deploy at 16:18 UTC. Live check: `/health` ok before
and after; the overview, `/transactions`, the event log, `/learn`, the
posting form, a transaction's page and an account's page; an unknown
transaction's 404 page and the API's JSON `404`; the 12 static files
identical to the commit; and an API post with the amount `1E-999999999`,
refused with a `422` before any database access, so nothing was written.

**#36, `feat/transaction-missing-page`**, merged 2026-10-19, gives a
transaction's page the account page's 404 page. An id that is no
transaction's, or not an id at all, now gets `transaction_missing.html` with
a `404` ("No such transaction", the reset sentence on the demo only, a link
to all transactions) where it got FastAPI's JSON: a `404` for an unknown id,
a `422` for a malformed one. `?already=` is unchanged: only the posting
redirect writes it, as `1`, and anything else is still FastAPI's `422`. `GET
/api/transactions/{id}` keeps its own error shape. The PR also pins the
`1E-999999999` amount, which gave a 500 before #32 (the post rolled back;
nothing was written) and has been refused since pydantic 2.14 with a `422`:
the API test runs with no database, so the refusal comes before any
connection, and the form's test checks that nothing is written. The form
used to call that amount "too large", because pydantic reports a vanishingly
small amount and a huge one as the same error (too many digits in total). It
now says the amount "has more than two decimal places" whenever that is the
part to fix, that is with at most 16 digits before the point. No migration,
no host action. Checked on that branch:

| Check | Result |
|---|---|
| `ruff check .` (0.16.10) | clean |
| `pytest` collection | **458 tests** |
| `pytest` (no database available) | 298 passed, 160 skipped |
| `pytest` (local Postgres 16) | 458 passed |
| New tests fail without their change | 12 deliberate breaks, one at a time, each failed a test: the account's template, status 200, the id parsed by FastAPI again, the reset sentence always or never shown, another link, heading, tab title or no eyebrow; the form's wording fix removed, `normalize()` (which rounds `1E-999999999` to 0), the 16-digit limit dropped |
| Every page, `main` (`82fba15`) against the branch, same seeded data | 44 pairs, two passes. Identical apart from the native drop-down's text on three phone shots (the new-account form, the posting form and its second "Try it" step), whose HTML was byte-identical on both servers once each render's own UUIDs were masked |
| The new page | light/dark × 1280/390 px, unknown and malformed ids: `404`, no sideways scroll, no console error or blocked style, GET requests only. On `main` the same URLs are FastAPI's JSON (`404`; `422` `uuid_parsing`) |

Render marked the first deploy of `2c80961` "Failed" at about 15:14 UTC,
yet its log showed no error: the build, the migrations and startup
completed and the health checks answered 200. The live site served the new
page from 15:15:30 UTC while the dashboard still showed `82fba15` as live.
The owner redeployed the same commit by hand, and Render showed it live at
17:52 UTC. The dashboard and the served version can disagree, so live
checks report what the site serves, by something the PR changes. Live
check, GET requests only, from 15:15:30 UTC: an unknown or malformed id,
and `?already=1`, gave the new 404 page; `?already=maybe` FastAPI's `422`;
`/api/transactions/{id}` its own JSON `404`; a real transaction's page
200; `/health` ok; the security headers on the 404 page.

**#33, sqlalchemy[asyncio] 2.0.35 → 2.1.4** (Dependabot), merged
2026-10-17. Keel uses only SQLAlchemy Core. The review read 2.1's
changelog and the 2.0.x fixes released with it. Migrations ran on fresh
databases with both versions: up, down to base and up again, `alembic
check` clean, the catalogs identical, and the offline SQL identical. 447
tests passed, also with deprecation warnings as errors. Every page, 44
pairs, was identical but for the native drop-down's text. CI on the PR:
both checks passed. Read-only live check after merging: `/health`
`{"status":"ok","db":"up"}`, the overview with its 8 accounts, and
`/transactions` with its 10. The deploy of `82fba15` was confirmed in
Render's events.

**#32, the `runtime` group** (Dependabot: fastapi 0.142.2 → 0.143.0,
asyncpg 0.31.0 → 0.32.0, pydantic 2.13.5 → 2.14.0), merged 2026-10-17.
CI on the PR: both checks passed, `pip-audit` clean. 447 tests passed
locally. Every page, 44 pairs, was identical but for the native
drop-down's text. A probe of the app's responses found two changes. The
wording of the malformed-UUID `422` changed. And `1E-999999999` as an
amount, which pydantic 2.13 accepted and Postgres then could not store (a
500, rolled back), is now refused with a `422` (pinned by
`feat/transaction-missing-page`, above). Read-only live check after
merging: the new UUID wording showed the deploy was live; `/health`
`{"status":"ok","db":"up"}`, so asyncpg 0.32 reaches Neon over TLS; the
overview and `/transactions` as expected.

**#31, `chore/base-images-from-ghcr`**, moves both base images to this
repository's mirror on ghcr.io, which #30's workflow fills (below), with the
same digests: the Dockerfile's
`ghcr.io/atreyeed02/keel-base/python:3.12-slim@sha256:a6e34c59…` and
compose's `ghcr.io/atreyeed02/keel-base/postgres:16-alpine@sha256:721873c3…`.
After ECR Public's anonymous limits failed Dependabot's docker runs and one
of `main`'s CI runs, nothing Keel builds or tests pulls from a shared
anonymous allowance any more. The base-image test now requires the mirror,
and a new one requires the mirror workflow to copy exactly the pinned tags,
to the mirror, from `main` only, with no other trigger or permission.
CONTRIBUTING has the command a reviewer runs to confirm a digest bump is
Docker's own image. It changes no app code and no migration, and needs no
host action: both packages are public. Checked on that branch, then on the
live site after merging:

| Check | Result |
|---|---|
| `ruff check .` (0.16.10) | clean |
| `pytest` collection | **447 tests** |
| `pytest` (no database available) | 292 passed, 155 skipped |
| `pytest` (local Postgres 16) | 447 passed |
| New tests fail without their change | 9 deliberate breaks, one at a time, each failed a test: the Dockerfile or compose back on ECR Public, the python pin a bare tag, the workflow no longer copying postgres, compose on a tag the workflow doesn't copy, a `push` trigger, `contents: write`, the job not gated to `main`, another mirror |
| CONTRIBUTING's digest check, on today's pins | `docker buildx imagetools inspect` printed the python pin for `docker.io/library/python@` that digest, and the postgres pin for `library/postgres`, as did the mirror for both; postgres's digest under `library/python`, and a digest of zeros: `not found`, exit 1. No Docker engine was running: it only asks the registry |
| Anonymous pulls from the mirror | a token with no login, then each pinned digest's manifest: 200 for both |
| Every page, `main` (`3a8f967`) against the branch, same seeded data | 11 pages × light/dark × 1280/390 px, 44 pairs, compared pixel by pixel twice, with every animation finished. 41 identical in both passes. A transaction's page (phone, light) in the first pass and an account's page (phone, dark) in the second differed by 96 and 323 scattered pixels, each in one pass only. The new-account form (phone, dark) differed in both, only in the native drop-down's "Asset", while the page and every file it loads were byte-identical on both servers: Chrome drawing its own control, not a change |
| CI on the PR | both checks passed; `lint-and-test` pulled the python image from `ghcr.io/atreyeed02/keel-base` |
| The deploy | `5badfe2` live in Render's events on 2026-10-16 at 18:04 UTC, about a minute after it started; the Dockerfile names only the mirror, so that build pulled from it |
| Read-only, GET requests only | 14 URLs' status and security headers as expected, the overview's 5 static files byte-identical to the commit, `/health` ok, 32 renders clean, injected style and script refused |

**#30, `chore/mirror-base-images`**, adds the "Mirror base images" workflow
(`ARCHITECTURE.md` §5.12). Every Monday at 02:30 UTC (Sundays since #38,
above), an hour before Dependabot, and by hand, it copies `python:3.12-slim`
and `postgres:16-alpine` from ECR Public to `ghcr.io/atreyeed02/keel-base`
with `crane` (pinned by version and checksum), by digest, every platform
included, and fails unless the copy has the official digest. Only `main`'s
copy runs; its token can read the repository and write packages. It changed
nothing that was built. Checked on that branch, then on `main` after
merging:

| Check | Result |
|---|---|
| CI on the PR | both checks passed |
| Every page, `main` (`3663e39`) against the branch | 44 pairs, two passes: identical apart from the demo dot and the native drop-down's text |
| First run, by hand on `main` (`3a8f967`), 2026-10-16 | passed. `python:3.12-slim`: none → `sha256:a6e34c59…`; `postgres:16-alpine`: none → `sha256:721873c3…`, the digests the Dockerfile and compose pin; the workflow's own check (the mirror's digest equals the official one) passed for both |
| The packages | public: pulled with no login, each pinned digest's manifest 200 |

**#29, uvicorn 0.31.0 → 0.54.0** (Dependabot, on its own, as CONTRIBUTING
asks). `app/client_address.py` runs uvicorn's `ProxyHeadersMiddleware`
unchanged, so the review read what changed there: several
`X-Forwarded-For` headers are now joined into one list, where 0.31 kept
only the last; `host:port` entries are split, the port going to the
client's second field, which Keel never reads; trust checks are memoised.
On Render's chain, a single header with no ports, nothing changes, which a
local probe of both versions showed. Before merging: 446 tests passed on
both versions, also with warnings as errors; every page, 44 pairs,
identical; 22 URLs identical but for each render's own submission key and
the static files' dates. After merging, `main`'s first CI run failed
pulling postgres from ECR Public (`toomanyrequests: Data limit exceeded`),
and its rerun passed. Checked on the live site:

| Check | Result |
|---|---|
| The deploy | `3663e39` live: `keel.css` served with `Last-Modified` 8 s after the merge, Render's checkout time |
| Read-only, GET requests only | 14 URLs and their headers as expected, the static files byte-identical to the commit, 32 renders clean, injected style and script refused |
| The README's forged-header check (2026-10-14) | a write with forged `X-Forwarded-For`, `True-Client-IP` and `X-Real-IP`: 400, logged with the sender's own public address as `client` and `scheme` `https` |
| Two separate `X-Forwarded-For` headers (`192.0.2.5`, then `192.0.2.6`), the case 0.54 handles differently | 400, logged the same way: the sender's own address, not either forged one |

**#27, #26 and #25**, the first Dependabot updates. #26 prepared for them:
`sqlalchemy[asyncio]`, because SQLAlchemy 2.1 no longer installs greenlet,
which broke #24's CI and container; `alembic` sorted as third-party, which
newer ruff wanted; and sqlalchemy and uvicorn moved out of the `runtime`
group into pull requests of their own. Then #25 (pytest 9.1.1, httpx
0.28.1, ruff 0.16.10) and #27 (the `runtime` group, 7 updates, fastapi
0.142.2 among them) merged with CI green. #27 was checked on the live site
after its deploy: 12 URLs and their headers, 9 static files byte-identical,
32 renders clean, injected style and script refused, GET requests only.

**#23, `chore/dependabot`**, adds `.github/dependabot.yml`: pip, the
Dockerfile's python, compose's postgres and the actions, every Monday at
09:00 India time, in groups, with a 7-day cooldown for packages and
actions; Python stays on 3.12 and Postgres on 16 (`ARCHITECTURE.md`
§5.13). CI's tests now run on compose's `db`, under `COMPOSE_FILE` at job
level, so the postgres digest is written once. Before merging: 3 deliberate
breaks, each failed a test; every page, 44 pairs against `82cdd62`,
identical; CI ran 446 tests, none skipped, on compose's database. After
merging, Dependabot alerts and security updates were turned on (they had
been off); there were no open alerts. Each ecosystem's first run: pip opened
#24 and #25, both with CI red (above); the actions were up to date; the
postgres digest was current; the python check failed on ECR Public's
anonymous limit (`429`), which is what led to #30 and the mirror.

**#22, `feat/ui-account-detail`**, the redesign's seventh and last phase, adds
an account's page, `/account-detail/{id}` (§2, "HTTP"): the account as a
T-account, debits on the left and credits on the right, oldest first, 25
entries a page; each entry's transaction as its one link, with the accounts
on its other side; why the account's normal side is the one it is; and the
sum that gives its balance, saying what a balance below zero means. The
chart of accounts, each entry on a transaction's page and each opened
account in the event log link to it. An id that isn't an account's gets a
page saying so, with a 404; on the demo it adds that the nightly reset
changes every id. It adds the "T-account" term, no migration and no
dependency; the existing URLs and the JSON API are unchanged. Checked on
that branch, then on the live site after merging:

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `pytest` collection | **444 tests** |
| `pytest` (no database available) | 289 passed, 155 skipped |
| `pytest` (local Postgres 16) | 444 passed |
| Unchanged | every existing URL and its status, the JSON API and its tests; a transaction's page keeps its JSON 404 for an unknown id |
| New tests fail without their change | 16 deliberate breaks, one at a time, each failed a test: entries newest first, a column's count from the page rather than the account, the other side naming this side's accounts, the sum ignoring the normal side, a balance below zero unexplained, a hyphen for the minus sign, an unknown id as JSON, a malformed id as a 422, no note that the totals cover every page, the chart of accounts, the event log or a transaction's page not linking, the event log's page off by one, the reset sentence outside the demo, the term without its card's anchor, "a" and "an" ignoring the type |
| Every page, `main` (`731003f`) against the branch, same seeded data | 9 pages × light/dark × 1280/390 px, 36 pairs, compared pixel by pixel with every animation finished: 24 pairs identical. The overview differs only in the chart of accounts (the names are links now, at the links' weight, so its columns shift a few pixels), a transaction's page only in its entries' account names, and the event log only by each opened account's "Open account" link (8 × 44 px taller), with a few anti-aliased pixels on the timeline's markers above it |
| Headless Chrome, account pages | Cash, Equipment loan payable, Cash below zero and a new account with no entries (both posted to a local database only), and an unknown id, in light and dark at 1280 and 390 px (20 renders): no CSP violation, console error, failed request (but the unknown id's own 404), cut-off figure, or page wider than the screen |
| Motion | Stitch's T-account rows: an entry's background fades in under the pointer and its transaction's name changes colour, 150 ms; 0 s under reduced motion. Stitch's normal-balance tooltip is the term cards. Its inflow/outflow ratio bar, an invented "retained liquidity" figure whose transition never ran, is not shipped |
| Keyboard and terms | an entry's link takes the focus ring and its colour; the page defines its account's type, normal side, T-account, debit, credit and event once each; the T-account card links to `/learn#normal-side` |
| Live, on `82cdd62`, read-only (GET requests only; the browser failed any other request before sending it, and none was attempted) | live about 50 s after the merge; headers on 13 URLs exact; the 12 static files byte-identical to the commit; `/health` ok; the posting form's checks and the 422 hold; 28 renders clean, injected style and script refused. All 8 account pages: 200, exact headers, nothing inline, the chart's name and balance, "why debit/credit" right for the type, each column's count and total its entries', the sum the right way round, oldest first, each entry's one link a transaction listing the account on that side, every "other side" that transaction's opposite accounts, the event link on the log's page holding its "Open account", each term once, no repeated id. The 21 account links on the 10 transaction pages and the 8 "Open account" links in the event log each land on the right account. An unknown and a malformed id: the HTML 404 with the reset sentence and its link. 12 renders of account pages clean; the hover fade, the focus ring and the T-account card work, and nothing moves under reduced motion |

**#21, `chore/pin-base-images-ecr`**: Docker Hub's anonymous pull limit
(`429 Too Many Requests`) stopped both CI jobs on #20 before a step ran. The
Dockerfile's `python:3.12-slim` and the `postgres:16-alpine` in
`docker-compose.yml` and CI now come from `public.ecr.aws/docker/library`,
Docker's own copy of its official images, pinned by digest (the same
digests as Docker Hub's); a test keeps the three pinned and CI's Postgres
the same as compose's. Render builds the Dockerfile, so production pulls
from there too. Its CI and #20's (after `gh pr update-branch`) pulled from
ECR Public; `/health` was up after the merge, and #20's deploy, built from
the same Dockerfile, went live. The base images now change only through a
pull request that bumps the digests.

**#20, `fix/reversal-example-class`**, fixes a regression #19 put on the live
site. #19 named the event log's reversal example `.example`, a class
`/learn` already used for its five worked examples. Its rule came later in
`keel.css`, so it won: on the live site `/learn`'s boxes got a dashed
border, less padding, smaller corners and 16 px more space above, and the
event log's box took `/learn`'s fill. #19's checks looked for errors and
overflow, not for pages that changed without meaning to, so none caught it;
the live check after merging did. The event log's classes are now
`.reversal-example` and its parts, Phase 5's second `.pager` rule is folded
into the first, and a stylesheet test fails when a selector is styled in
two top-level rules unless it's on one of two lists with its reason: the
motion section's own repeats, and the shared-then-own pairs (`h1`, `h2`,
`.t-side li`, `.t-total`). Checked on that branch, then on the live site
after merging:

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `pytest` collection | **420 tests** |
| `pytest` (no database available) | 277 passed, 143 skipped |
| `pytest` (local Postgres 16) | 420 passed |
| The new stylesheet test | fails on `0faa9df`'s stylesheet, naming `.example` and `.pager`; passes on the branch |
| Every page, `main` (`0faa9df`) against the branch, same seeded data | 9 pages (the overview, the list and a filtered list, a transaction, the posting form and a `?try=` step, the event log, a new account, `/learn`) × light/dark × 1280/390 px, 36 pairs, compared pixel by pixel with every animation finished: only `/learn` (from its first worked example down, 60 px taller) and the event log's example box differ, as intended; the other 28 pairs are identical |
| `/learn` against `fce1d7e`, before the regression | identical in all four views |
| The pager | the same computed style on `main` and the branch (it doesn't show on the seeded data, which fits on one page) |
| Live, on `731003f`, read-only (GET requests only; the browser failed any other request before sending it, and none was attempted) | live a minute after the merge; headers on 13 URLs exact; the 12 static files byte-identical to the commit; the posting form's checks and the malformed date's 422 hold; 28 renders clean, injected style and script refused. `/learn`'s five worked examples against `fce1d7e`, before the regression: the same computed style, and pixel-identical in light and dark at 1280 and 390 px. The event log's example: a 1 px dashed border, no fill, not `.example`, no number, time or marker |

**#19, `feat/ui-event-log`**, the redesign's sixth phase, turns the event log
into a timeline (§2, "HTTP"): each event in words from its own payload, the
raw event folded away under it, and a card on how the log works, the
nightly reset included. Checked on that branch, then on the live site after merging; it adds no migration and no
dependency:

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `pytest` collection | **419 tests** |
| `pytest` (no database available) | 276 passed, 143 skipped |
| `pytest` (local Postgres 16) | 419 passed |
| Unchanged | `/event-log` and `?page=`, 25 a page, the transaction page's link to the event log's page holding its event, the JSON API and its tests |
| New tests fail without their change | 13 deliberate breaks, one at a time, each failed a test: entries out of their recorded order, damaged data shown as balanced, the normal side ignoring the type, an old payload's version unexplained, account names or transaction numbers not looked up, the example or the reset sentence outside the demo, the example not undoing the demo's own rent, the raw event open by default, the pager saying "transactions", "1 events", the raw payload dropped |
| Headless Chrome, seeded demo data | the event log, with and without raw events open, in light and dark, at 1280 px and 390 px (8 renders): no CSP violation, console error, failed request, or page wider than the screen; on a phone, with every raw event open, nothing overflows |
| The timeline | 18 events, newest first. Event No. 13: "Transaction posted: January operating costs", its three entries by name, "Balanced Debits = credits USD 2,718.50", "Open transaction No. 5". Event No. 1: "Account opened: Cash", "Asset · USD · normal side Debit". The raw event opens with Enter, and with JavaScript off |
| The example | "For example, undoing February's rent (No. 6) would be:" in a dashed box with no number, time or marker; only on the demo, and it is the demo's own No. 6 with its sides swapped |
| Contrast | the new text at least 5.25:1 in both themes; the markers' outlines at least 3.53:1 |
| Motion | none added: Stitch's timeline has none |
| Live, on `0faa9df`, read-only (GET requests only; the browser failed any other request before sending it, and none was attempted) | headers on all seven pages, `/health`, the steps' forms, a filtered list and a search by description alone exact, no nonce, nothing inline; the 12 static files byte-identical to the commit; 28 renders clean, injected style and script refused. The event log: 18 events, No. 18 down to No. 1 with no gaps, every raw event folded, no "Doesn't balance". All 8 opened accounts match their payloads. All 10 "Open transaction" links land on the right transaction (its title, number, event id, entries in order and event number), and each transaction page's link to the event log points to the page holding its event; January's round trip lands on it. The example on the live demo, labelled "For example, undoing February's rent (No. 6) would be:", is the live No. 6 with its sides swapped, with no number, time or marker; the reset sentence is there. Its terms once each; page 9's message; Enter and JavaScript off open a raw event; nothing overflows on a phone. **It also found a regression:** the event log's `.example` restyled `/learn`'s worked examples, fixed by `fix/reversal-example-class` (above) |

**#18, `feat/ui-transactions`**, the redesign's fifth phase, rebuilds the
transaction list and a transaction's page (§2, "HTTP"), and fixes two
things on the way. The list's "Volume" added every entry, so each amount
counted twice and currencies were added together; it is now "Amount", the
debit total in each currency, on the overview's recent table too. And a
search by description alone, which the browser sends with both dates empty
(`date_from=&date_to=`), was answered with a 422 instead of the list, on the
live site too. Checked on that branch, then on the live site after
merging; it adds no migration and no dependency:

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `pytest` collection | **397 tests** |
| `pytest` (no database available) | 264 passed, 133 skipped |
| `pytest` (local Postgres 16) | 397 passed |
| Unchanged | the URLs and their query parameters, the pagination tests, the JSON API and its tests |
| New tests fail without their change | 13 deliberate breaks, one at a time, each failed a test: both sides added, currencies added together, an account named twice, a chip dropping every filter, a page past the end not noticed, what an entry does ignoring the normal side, the uneven-sides note on every transaction, the event log's page off by one, the overview keeping "Volume", no two-currency note, no chips, a second detail link on a card (which also fails the existing pagination tests), empty dates refused |
| Headless Chrome, seeded demo data and one local two-currency transaction | the list, a filtered list, three transactions' pages and the overview, in light and dark, at 1280 px and 390 px (24 renders): no CSP violation, console error, failed request, or page wider than the screen |
| The list | January's costs: "Debits = credits USD 2,718.50", where "Volume" said 5,437.00; two currencies: "EUR 50.00 · USD 54.00". A click anywhere on a card opens it, and the focus ring goes round the card. Filtered by "invoice" from 2026-01-01: two chips, each a link without its filter |
| JavaScript off | a search by description from the form: "1 transaction matching", one chip; its link goes back to all 10 |
| A transaction's page | No. 5: "Expense: a debit increases it." twice, "Asset: a credit decreases it.", totals under a double rule, "USD 2,718.50 − 2,718.50 = 0.00", and the note on uneven sides. Two currencies: each side's totals per currency, a line per currency, the two-currency note. The event link lands on the event log's page holding the event |
| Contrast | the new pairs at least 5.02:1 in both themes |
| Motion | the transaction cards and the filter chips fade in a background under the pointer in 150 ms; under reduced motion, 0 s |
| Live, on `fce1d7e`, read-only (GET requests only; the browser failed any other request before sending it, and none was attempted) | all seven pages, `/health`, the three steps' forms, a filtered list and a search by description alone send exactly `script-src 'self'`, with no nonce, and `style-src 'self'`, `nosniff`, `DENY` and `same-origin`; nothing inline. The 12 static files are byte-identical to the commit. 7 pages in light and dark at 1280 px and 390 px (28 renders): no CSP violation, console error or failed request, no page wider than the screen; an injected style attribute, `<style>` and inline script were refused. **The 422 is gone:** `/transactions?q=rent&date_from=&date_to=`, the URL the form sends, is 200 with "1 transaction matching" and one chip, also when submitted from the form in the browser; before the merge it was 422 on the live site. A malformed date is still 422. All 10 cards: each one's amount, badge, accounts by side and title match its own page (January's costs "Debits = credits USD 2,718.50"); the overview's recent table says "Amount", has no "Volume", and its 10 amounts match the cards. The chips each link without their filter; page 9 says there's no such page and links to page 1. January's page: what each entry does, the totals, `USD 2,718.50 − 2,718.50 = 0.00`, the note linking to `/learn#example-january`, its five terms once each, and its event linked to the event log's page showing it. A click on a card's corner opens it; the focus ring goes round the card; 150 ms fades, 0 s under reduced motion; nothing cut off on a phone |

**#17, `feat/ui-overview`**, the redesign's fourth phase, opens the overview with
the accounting equation in each currency, and on the demo adds three "try
it" steps, linked from the demo notice, that open the posting form filled
in (§2, "HTTP"). Checked on that branch, then on the live site after
merging; it adds no migration and no dependency:

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `pytest` collection | **370 tests** |
| `pytest` (no database available) | 252 passed, 118 skipped |
| `pytest` (local Postgres 16) | 370 passed |
| New tests fail without their change | 9 deliberate breaks, one at a time, each failed a test: the steps or the notes shown outside the demo (on the overview, the form's GET, its refusal), the hidden `try` field dropped, the notice's link dropped, currencies in reverse order, a currency without entries shown, the newest account of a name used, `holds` always true |
| Headless Chrome, seeded demo data | all seven pages, in light and dark, at 1280 px and 390 px: no CSP violation, console error, failed request, or page wider than the screen. An injected style attribute, `<style>` and inline script were refused |
| The equation | EUR, then USD: `1,800.00 = 0.00 + 0.00 + (1,800.00 − 0.00)` and `43,643.50 = 10,500.00 + 25,000.00 + (14,450.00 − 6,306.50)`, both "Holds", the same sums `/learn` writes out. Four cards to a row on a desktop; stacked on a phone, the operators between them |
| The steps | each opens the form filled in, with its note and the panel as expected: 1, "Balanced ✓"; 2, "Out of balance by 2.00 USD.", Post `aria-disabled`; 3, "Out of balance in 2 currencies." with the no-conversion sentence. The notice's link lands on the steps. Without JavaScript, "Add line" keeps the note. Posting step 1 lowers Assets and Revenue − Expenses by 12.00, and both currencies still hold |
| Terms | the demo's overview defines every glossary term once, the five types in the formula; outside the demo, all but "event" |
| Contrast | the new pairs at least 4.97:1 in both themes |
| Motion | the equation cards fade in a background under the pointer in 150 ms; under reduced motion, 0 s |
| Live, on `d1effe4`, read-only (GET requests only; the browser failed any other request before sending it, and none was attempted) | all seven pages, `/health` and the three steps' forms send exactly `script-src 'self'`, with no nonce, and `style-src 'self'`, `nosniff`, `DENY` and `same-origin`; no page has an inline script, `<style>` or style attribute. The 12 static files are byte-identical to the commit, and the panel's sentences match `PANEL_TEXT`. 7 pages in light and dark at 1280 px and 390 px (28 renders): no CSP violation, console error or failed request, fonts and theme applied, no page wider than the screen; an injected style attribute, `<style>` and inline script were refused. The equation: EUR, then USD, both "Holds", every card, caption and sum line matching the chart of accounts on the same page; on a phone, in both themes, no figure cut off. The steps follow it, their "Why" links land on sections of `/learn`, and the notice's link, on every page, lands on them. Each step's form opens with its note and the panel as on the branch; pressing Post on steps 2 and 3 stays on the page, sends nothing and moves the focus to the banner. Step 1 was not posted. Without JavaScript, "Add line" keeps the note; `?try=` other than 1, 2 or 3 shows none. A 150 ms fade, 0 s under reduced motion |

**#16, `feat/ui-posting-form`**, the redesign's third phase, rebuilds the posting
form: every refusal explains the rule broken and how to fix it, linked to
`/learn`; a live balance panel per currency; add and remove without
JavaScript; the script moved to a file and the CSP nonce removed; uvicorn's
access log off so query strings are never logged (§2, "HTTP"). Checked on
that branch, then on the live site after merging; it adds no migration and no
dependency:

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `pytest` collection | **354 tests** |
| `pytest` (no database available) | 244 passed, 110 skipped |
| `pytest` (local Postgres 16) | 354 passed |
| Headless Chrome, seeded demo data | all seven pages, in light and dark, at 1280 px and 390 px: no CSP violation, console error, failed request, or page wider than the screen. An injected style attribute, `<style>` and inline script were refused |
| The panel, from computed styles and the accessibility tree | 650.00 against 500.00: "Out of balance by 150.00 USD.", Post `aria-disabled` but focusable, described by the reason; pressing it or Enter in a field posts nothing and moves the focus to the banner, which is announced. 0.10 + 0.20 against 0.30: balanced. An EUR account fills in EUR; two currencies off in opposite directions get the no-conversion sentence. Line hints for a wrong currency, a negative amount and a third decimal place. Add and remove in the page, renumbered. Balanced, Enter posts and lands on the transaction |
| Resubmissions | the same values again land on the transaction with "Already posted."; changed values come back as the form, linking to what was posted, with a new key and what was typed |
| JavaScript off | add and remove redraw the form with what was typed; a rejected post shows the summary and the panel |
| Refusals before the app | the write limit gives a form a page with the wait in seconds and `Retry-After`; the API's 429 body is unchanged |
| Motion | the banner fades between its states in 150 ms; under reduced motion, 0 s |
| Live, on `370e904`, read-only (GET requests only; the browser failed any other request before sending it, and none was attempted) | all seven pages and `/health` send exactly `script-src 'self'`, with no nonce, and `style-src 'self'`, `nosniff`, `DENY` and `same-origin`; no page has an inline script, `<style>` or style attribute. The 12 static files are byte-identical to the commit, and the panel's sentences match `PANEL_TEXT`. 7 pages in light and dark at 1280 px and 390 px (28 renders): no CSP violation, console error or failed request, fonts and theme applied, no page wider than the screen; an injected style attribute, `<style>` and inline script were refused. The panel behaves as on the branch: out of balance, Post `aria-disabled`, focusable and described by the reason; pressing it or Enter while out of balance sends nothing, moves the focus to the banner and announces it; 0.10 + 0.20 against 0.30 balances; the EUR fill, the no-conversion sentence, the line hints, add and remove, with and without JavaScript; a 150 ms fade, 0 s under reduced motion. Not checked live, because each needs a POST: the server's refusals (the form's problem summary, 409, 429, 413) |

**#15, `feat/ui-learn`**, the redesign's second phase, adds `/learn` (how
double-entry works, with worked examples from the demo's own data) and
defines terms where the pages use them: debit, credit, normal side,
balanced, trial balance, the five account types and event (§2, "HTTP").
Checked on that branch, then on the live site after merging; it adds no
migration and no dependency:

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `pytest` collection | **327 tests** |
| `pytest` (no database available) | 224 passed, 103 skipped |
| `pytest` (local Postgres 16) | 327 passed |
| Headless Chrome, seeded demo data | all seven pages, in light and dark, at 1280 px and 390 px: no CSP violation, console error, failed request or horizontal page scroll |
| A term's card, from the accessibility tree and computed styles | the term is a button named by its word and described by its definition, `expanded` false, then true when open. Desktop: a click opens the card 8 px under its term; Esc closes it and focus returns to the term; hovering opens it after 300 ms (not at 150 ms), it stays while the pointer is over the card and closes after leaving; one opened by a click stays when the pointer leaves, and a click elsewhere closes it. Keyboard: Enter opens, Tab reaches "More about …", Esc closes and returns focus. Phone: a tap opens a full-width sheet along the bottom; Close closes it. JavaScript off: a click still opens it, hover does not. Reduced motion: no fade |
| Learn | the quiz's answers open without JavaScript; a `/learn#…` link marks where it lands |
| Live, on `4484adc`, read-only (GET requests; the browser checks submitted nothing) | all seven pages, `/learn` and a transaction's detail included, send the CSP; static files are byte-identical to the commit. 7 pages in light and dark at 1280 px and 390 px (28 renders): no CSP violation, console error or failed request, fonts and theme applied. The term cards behave as on the branch, and each page has exactly its planned terms, every reference and Learn link resolving |

**#14, `feat/ui-foundation`**, the first phase of the UI redesign, replaces the
Tailwind CDN with Keel's own stylesheet (light and dark themes,
self-hosted fonts and icons) and tightens the page CSP to name no other
origin and allow no inline styles (§2, "HTTP"). Checked on that branch, then
on the live site after merging; it adds no migration and no dependency:

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `pytest` collection | **319 tests** |
| `pytest` (no database available) | 217 passed, 102 skipped |
| `pytest` (local Postgres 16) | 319 passed |
| Headless Chrome over the DevTools protocol, seeded demo data | all six pages, in light and dark, at 1280 px and 390 px: no CSP violation, console error, failed request or horizontal page scroll, and both fonts loaded. Typing an amount into the posting form updated its live total. Injected into a page, a style attribute, a `<style>` element and an un-nonced script were all refused |
| Keyboard | the first Tab shows the skip link; focus rings visible on links, buttons and fields in both themes |
| Contrast | every text/background pair in both themes at least 4.5:1, control borders and the focus ring at least 3:1 |
| Motion, read from computed styles | normal: 150 ms fades on links, buttons, fields and table rows; the posting button lifts 2 px on hover; the status dot pulses 3 times; the busy spinner turns 12 times, then the button resets on back navigation; the amount field takes its line's colour on focus in both themes. `prefers-reduced-motion: reduce`: every duration 0 s, no pulse, no lift, no spinner |
| JavaScript off | every page renders, and the posting form still posts and redirects to the new transaction |
| Live, on `16006d3`, read-only (GET requests; the browser checks submitted nothing) | every page sends the new CSP with a nonce per request, `style-src 'self'`, no CDN and no `'unsafe-inline'`, with `nosniff`, `DENY` and `same-origin`; static files are served with their types, the fonts byte for byte as committed. Six pages in light and dark at 1280 px and 390 px (24 renders): all 200, no CSP violation, console error or failed request, both fonts loaded, the theme and font applied, the demo notice present, no horizontal scroll. An injected style attribute, `<style>` and un-nonced script were refused; the posting form's live total updated |

**#13, `chore/pin-workflow-actions`**, pins every action by commit
(checkout v7.0.1, setup-python v7.0.0) and runs every job on
`ubuntu-24.04` (§2, "CI"). Checked on that branch and on `main`:

| Check | Result |
|---|---|
| CI on the PR | passed on `ubuntu-24.04`, 312 tests, 0 annotations on both jobs: the Node 20 warning and the Ubuntu 26 notice are gone |
| "Cloudflare ranges" run #1, dispatched on the branch (`dc210ec`) | passed, 0 annotations. It was the workflow's first-ever run: its Monday 03:17 UTC slot on 2026-10-05 had not fired by 03:54 UTC |
| Run #2, dispatched on `main` (`deb637e`) after the merge | passed in 22 s, 0 annotations, matching Cloudflare's 22 published ranges |

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

The 118 skips are not failures. Every database-backed test skips itself
unless `TEST_DATABASE_URL` is set, so **a green local run of 252 tests
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

### HTTP — eight server-rendered pages

All in `app/main.py`, Jinja2 on a shared `base.html`:
overview with the accounting equation per currency, per-account balances
and normal-side signs, the event log as a paginated timeline, filterable + paginated transaction list, posting form,
transaction detail with debit/credit columns, account detail as a
T-account, account creation, and
`/learn`, which explains double-entry with the demo's own data and needs
no database. Plus
`/health`, which reports `degraded` rather than raising. The
`/transactions` date filters are UTC days, matching the UTC timestamps
the pages show, whatever time zone the database session uses.

The pages are styled by one hand-written stylesheet,
`app/static/css/keel.css`, with light and dark themes that follow the
visitor's system setting, and self-hosted fonts and icons. Nothing loads
from another origin, so the page CSP is `style-src 'self'` with no CDN
(`ARCHITECTURE.md` §5.8, §5.18). Debits and credits are never shown by
colour alone. The Stitch designs' animations are kept, in CSS, and all
stop for visitors who ask their system for reduced motion. Key terms
are defined where they appear: a dotted-underlined term opens a short
card that links to its section of `/learn` (`app/glossary.py`). The posting
form shows a live balance per currency, and when it refuses a transaction it
says what went wrong, why the rule exists and how to fix it, by line
(`app/posting_messages.py`); without JavaScript, "Add line" and "Remove"
redraw the form. Every script is a file under `/static/js`, so the CSP is
`script-src 'self'` with no nonce, and no log records a query string. The
overview opens with the accounting equation in each currency
(`app/overview.py`); on the demo, three "try it" steps, linked from the
demo notice, open the posting form filled in, with a note for each. The
transaction list shows each transaction as a card with its accounts by side
and its amount, the debit total in each currency, and a transaction's page
says what each entry does to its account and shows the balance in each
currency (`app/transactions_view.py`). The event log is a timeline of each
event in words from its own payload, with the raw event folded away under
it (`app/event_log.py`). An account's page shows it as a T-account, with
its entries oldest first, why its normal side is the one it is, and the sum
that gives its balance, below zero included (`app/account_view.py`). This
was a redesign done one PR per phase; §6 has the phases.

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

In `requirements.txt`, what the image runs: FastAPI 0.143.1 on Starlette
1.8.0 (pinned directly), uvicorn 0.54.0, SQLAlchemy 2.1.4, asyncpg 0.32.0,
pydantic 2.14.1, pydantic-settings 2.16.0, python-dotenv 1.2.4, jinja2
3.1.6, and pyyaml 6.0.3, which uvicorn[standard] brings in. In
`requirements-dev.txt`, on top of those: pytest 9.1 with pytest-asyncio 1.4,
httpx 0.28 and ruff 0.17. Moving off Starlette 0.38.6 closed all 7 of its
advisories and needed no application change. Starlette 1.x also imports
`python_multipart` directly, so form parsing no longer goes through the
deprecated `import multipart` shim.

Dependabot (`.github/dependabot.yml`) proposes updates every Monday for pip,
the Dockerfile's python, compose's postgres (which CI's tests run on, so the
digest is written once) and the actions' commit pins. The two images' new
digests come from the mirror, which copies Docker's official images a day
earlier. Updates are grouped, Python stays on 3.12 and Postgres on 16, and
nothing merges without review (ARCHITECTURE §5.13, DEVELOPMENT.md).

### CI — two jobs

`lint-and-test` (ruff, `alembic check` on a freshly migrated database, then
pytest against compose's Postgres) and `docker-smoke`, which is the more
interesting one: it builds the image, waits for `/health`, asserts the exact
healthy body `{"status":"ok","db":"up"}` (a bare 200 check would go green on
a stack whose migrations failed), confirms `alembic_version` was actually
stamped, then drives a real account-creation → posting → overview flow
through the container. It pins `COMPOSE_FILE` so it cannot accidentally pick
up the dev bind-mount and test host code instead of the image. The dev bind
mount itself sets `create_host_path: false`, so a checkout without `./app`
refuses to start instead of mounting an empty directory over the image.
Since #5, `lint-and-test` also runs `pip-audit --strict`, and `docker-smoke`
fails if the container runs as root; since `chore/split-requirements`, also
if the image is missing a runtime pin or contains a dev tool. A third,
scheduled workflow, "Cloudflare ranges", compares `app/client_address.py`'s
copy of Cloudflare's address ranges with the published lists every Monday. A
fourth, "Demo maintenance", resets the public demo every night (below), and
a fifth, "Mirror base images", copies the base images to ghcr.io every
Sunday (#30; Mondays until #38). Since #13, the workflow files pin their
actions by commit and run every job on `ubuntu-24.04`. The images both jobs
pull, Python for the app and Postgres, are pinned by digest and come from
the repository's mirror on ghcr.io: Docker Hub's anonymous pull limit
stopped them first, and #21 moved them to ECR Public; then ECR Public's
stopped Dependabot and a run of `main`'s CI, and #31 moved them to the
mirror. Both jobs are required checks: the "Protect main" ruleset (§1)
merges nothing into `main` until they pass.

### Deploy readiness — merged (#5), live on Render

What a public demo on a container host needs, with `docs/DEPLOYING.md`
as the operator's guide:

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
  including a CSP that allows scripts and styles only from Keel's own
  files: no page has an inline script, so there is no nonce.
- **Public writes, bounded.** Every write, form or API, counts against a
  per-client allowance, 30 a minute by default. Past it the answer is a
  `429` with an exact `Retry-After`. Reads are not limited. The counts are
  in memory, which is a second single-instance assumption. Hard caps of
  200 accounts and 2000 transactions (`409 ledger_full`) keep a free 0.5 GB
  database from filling however many clients write. The defaults were sized
  from measured bytes per write.
- **The demo itself.** With `ENVIRONMENT=demo`, every page says it is a
  public demo whose data resets nightly, and
  `python -m scripts.reset_demo_data --yes` restores the demo data. It
  refuses in any other environment and counts only without `--yes`. It is
  the one sanctioned exception to the append-only log, done in a single
  transaction that re-enables the trigger. The "Demo maintenance" workflow
  runs it daily at 21:43 UTC on GitHub Actions, with `DATABASE_URL` from
  the `production-demo` environment, after
  `python -m scripts.check_database_host` confirms the host is the live
  endpoint. Key pruning isn't scheduled on the demo: the reset empties the
  keys. DEPLOYING.md's "Scheduled maintenance" section has the setup.

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

- keep the repository active, or GitHub pauses the nightly reset and the
  base-image mirror after 60 days without activity (DEPLOYING.md,
  "Scheduled maintenance"). The reset itself
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
event-log endpoint yet (`ARCHITECTURE.md` §8 item 7).

**Done since the previous version of this list:** deploy readiness
(host-style configuration, a migrate-then-serve start command, a non-root
image, proxy headers, a body limit and security headers, the write rate
limit, caps on accounts and transactions, the demo notice and the reset
script), all on `chore/deploy-readiness`; and, merged, stable entry order
(each entry's submission position, stored and replayed), payload schema
versions, `alembic check` in CI with the duplicate idempotency-key
constraint removed, the JSON API, the `account.created` backfill, the
account-currency rule in the database, idempotency key retention, UTC date
filtering, the trigram search index, the FastAPI/Starlette upgrade, the test
fixtures' guard against migrated databases, a transaction's 404 page, like
an account's (#36), the README showcase (#39), the split of the runtime
and test requirements (#40), and `HEAD` on every route that answers `GET`,
the `413` for an over-limit form sent without `Content-Length`, and the
warning when `DATABASE_SSL` and the URL's `sslmode` disagree
(#43), the v1.0.0 release (#44), and a shorter README and CONTRIBUTING,
their reference sections moved into `docs/` (`docs/readme-contributing-polish`).

**Small fix, not started:** an accurate `Allow` header on a `405`.
Starlette takes it from the first route that matches the path, so a
`DELETE /api/accounts` gets `Allow: POST`, though `GET /api/accounts`
exists too.

**Dependency pinning, not started:** a lock or constraints file. Only the
packages named in `requirements.txt` are pinned; their own dependencies
(uvloop, httptools, watchfiles, anyio and the rest) are resolved at build
time, so two builds of the same commit can install different versions, and
`pip-audit` checks whatever resolved that day. A constraints file generated
from the image, or a lock file, would pin all of them, with Dependabot
keeping it current.

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
you are running 292 of 447 tests:

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

The design reference is kept outside the repository: the Stitch project
"Keel Educational Accounting Ledger", whose design system ("Academic
Ledger") and screens for the overview, transactions, posting form,
account detail, event log, a Learn page and the logo set the look.
`keel.css` re-states its palette, type and spacing as tokens; none of its
generated markup, Tailwind classes, Google Fonts or remote images is used.
An earlier generated kit ("Audit Ledger Protocol") is also kept outside
the repository and is superseded.

**The mockups are a visual reference, not a specification.** They show
things Keel does not have and will not claim: hashes and hash chains,
actors with email addresses, account codes, sub-accounts, exports, and an
unbalanced transaction in the event log, which Keel's invariant makes
impossible. Every figure on a page comes from the database.

The redesign ships one PR per phase:

1. **Foundation** (#14, merged): the stylesheet, light and dark
   tokens, fonts, icons, logo, header, motion, and all six pages
   restyled.
2. **Learn** (#15, merged): `/learn`, and inline definitions of
   debit, credit, normal side, balanced, trial balance, the account types
   and event, each linking to its section.
3. **Posting form** (#16, merged): a live balance panel per
   currency, "out of balance by X" until it balances, refusals that
   explain the rule broken, add and remove without JavaScript, the script
   moved to a file and the CSP nonce removed.
4. **Overview** (#17, merged): the accounting equation per
   currency, and a guided "try it" path from the demo notice.
5. **Transactions list and detail** (#18, merged): cards with
   each transaction's accounts by side and its amount per currency, the
   filters in force as chips, and a transaction's page saying what each
   entry does, with the balance per currency.
6. **Event log** as a timeline (#19, merged): each event in words
   from its own payload, the raw event folded away, and how append-only
   events and rebuilds work.
7. **Account detail** (#22, merged): each account as a
   T-account, its entries oldest first, why its normal side, and its
   balance as a sum, below zero included.

Each phase brings the Stitch animations of the elements it builds (card
hovers, the balance banner's change of state), under the same rules: CSS first, nothing endless, all
of it off under reduced motion, and every page working without JavaScript.
