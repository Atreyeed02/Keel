import mimetypes
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from itertools import zip_longest
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from starlette.types import Scope

from app.api.accounts import router as accounts_api_router
from app.api.errors import register_api_error_handlers
from app.api.health import router as health_router
from app.api.transactions import router as transactions_api_router
from app.client_address import ClientAddressMiddleware
from app.config import settings
from app.db.engine import engine
from app.db.schema import accounts, events, idempotency_keys, ledger_entries, transactions
from app.domain.accounts import (
    ACCOUNT_TYPES,
    InvalidAccountError,
    create_account_record,
    validate_account,
)
from app.domain.capacity import LedgerFullError
from app.domain.idempotency import (
    IdempotencyConflictError,
    post_transaction_once,
    request_fingerprint,
)
from app.domain.ledger import (
    EntryAccountError,
    EntryInput,
    UnbalancedTransactionError,
    assert_balanced,
    validate_description,
)
from app.domain.reads import account_balances, currency_totals, transaction_with_entries
from app.glossary import GLOSSARY
from app.observability import (
    configure_logging,
    log_account_created,
    log_idempotency_conflict,
    log_ledger_full,
    log_transaction,
    log_transaction_rejected,
    request_context_middleware,
)
from app.posting_messages import (
    ALREADY_POSTED,
    PANEL_TEXT,
    Problem,
    account_problems,
    balance_panel,
    changed_after_posting,
    check_line,
    description_too_long,
    fewer_than_two,
    incomplete,
    ledger_full,
    rate_limited,
    too_large,
    unbalanced,
)
from app.ratelimit import RateLimiter, WriteRateLimitMiddleware
from app.security import BodySizeLimitMiddleware, SecurityHeadersMiddleware

configure_logging(settings.log_level)

app = FastAPI(
    title=settings.app_name,
    description="Event-sourced, double-entry ledger service.",
    version="0.1.0",
)
# One per process: the counts are in memory (app/ratelimit.py).
write_limiter = RateLimiter(settings.write_rate_limit, settings.write_rate_window_seconds)


def _refusal_page(scope: Scope, refused: str, numbers: dict[str, float]) -> bytes:
    """
    A refusal the write limit or the body limit makes before the app runs, as
    a page with the site's header, for a form rather than the JSON API. The
    words are app/posting_messages.py's.
    """
    problem = {"rate_limited": rate_limited, "too_large": too_large}[refused](**numbers)
    page = templates.get_template("refused.html")
    return page.render(request=Request(scope), problem=problem).encode()


# Starlette runs the middleware added last first. The rate limit and the body
# limit sit inside the request log, so a 429 or a 413 is logged like any other
# response; the rate limit comes first, so a client over it is refused before
# its body is looked at. The security headers sit outside everything, so every
# response gets them, a 413 or a 429 included.
app.add_middleware(
    BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes, page=_refusal_page
)
app.add_middleware(WriteRateLimitMiddleware, limiter=write_limiter, page=_refusal_page)
app.middleware("http")(request_context_middleware)
app.add_middleware(SecurityHeadersMiddleware)
# What `python -m app.serve` runs: the app behind the one place that turns
# forwarded headers into the client and scheme (app/client_address.py), so
# the log, the rate limit and redirects all see them. Wrapped rather than
# added with add_middleware, so tests can put `app` behind the same middleware
# with other trusted lists.
served = ClientAddressMiddleware(app, trusted_hosts=settings.forwarded_allow_ips)
app.include_router(health_router)
# The JSON API (app/api/): same domain layer as the pages below, with its own
# error shape under /api/.
app.include_router(accounts_api_router)
app.include_router(transactions_api_router)
register_api_error_handlers(app)
BASE_DIR = Path(__file__).resolve().parent
# StaticFiles takes types from the mimetypes module, which may not know .woff2
# (python:3.12-slim ships no /etc/mime.types). The pages' fonts are woff2.
mimetypes.add_type("font/woff2", ".woff2")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _money(value: Decimal | None) -> str:
    return f"{(value or Decimal('0')):,.2f}"


def _utc(value: datetime, seconds: bool = False) -> str:
    """A timestamp as the pages show it: in UTC, e.g. "Oct 5, 2026 · 14:02 UTC"."""
    value = value.astimezone(UTC)
    clock = f"{value:%H:%M:%S}" if seconds else f"{value:%H:%M}"
    return f"{value:%b} {value.day}, {value.year} · {clock} UTC"


# Backslash is the conventional choice and what Postgres assumes by default,
# but LIKE/ILIKE only honour it when the query says so, hence the explicit
# ESCAPE clause at the call site.
_LIKE_ESCAPE = "\\"


def _like_contains(term: str) -> str:
    """
    Build a `%term%` pattern that matches `term` literally.

    `%` and `_` are LIKE metacharacters — without this, searching for
    "50% off" means "50, then anything, then ' off'", and a lone "%" or "_"
    matches every row. Each metacharacter is prefixed with the escape
    character, which the query then declares via `ESCAPE`.

    The escape character is escaped first, so the backslashes added for `%`
    and `_` in the following passes are not themselves escaped again.
    """
    for char in (_LIKE_ESCAPE, "%", "_"):
        term = term.replace(char, _LIKE_ESCAPE + char)
    return f"%{term}%"


def _utc_midnight(day: date) -> datetime:
    """
    The instant `day` starts in UTC.

    The date filters compare against this rather than the bare date. Given a
    date, Postgres casts it to a timestamptz in the *session's* time zone, so
    the same filter would select different rows depending on how the server
    was configured. The pages show timestamps in UTC, so UTC days are the
    ones a person reading them means.
    """
    return datetime.combine(day, time.min, tzinfo=UTC)


def _transaction_rows():
    """
    The select behind every transaction listing: id, description, when, how
    many entries and how much moved. The overview's "recent transactions"
    table and the /transactions page both build on this, so the two cannot
    drift into showing different numbers for the same row.

    Callers add their own filtering, ordering and limit.
    """
    return (
        select(
            transactions.c.id,
            transactions.c.description,
            transactions.c.created_at,
            func.coalesce(func.sum(ledger_entries.c.amount), 0).label("entry_volume"),
            func.count(ledger_entries.c.id).label("entry_count"),
        )
        .outerjoin(ledger_entries, ledger_entries.c.transaction_id == transactions.c.id)
        .group_by(
            transactions.c.id,
            transactions.c.sequence,
            transactions.c.description,
            transactions.c.created_at,
        )
    )


templates.env.filters["money"] = _money
templates.env.filters["utc"] = _utc
# A function, not a value, so the notice follows the setting at render time. The
# pages get this one flag, never the settings object with its database URL.
templates.env.globals["is_demo"] = lambda: settings.is_demo
# The terms the pages define in place (app/glossary.py, the `term` macro).
templates.env.globals["glossary"] = GLOSSARY


# A new form's two lines: one each side.
NEW_LINES = [
    {"account_id": "", "entry_type": "debit", "amount": "", "currency": "USD"},
    {"account_id": "", "entry_type": "credit", "amount": "", "currency": "USD"},
]


def _form_lines(
    account_id: list[str], entry_type: list[str], amount: list[str], currency: list[str]
) -> tuple[list[dict[str, str]], bool]:
    """
    The posting form's lines, one dict per line, and whether every line
    arrived whole. A browser always sends all four fields for every line; a
    request that doesn't is padded with blanks rather than refused outright,
    so the form can come back with what did arrive.
    """
    columns = (account_id, entry_type, amount, currency)
    complete = len({len(column) for column in columns}) == 1
    lines = [
        {"account_id": a, "entry_type": k, "amount": v, "currency": c.upper()}
        for a, k, v, c in zip_longest(*columns, fillvalue="")
    ]
    return lines, complete


async def _form_context(lines: list[dict[str, str]] | None = None) -> dict[str, Any]:
    async with engine.connect() as conn:
        account_rows = (
            (
                await conn.execute(
                    select(accounts).order_by(accounts.c.account_type, accounts.c.name)
                )
            )
            .mappings()
            .all()
        )
    lines = lines or [dict(line) for line in NEW_LINES]
    return {
        "accounts": account_rows,
        "entries": lines,
        # the live balance panel as the page first shows it, and the sentences
        # the page hands static/js/post-transaction.js to keep it current
        "panel": balance_panel(lines),
        "panel_text": PANEL_TEXT,
    }


async def _posted_under(key: str) -> uuid.UUID | None:
    """The transaction a submission key has already posted, if any."""
    async with engine.connect() as conn:
        body = await conn.scalar(
            select(idempotency_keys.c.response_body).where(idempotency_keys.c.key == key)
        )
    return uuid.UUID(body["transaction_id"]) if body and body.get("transaction_id") else None


@app.get("/", response_class=HTMLResponse)
async def read_overview(request: Request):
    async with engine.connect() as conn:
        balances = await account_balances(conn)
        totals = await currency_totals(conn)
        recent = (
            (
                await conn.execute(
                    _transaction_rows().order_by(transactions.c.sequence.desc()).limit(10)
                )
            )
            .mappings()
            .all()
        )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for account in balances:
        grouped[account["account_type"]].append(account)
    return templates.TemplateResponse(
        request=request,
        name="overview.html",
        context={
            "accounts_by_type": grouped,
            "totals_by_currency": totals,
            "recent_transactions": recent,
        },
    )


@app.get("/event-log", response_class=HTMLResponse)
async def read_event_log(request: Request, page: int = Query(1, ge=1)):
    page_size = 25
    async with engine.connect() as conn:
        total = await conn.scalar(select(func.count()).select_from(events))
        rows = (
            (
                await conn.execute(
                    select(events)
                    .order_by(events.c.sequence.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .mappings()
            .all()
        )
    return templates.TemplateResponse(
        request=request,
        name="event_log.html",
        context={"events": rows, "page": page, "page_size": page_size, "total": total or 0},
    )


@app.get("/transactions", response_class=HTMLResponse)
async def read_transactions(
    request: Request,
    page: int = Query(1, ge=1),
    q: str | None = Query(None, description="case-insensitive substring of the description"),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
):
    """
    The full transaction list, paginated the same way /event-log is.

    Filters are optional and combine with AND. They are applied to the count
    as well as the page, so "N transactions" describes the filtered set
    rather than the table, and they are threaded back into the pager links
    so paging does not silently drop the filter.
    """
    page_size = 25
    q = (q or "").strip() or None

    conditions = []
    if q:
        conditions.append(
            transactions.c.description.ilike(_like_contains(q), escape=_LIKE_ESCAPE)
        )
    if date_from:
        conditions.append(transactions.c.created_at >= _utc_midnight(date_from))
    if date_to:
        # created_at is a timestamp; a bare `<= date_to` would exclude
        # everything after midnight on the closing day, so the range is
        # half-open against the following day instead.
        conditions.append(transactions.c.created_at < _utc_midnight(date_to + timedelta(days=1)))

    count_stmt = select(func.count()).select_from(transactions)
    listing = _transaction_rows()
    for condition in conditions:
        count_stmt = count_stmt.where(condition)
        listing = listing.where(condition)

    async with engine.connect() as conn:
        total = await conn.scalar(count_stmt)
        rows = (
            (
                await conn.execute(
                    listing.order_by(transactions.c.sequence.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .mappings()
            .all()
        )

    active = {
        key: value
        for key, value in (("q", q), ("date_from", date_from), ("date_to", date_to))
        if value
    }
    return templates.TemplateResponse(
        request=request,
        name="transactions.html",
        context={
            "transactions": rows,
            "page": page,
            "page_size": page_size,
            "total": total or 0,
            "q": q or "",
            "date_from": date_from.isoformat() if date_from else "",
            "date_to": date_to.isoformat() if date_to else "",
            # pre-encoded so the pager can append it without rebuilding the
            # filter state in the template
            "filter_qs": urlencode({k: str(v) for k, v in active.items()}),
            "is_filtered": bool(active),
        },
    )


@app.get("/learn", response_class=HTMLResponse)
async def read_learn(request: Request):
    """How double-entry works. Fixed text: it never touches the database."""
    return templates.TemplateResponse(request=request, name="learn.html")


@app.get("/accounts/new", response_class=HTMLResponse)
async def read_new_account(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="account_new.html",
        context={
            "account": {"name": "", "account_type": "asset", "currency": "USD"},
            "account_types": ACCOUNT_TYPES,
        },
    )


@app.post("/accounts", response_class=HTMLResponse)
async def create_account(
    request: Request,
    name: str = Form(""),
    account_type: str = Form(""),
    currency: str = Form(""),
):
    raw_account = {"name": name, "account_type": account_type, "currency": currency}

    def refused(message: str, status_code: int):
        return templates.TemplateResponse(
            request=request,
            name="account_new.html",
            context={"account": raw_account, "account_types": ACCOUNT_TYPES, "error": message},
            status_code=status_code,
        )

    try:
        account = validate_account(raw_account)
    except InvalidAccountError as exc:
        return refused(str(exc), 422)
    try:
        async with engine.begin() as conn:
            account_id = await create_account_record(
                conn, account, max_accounts=settings.max_accounts
            )
    except LedgerFullError as exc:
        log_ledger_full(str(exc))
        return refused(str(exc), 409)
    # after the commit, so the line only ever describes an account that exists
    log_account_created(account_id, account.account_type, account.currency)
    return RedirectResponse(url="/", status_code=302)


@app.get("/post-transaction", response_class=HTMLResponse)
async def read_post_transaction(
    request: Request,
    description: str = Query(""),
    submission_key: str | None = Query(None),
    account_id: list[str] = Query([]),
    entry_type: list[str] = Query([]),
    amount: list[str] = Query([]),
    currency: list[str] = Query([]),
    add_line: str | None = Query(None),
    remove_line: int | None = Query(None),
):
    """
    The posting form. With JavaScript, "Add line" and "Remove" work in the
    page. Without it they are submit buttons with formmethod="get": they land
    here carrying what has been typed, and the form comes back with a line
    more or fewer. Nothing is posted, and a GET is not counted by the write
    limit. The typed values ride in the query string, which no log records:
    request.completed logs the path (app/observability.py), and uvicorn's
    access log is off (app/serve.py).
    """
    lines, _ = _form_lines(account_id, entry_type, amount, currency)
    if add_line is not None:
        lines = (lines or [dict(line) for line in NEW_LINES]) + [dict(NEW_LINES[0])]
    if remove_line is not None and len(lines) > 2 and 1 <= remove_line <= len(lines):
        del lines[remove_line - 1]
    context = await _form_context(lines)
    context.update(
        {"description": description, "submission_key": submission_key or str(uuid.uuid4())}
    )
    return templates.TemplateResponse(
        request=request, name="post_transaction.html", context=context
    )


@app.post("/post-transaction", response_class=HTMLResponse)
async def submit_post_transaction(
    request: Request,
    description: str = Form(""),
    submission_key: str | None = Form(None),
    account_id: list[str] = Form([]),
    entry_type: list[str] = Form([]),
    amount: list[str] = Form([]),
    currency: list[str] = Form([]),
):
    """
    Post a transaction from the form. The domain decides: EntryInput per
    line, validate_description, assert_balanced, then post_transaction_once.
    app/posting_messages.py words every refusal for a person, and the form
    comes back with what was typed and each problem by its line.
    """
    lines, complete = _form_lines(account_id, entry_type, amount, currency)
    submission_key = submission_key or str(uuid.uuid4())

    async def refuse(
        problems: list[Problem],
        status_code: int = 422,
        key: str = submission_key,
        context: dict[str, Any] | None = None,
    ):
        context = context or await _form_context(lines)
        context.update({"problems": problems, "description": description, "submission_key": key})
        return templates.TemplateResponse(
            request=request, name="post_transaction.html", context=context, status_code=status_code
        )

    # Every line is checked, so the form reports all of them at once.
    problems: list[Problem] = [] if complete else [incomplete()]
    entries: list[EntryInput] = []
    for line, row in enumerate(lines, start=1):
        entry, found = check_line(line, row)
        problems += found
        if entry is not None:
            entries.append(entry)
    try:
        validate_description(description)
    except ValueError:
        problems.append(description_too_long(len(description)))
    if not problems:
        if len(entries) < 2:
            problems.append(fewer_than_two())
        else:
            try:
                assert_balanced(entries)
            except UnbalancedTransactionError:
                problems += unbalanced(entries)
    if problems:
        return await refuse(problems)

    fingerprint = request_fingerprint(description, lines)
    # post_transaction validates entries against the accounts they name,
    # which needs a connection — so those failures surface here rather than
    # in the pre-flight checks above. The engine.begin() context rolls the
    # whole thing back, idempotency claim included, before the form is
    # rendered again — so the same key can be resubmitted once it is fixed.
    try:
        async with engine.begin() as conn:
            transaction_id, replayed = await post_transaction_once(
                conn,
                submission_key,
                fingerprint,
                entries,
                description or None,
                max_transactions=settings.max_transactions,
            )
    except LedgerFullError as exc:
        log_ledger_full(str(exc))
        return await refuse([ledger_full(settings.max_transactions, settings.is_demo)], 409)
    except EntryAccountError as exc:
        log_transaction_rejected(submission_key, str(exc))
        context = await _form_context(lines)
        by_id = {row["id"]: row for row in context["accounts"]}
        found = account_problems(entries, by_id, settings.is_demo)
        return await refuse(found or [Problem(f"{exc}.")], context=context)
    except IdempotencyConflictError:
        log_idempotency_conflict(submission_key)
        # A new key, so posting this form again records a second transaction.
        posted = await _posted_under(submission_key)
        return await refuse([changed_after_posting(posted)], 409, key=str(uuid.uuid4()))
    log_transaction(transaction_id, submission_key, entries, replayed=replayed)
    # A replay lands on the transaction it already posted, which says so.
    already = "?already=1" if replayed else ""
    return RedirectResponse(url=f"/transaction-detail/{transaction_id}{already}", status_code=302)


@app.get("/transaction-detail/{transaction_id}", response_class=HTMLResponse)
async def read_transaction_detail(
    request: Request, transaction_id: uuid.UUID, already: bool = Query(False)
):
    async with engine.connect() as conn:
        transaction, entry_rows = await transaction_with_entries(conn, transaction_id)
        if transaction is None:
            raise HTTPException(status_code=404, detail="transaction not found")
        event = (
            (
                await conn.execute(
                    select(events).where(
                        events.c.aggregate_type == "transaction",
                        events.c.aggregate_id == transaction_id,
                    )
                )
            )
            .mappings()
            .first()
        )
    debits = [row for row in entry_rows if row["entry_type"] == "debit"]
    credits = [row for row in entry_rows if row["entry_type"] == "credit"]
    return templates.TemplateResponse(
        request=request,
        name="transaction_detail.html",
        context={
            "transaction": transaction,
            "debits": debits,
            "credits": credits,
            "debit_total": sum((row["amount"] for row in debits), Decimal()),
            "credit_total": sum((row["amount"] for row in credits), Decimal()),
            "event": event,
            # the posting form sent a resubmission here instead of posting twice
            "notice": ALREADY_POSTED if already else None,
        },
    )
