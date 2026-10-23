# API

Two interfaces over the same domain layer: a **JSON API** under `/api/`
and the **server-rendered HTML pages**. FastAPI's generated docs at
`/docs` list every route. Every `GET` route also answers `HEAD`, with the
same status and headers and no body.

## JSON

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
[docs/ARCHITECTURE.md §5.16](ARCHITECTURE.md#516-appapi--the-json-api).

## HTML

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | the accounting equation per currency (Assets = Liabilities + Equity + (Revenue − Expenses)), totals per currency, balances per account (normal-side signed), recent transactions with each one's amount per currency; on the demo, three "try it" steps that open the posting form filled in |
| `GET` | `/transactions` | all transactions, newest first, one card each: its number, accounts by side and amount per currency (its debit total, which is also its credit total). `q` (description search), `date_from`, `date_to` (left empty, no date filter), `page`; the filters in force are chips, each a link without it |
| `GET` | `/transaction-detail/{id}` | one transaction's debit and credit entries, what each does to its account, the totals and the balance per currency, and its event, linked to its page of the event log; `?already=1` after a resubmission says it was already posted. An unknown id is a 404 page |
| `GET` | `/account-detail/{id}` | one account as a T-account: its debits on the left and credits on the right, oldest first, 25 a page (`page`), each with its transaction and the accounts on its other side; why its normal side; the sum that gives its balance, below zero included; its event. An unknown id is a 404 page |
| `GET` | `/event-log` | the event log as a timeline, newest first, 25 a page (`page`): each event in words from its own payload, with the raw event folded away under it, and how append-only events and rebuilds work |
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
