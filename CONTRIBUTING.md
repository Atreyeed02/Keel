# Contributing to Keel

`main` is production: every commit merged into it deploys automatically to
the public demo on Render, with no manual step in between. These rules keep
that safe. [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) has each of them in
full, with the tests, the project layout and how to review Dependabot's
pull requests.

## How a change lands

1. Work on a branch and open a pull request against `main`. Nobody pushes
   to `main` directly.
2. Install `requirements-dev.txt`, then run `ruff check .` and the full test
   suite with `TEST_DATABASE_URL` pointing at a scratch Postgres
   ([Running tests](docs/DEVELOPMENT.md#running-tests)). Without it, about a
   third of the tests are skipped.
3. A pull request is merged only when CI is green (`lint-and-test` and
   `docker-smoke`) and a maintainer has approved it.
4. In the description, say what changed, why, and how you checked it, with
   any host action (rule 4 below) at the very top, or say there is none.

## Key rules

1. **Migrations work with the previous version of the code**, which is
   still serving while they run. Rename or drop in two pull requests.
2. **One instance only.** Startup migrations and the in-memory rate limit
   assume it.
3. **Keep the startup contract:** `python -m app.serve`, `$PORT` on
   `0.0.0.0`, `GET /health`, the `Dockerfile` at the root, the unprivileged
   `keel` user.
4. **Call out host configuration changes** at the top of the description:
   **"Render action needed: set X before merging"**.
5. **Stay within 512 MB of memory.**
6. **Remove temporary diagnostics completely**, never behind a flag.
7. **Dependabot pull requests** get the same checks and approval; never
   push to their branches.
   ([What to check for each](docs/DEVELOPMENT.md#reviewing-dependabot-pull-requests))
8. **No secrets and no real personal data** in code, tests, docs, commits or
   pull requests. Use the reserved documentation values (`192.0.2.0/24`,
   `2001:db8::/32`, `example.com`).
   ([In full](docs/DEVELOPMENT.md#secrets-and-personal-data))

Rules 1–6 in full: [Deployment constraints](docs/DEVELOPMENT.md#deployment-constraints).
