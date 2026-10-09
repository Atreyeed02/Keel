# Keel — Build Status & Handoff

**As of 2026-10-10.** A snapshot of what is actually built, what is
verified, and where the next piece of work starts. For the *why* behind
the design — the accounting concepts, the event-sourcing rationale, a
file-by-file walkthrough — read `ARCHITECTURE.md` first; this document
does not repeat it.

---

## 1. Verified state, right now

Eighteen PRs are merged into `main`, with regular merge commits, and their
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
| [#12](https://github.com/Atreyeed02/Keel/pull/12) | `docs/status-scheduled-reset` | `2cfdc49` |
| [#13](https://github.com/Atreyeed02/Keel/pull/13) | `chore/pin-workflow-actions` | `deb637e` |
| [#14](https://github.com/Atreyeed02/Keel/pull/14) | `feat/ui-foundation` | `16006d3` |
| [#15](https://github.com/Atreyeed02/Keel/pull/15) | `feat/ui-learn` | `4484adc` |
| [#16](https://github.com/Atreyeed02/Keel/pull/16) | `feat/ui-posting-form` | `370e904` |
| [#17](https://github.com/Atreyeed02/Keel/pull/17) | `feat/ui-overview` | `d1effe4` |
| [#18](https://github.com/Atreyeed02/Keel/pull/18) | `feat/ui-transactions` | `fce1d7e` |

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

**`feat/ui-event-log`**, the redesign's sixth phase, turns the event log
into a timeline (§2, "HTTP"): each event in words from its own payload, the
raw event folded away under it, and a card on how the log works, the
nightly reset included. Checked on that branch; it adds no migration and no
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

### HTTP — seven server-rendered pages

All in `app/main.py`, Jinja2 on a shared `base.html`:
overview with the accounting equation per currency, per-account balances
and normal-side signs, the event log as a paginated timeline, filterable + paginated transaction list, posting form,
transaction detail with debit/credit columns, account creation, and
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
it (`app/event_log.py`). This is
a redesign done one PR per phase; §6 has the rest.

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
instead of mounting an empty directory over the image.
Since #5, `lint-and-test` also runs `pip-audit --strict`, and `docker-smoke`
fails if the container runs as root. A third, scheduled workflow, "Cloudflare
ranges", compares `app/client_address.py`'s copy of Cloudflare's address
ranges with the published lists every Monday. A fourth, "Demo
maintenance", resets the public demo every night (below). Since #13, all
three workflow files pin their actions by commit and run every job on
`ubuntu-24.04`.
Both jobs are required checks: the "Protect main" ruleset (§1) merges
nothing into `main` until they pass.

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
event-log endpoint yet (`ARCHITECTURE.md` §8 item 7).

**4. The UI redesign, phases 4–7**
One PR each, in the order §6 lists. Each keeps every page working, is
checked in light and dark at desktop and phone width, and claims nothing
the ledger does not do.

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
you are running 244 of 354 tests:

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
6. **Event log** as a timeline (`feat/ui-event-log`): each event in words
   from its own payload, the raw event folded away, and how append-only
   events and rebuilds work.
7. **Account detail**, a T-account per account.

Each phase brings the Stitch animations of the elements it builds (card
hovers, the balance banner's change of state, bar widths), under the same rules: CSS first, nothing endless, all
of it off under reduced motion, and every page working without JavaScript.
