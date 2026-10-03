# Contributing to Keel

`main` is production. Every commit merged into it deploys automatically to
the public demo on Render, with no manual step in between. These guidelines
keep that safe. The README covers how to run Keel and its tests, and
`docs/ARCHITECTURE.md` explains how it works.

## How a change lands

1. Work on a branch and open a pull request against `main`. Nobody pushes
   to `main` directly.
2. Before opening it, run `ruff check .` and the full test suite against a
   scratch Postgres database, with `TEST_DATABASE_URL` set as the README's
   "Running tests" section shows. Without that variable, about a third of
   the tests are skipped, so a green run means less than it looks.
3. A pull request is merged only when CI is green (both `lint-and-test` and
   `docker-smoke`) and a maintainer has approved it.
4. In the description, say what changed, why, and how you checked it. Put
   any host action needed (see "Deployment constraints", rule 4) at the very
   top. If there is none, say so.

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
