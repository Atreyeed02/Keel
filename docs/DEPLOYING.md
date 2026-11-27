# Deploying Keel

Keel is one Docker image plus a Postgres database, so it runs on any host
that runs an image and provides Postgres: Render, Railway, Fly.io and the
like. Nothing here is specific to one of them.

**Build** from the `Dockerfile`. The compose files are for running locally
and are not part of a deployment. The base image is pulled from
`ghcr.io/atreyeed02/keel-base`, this repository's public copy of Docker's
official `python:3.12-slim`, pinned by digest, so a host needs no registry
login (`docs/ARCHITECTURE.md` §5.12).

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

## Environment variables

| Variable | Required | What it does |
|---|---|---|
| `DATABASE_URL` | **yes** | The Postgres URL. `postgres://`, `postgresql://` and `postgresql+asyncpg://` all work, with or without `?sslmode=...`. The only query parameters accepted are `sslmode` and `channel_binding`, and `channel_binding` only as `prefer` or `disable`. The app's driver, asyncpg, can't do channel binding, so `channel_binding=require`, which Neon puts in the URLs it gives you, stops startup: remove it or change it to `prefer`. Any other parameter (`application_name`, `connect_timeout`, `options`, ...) also stops startup, because asyncpg would fail on every connection. |
| `ENVIRONMENT` | **yes**: `production`, or `demo` for the public demo | Either refuses to start if `DATABASE_URL` is unset or is the local `ledger:ledger@db` default, if the database connection is not encrypted (`DATABASE_SSL`, below), or if `FORWARDED_ALLOW_IPS` is `*` or empty. `demo` also shows a notice on every page saying this is a public demo that resets nightly, linking to the overview's "try it" steps, and is the only environment `scripts.reset_demo_data` will run in. |
| `PORT` | set by most hosts | Where the server listens. Default 8000. |
| `FORWARDED_ALLOW_IPS` | **yes** behind a proxy | Proxies whose `X-Forwarded-For` / `-Proto` are believed, as addresses and networks. It decides who the client is: in the logs, and for the write rate limit. On Render: `127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16` (below). Default `127.0.0.1`. `production` and `demo` refuse to start on `*`, alone or in a list, or on an empty value. |
| `WRITE_RATE_LIMIT`, `WRITE_RATE_WINDOW_SECONDS` | no | Writes (any method but `GET`, `HEAD`, `OPTIONS`) one client may make in any window, forms and API alike; past that, `429` with `Retry-After`. Reads are not limited. Default 30 per 60 seconds; `WRITE_RATE_LIMIT=0` turns it off. |
| `DATABASE_SSL` | **yes** in `production` and `demo`, unless the URL has `sslmode` | `disable`, `allow`, `prefer`, `require`, `verify-ca` or `verify-full`. If the URL has an `sslmode` too, the stricter of the two is used, so neither can weaken the other, and when they differ the app logs a `db.ssl_mode_conflict` warning at start naming both. Unset: whatever the URL says, else the driver default. `production` and `demo` refuse to start unless the result is `require`, `verify-ca` or `verify-full`: with no mode, or `disable`, `allow` or `prefer`, the connection can be plaintext. |
| `MAX_ACCOUNTS`, `MAX_TRANSACTIONS` | no | The most accounts and transactions the ledger will hold. A write that would add one more is a `409` (`ledger_full`), form or API; replays are still answered. Default 200 and 2000, sized for a 0.5 GB database (below). `0` means no cap, which a real ledger wants. |
| `MAX_REQUEST_BODY_BYTES` | no | Largest request body accepted; larger is a 413. Default 65536. |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | no | Connections per instance, default 5 + 10. Keep the total under your database plan's connection limit. |
| `LOG_LEVEL` | no | Level of the JSON log lines on stdout. Default `INFO`. |

## Why 200 accounts and 2000 transactions

The caps are there so a public demo cannot fill a free 0.5 GB database,
however many clients write to it. Measured on Postgres 16, including indexes,
the event row and the idempotency key: an account takes about 1.1 KB, a
two-entry transaction about 1.7 KB, and the largest transaction the 64 KiB
body limit admits (470 entries, a 512-character description) about 91 KB.
So even if every one of the 2000 were that large, the ledger would stay
under 200 MB. Concurrent writes can overshoot a cap by the few that were
already in flight when it was reached. Raising `MAX_REQUEST_BODY_BYTES`
raises the worst case with it.

## Behind Render and Cloudflare: `FORWARDED_ALLOW_IPS`

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

## Scheduled and one-off jobs

Run these with the same image and environment as the app, from the host's
cron or scheduled-job feature, or its one-off shell:

| Command | When |
|---|---|
| `python -m scripts.prune_idempotency_keys --yes` | **daily**, on a ledger that isn't reset. Deletes idempotency keys older than 30 days; without it the table grows forever. Not scheduled on the public demo, whose daily reset empties the table (below). |
| `python -m scripts.seed_demo_data` | once, on an empty database, if you want the demo data. It refuses to touch a ledger that already has accounts. |
| `python -m scripts.reset_demo_data --yes` | **public demo only**, daily, scheduled by the "Demo maintenance" workflow (below). Deletes everything visitors wrote and restores the demo data, in one transaction. Refuses unless `ENVIRONMENT=demo`, and only counts without `--yes`. |
| `python -m scripts.rebuild_read_model --yes` | only to repair the read model from the event log. Safe while serving; postings wait for it. |

## Scheduled maintenance: the daily demo reset

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
workflows after 60 days without repository activity, this one,
"Cloudflare ranges" and "Mirror base images" alike. A disabled workflow
doesn't run, so it has no failure to email: no email doesn't mean the reset
ran. Push a commit at
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
- Links to old transactions get a page saying so (`404`). Resubmitting an
  old form can't post twice, because the accounts it names are gone.
- Rarely, a page load and the reset deadlock, and Postgres aborts one of
  them: the visitor gets one error page, or the reset rolls back and the run
  fails (above).
- The write rate limit's counts are in the app's memory, and aren't reset.

**Running it by hand.** **Actions → Demo maintenance → Run workflow**, with
branch `main`, or `gh workflow run demo-maintenance.yml --ref main` and then
`gh run watch`. It resets the live demo, exactly as the nightly run does.

## Azure Container Apps

A second copy of Keel runs on Azure Container Apps, ready to take over from
Render. Until that cutover, **Render stays the live site**. Both run from
`main`, against the same Neon database. The "Deploy to Azure" workflow
(`.github/workflows/deploy-azure.yml`) deploys the Azure copy;
`tests/test_deploy_azure.py` fails CI if its triggers, permissions,
credentials, step order or health check change.

### What's there

Set up by hand, once:

| Resource | Settings |
|---|---|
| Resource group `rg-keel` | Central India (`centralindia`). The subscription's policy allows only a few regions, and Southeast Asia isn't one of them. |
| Environment `cae-keel` | Consumption only (`--environment-mode ConsumptionOnly`), logs destination `none`. Not an Express environment: Express environments can't run Jobs. |
| Container App `keel` | External ingress: HTTPS, with HTTP redirected. Target port 8000, Keel's default `PORT`. 0.25 CPU, 0.5 GiB. Single revision mode. Min 0 replicas, so the first request after it has been idle starts one, as on Render's free tier. Max 1 replica: Keel must run as a single instance (above). Command `python -m app.serve --no-migrate`. |
| Job `keel-migrate` | Manual trigger, replica timeout 600 s, no retries, the same image and resources. Command `python -m alembic upgrade head`. |
| On both | Secret `database-url`, and env vars `ENVIRONMENT=demo`, `DATABASE_SSL=require` and `DATABASE_URL=secretref:database-url`. `FORWARDED_ALLOW_IPS` isn't set yet (open items, below). |
| Entra app `keel-github-deploy` | No secret or certificate, only one federated credential, for the subject `repo:Atreyeed02/Keel:environment:production-azure`. Contributor on `rg-keel` and nowhere else. |
| GitHub environment `production-azure` | Deployment branches: `main` only. No required reviewers and no wait timer. Secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID`: identifiers, not passwords, kept as secrets so they stay out of the logs. |
| Package `ghcr.io/atreyeed02/keel` | Public, like `keel-base`, so the app and the job pull with no registry login. |

**The database URL** is the same one Render uses: Neon's direct endpoint,
with `sslmode=require` and without `channel_binding=require`. It is stored
only as the Container Apps secret `database-url`; it never passes through
GitHub. When the database password changes, update it on Render, in the
`production-demo` environment and on both of these, from a shell where
`DATABASE_URL` holds the new URL:

```bash
az containerapp secret set -n keel -g rg-keel --secrets "database-url=$DATABASE_URL"
az containerapp job secret set -n keel-migrate -g rg-keel --secrets "database-url=$DATABASE_URL"
az containerapp revision restart -n keel -g rg-keel \
  --revision "$(az containerapp show -n keel -g rg-keel --query properties.latestRevisionName -o tsv)"
```

The app reads the secret when it starts, hence the restart. The job reads it
at every run.

**A new required environment variable**, or a change to what one must hold,
goes on the Container App and the Job as well as Render, before the merge
that needs it.

**The package's visibility.** GitHub makes a new package private, and only
its settings page can change that: no API can. So the workflow's first run
pushes the image, finds that it can't pull it without a login, and stops
before anything in Azure changes. Then, once:

1. On GitHub, open the profile's **Packages** tab, then `keel`, then
   **Package settings**.
2. Under **Danger Zone**, **Change visibility** to **Public**.
3. Re-run the failed job: open the run, then **Re-run jobs → Re-run failed
   jobs**.

Every later push keeps it public.

### The deploy workflow

It runs on every push to `main` that changes what the image is built from:
`app/`, `alembic/`, `alembic.ini`, `scripts/`, `requirements.txt`, the
`Dockerfile` or `.dockerignore`. A Dependabot bump of the base image or of a
runtime package deploys too. A change to docs, tests or other workflows
deploys nothing, and neither does a change to the workflow itself: run it by
hand for that (below). One deploy runs at a time, and none is cancelled
part-way.

1. **Build and push.** Builds the `Dockerfile` and pushes it to
   `ghcr.io/atreyeed02/keel`, tagged with the commit's SHA and `main`,
   logging in with the run's `GITHUB_TOKEN`. What it deploys is the digest
   the registry reports for the SHA tag, so a moved tag can't change what
   runs.
2. **Pull check.** Fails unless the image can be pulled with no login (the
   package's visibility, above).
3. **Azure login.** `azure/login` with OIDC: GitHub vouches for the job's
   environment, and Entra trusts that one subject. Nothing that can be
   leaked is stored.
4. **Migrate.** Points `keel-migrate` at the image with the command
   `python -m alembic upgrade head`, starts it, and waits for that run to
   succeed. Any other outcome, or more than 10 minutes, fails the workflow
   and leaves the app as it was. Alembic runs the whole upgrade in one
   transaction, so a failed run changes nothing.
5. **Update the app.** Points `keel` at the same image with
   `python -m app.serve --no-migrate` and target port 8000, then waits for a
   revision running that image. In single revision mode the previous
   revision keeps serving until the new one is ready.
6. **Health.** Polls `https://<keel's address>/health` for up to 3 minutes
   until the body is exactly `{"status":"ok","db":"up"}` and the new revision
   is the one serving. The address comes from Azure. Container Apps' own
   probes can't read a response body, so nothing checks it between deploys.

Steps 4 and 5 change the image and command through the REST API's PATCH
(`az rest`), because `az containerapp update` refuses a command argument that
starts with a dash, such as `-m`. The PATCH sends back the container as Azure
has it with only those fields changed, so its env vars, secret reference and
resources stay as they are.

**When a run fails**, GitHub emails whoever triggered it: the person who
merged, or who started it by hand. Open the run and read the failed step.

### Running it by hand

**Actions → Deploy to Azure → Run workflow**, with branch `main`, or
`gh workflow run deploy-azure.yml --ref main` and then `gh run watch`. It
builds `main` as it is now and deploys it, migrations included. A run from
any other branch stops before its first step: the environment admits `main`
only.

### Rolling back

**The usual way:** revert the change on `main` with a pull request. Its
merge deploys the reverted code, here as on Render.

**Faster, while that pull request waits:** point the app at an earlier
build. Every build is tagged with its commit's SHA:

```bash
az containerapp update -n keel -g rg-keel --image ghcr.io/atreyeed02/keel:<earlier commit's full SHA>
curl -s https://<keel's address>/health
```

That changes the image only; the command, port and env vars stay. The
earlier tags are on the package's page on GitHub, and each deploy's run
shows the commit it built. `main` still holds the bad change, so the next
deploy brings it back unless it has been reverted.

**Don't downgrade the schema.** Migrations work with the previous version of
the code (`docs/DEVELOPMENT.md`, rule 1), so an earlier image runs on the
current schema. `alembic downgrade` isn't part of a rollback.

### Open items

- **`FORWARDED_ALLOW_IPS` is still to be measured.** It isn't set, so the
  default, `127.0.0.1`, applies. Container Apps' ingress is a different
  chain from Render's: no Cloudflare, and a proxy whose address hasn't been
  observed. Until it is set, every visitor may share one write allowance and
  log line address, and `scheme` may read `http`. Redirects are relative,
  so they're unaffected. Observe the chain on the deployed app, set the
  variable on the Container App, then run the forged-header check
  ("Check it after a deploy", above) against the app's address, reading
  the `request.completed` line from
  `az containerapp logs show -n keel -g rg-keel --follow` while it runs
  (below). Never `*`: the app refuses it.
- **Render remains live until the cutover**, and both copies use the same
  database. The nightly reset resets it for both.
- **Don't merge a migration while both run against Neon.** The merge starts
  Render's deploy, which migrates when it starts, and this workflow's
  migration at about the same time. Alembic takes no lock, so both could
  apply the same migration at once. Hold pull requests with migrations
  until the cutover.
- **No logs are kept.** With logs destination `none`, a container's output
  goes nowhere once it stops: a failed migration shows only its status.
  `az containerapp logs show -n keel -g rg-keel --follow` streams a running
  replica's output. Keeping logs needs a Log Analytics workspace, which
  costs money.

## Pre-deploy checklist

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

## Logs

Structured JSON logs, one object per line, on the `keel` logger (level
from `LOG_LEVEL`). Every request gets an id, taken from a well-formed
`X-Request-ID` header or generated, and returned in the response's
`X-Request-ID` header. Ledger writes log after they commit:

```json
{"event": "transaction.posted", "request_id": "7d92…", "transaction_id": "2e1c…",
 "idempotency_key": "invoice-1001", "entry_count": 2, "account_ids": ["39fc…", "e285…"]}
```

Also logged: `transaction.replayed`, `transaction.rejected`,
`idempotency.conflict`, `ledger.full`, `account.created`, `request.completed`,
`db.ping_failed` (with the password masked) and, at start, `db.ssl_mode_conflict`
when `DATABASE_SSL` and the URL's `sslmode` disagree. There are no metrics
and no tracing.
