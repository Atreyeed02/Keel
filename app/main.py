import hashlib
import json
import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import case, func, insert, select

from app.api.health import router as health_router
from app.config import settings
from app.db.engine import engine
from app.db.schema import accounts, events, idempotency_keys, ledger_entries, transactions
from app.domain.accounts import ACCOUNT_TYPES, InvalidAccountError, validate_account
from app.domain.errors import describe_validation_error
from app.domain.ledger import (
    EntryAccountError,
    EntryInput,
    UnbalancedTransactionError,
    assert_balanced,
    post_transaction,
)

app = FastAPI(
    title=settings.app_name,
    description="Event-sourced, double-entry ledger service.",
    version="0.1.0",
)
app.include_router(health_router)
BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
NORMAL_DEBIT_TYPES = {"asset", "expense"}


def _money(value: Decimal | None) -> str:
    return f"{(value or Decimal('0')):,.2f}"


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
        .group_by(transactions.c.id, transactions.c.description, transactions.c.created_at)
    )


templates.env.filters["money"] = _money


async def _form_context(entries: list[dict[str, str]] | None = None) -> dict[str, Any]:
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
    return {
        "accounts": account_rows,
        "entries": entries
        or [
            {"account_id": "", "entry_type": "debit", "amount": "", "currency": "USD"},
            {"account_id": "", "entry_type": "credit", "amount": "", "currency": "USD"},
        ],
    }


@app.get("/", response_class=HTMLResponse)
async def read_overview(request: Request):
    debit = func.coalesce(
        func.sum(case((ledger_entries.c.entry_type == "debit", ledger_entries.c.amount), else_=0)),
        0,
    )
    credit = func.coalesce(
        func.sum(case((ledger_entries.c.entry_type == "credit", ledger_entries.c.amount), else_=0)),
        0,
    )
    balance = func.coalesce(
        func.sum(
            case(
                (ledger_entries.c.entry_type == "debit", ledger_entries.c.amount),
                else_=-ledger_entries.c.amount,
            )
        ),
        0,
    )
    async with engine.connect() as conn:
        account_rows = (
            (
                await conn.execute(
                    select(
                        accounts.c.id,
                        accounts.c.name,
                        accounts.c.account_type,
                        accounts.c.currency,
                        debit.label("debits"),
                        credit.label("credits"),
                        balance.label("raw_balance"),
                    )
                    .outerjoin(ledger_entries, ledger_entries.c.account_id == accounts.c.id)
                    .group_by(
                        accounts.c.id, accounts.c.name, accounts.c.account_type, accounts.c.currency
                    )
                    .order_by(accounts.c.account_type, accounts.c.name)
                )
            )
            .mappings()
            .all()
        )
        # Grouped by currency, not summed across all of them: adding USD
        # to EUR produces a real number that means nothing. Entries are
        # now guaranteed to carry their account's currency, so grouping
        # on the entry column gives one honest total per currency.
        totals = (
            (
                await conn.execute(
                    select(
                        ledger_entries.c.currency,
                        debit.label("debits"),
                        credit.label("credits"),
                    )
                    .group_by(ledger_entries.c.currency)
                    .order_by(ledger_entries.c.currency)
                )
            )
            .mappings()
            .all()
        )
        recent = (
            (
                await conn.execute(
                    _transaction_rows().order_by(transactions.c.created_at.desc()).limit(10)
                )
            )
            .mappings()
            .all()
        )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in account_rows:
        data = dict(row)
        data["normal_side"] = "debit" if row["account_type"] in NORMAL_DEBIT_TYPES else "credit"
        data["balance"] = (
            row["raw_balance"] if data["normal_side"] == "debit" else -row["raw_balance"]
        )
        grouped[row["account_type"]].append(data)
    return templates.TemplateResponse(
        request=request,
        name="overview.html",
        context={
            "accounts_by_type": grouped,
            "totals_by_currency": [
                {
                    "currency": row["currency"],
                    "debits": row["debits"],
                    "credits": row["credits"],
                    "delta": row["debits"] - row["credits"],
                }
                for row in totals
            ],
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
        conditions.append(transactions.c.description.ilike(f"%{q}%"))
    if date_from:
        conditions.append(transactions.c.created_at >= date_from)
    if date_to:
        # created_at is a timestamp; a bare `<= date_to` would exclude
        # everything after midnight on the closing day, so the range is
        # half-open against the following day instead.
        conditions.append(transactions.c.created_at < date_to + timedelta(days=1))

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
                    listing.order_by(transactions.c.created_at.desc())
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
    try:
        account = validate_account(raw_account)
    except InvalidAccountError as exc:
        return templates.TemplateResponse(
            request=request,
            name="account_new.html",
            context={
                "account": raw_account,
                "account_types": ACCOUNT_TYPES,
                "error": str(exc),
            },
            status_code=422,
        )
    async with engine.begin() as conn:
        await conn.execute(
            insert(accounts).values(
                id=uuid.uuid4(),
                name=account.name,
                account_type=account.account_type,
                currency=account.currency,
            )
        )
    return RedirectResponse(url="/", status_code=302)


@app.get("/post-transaction", response_class=HTMLResponse)
async def read_post_transaction(request: Request):
    context = await _form_context()
    context["submission_key"] = str(uuid.uuid4())
    return templates.TemplateResponse(
        request=request, name="post_transaction.html", context=context
    )


@app.post("/post-transaction", response_class=HTMLResponse)
async def submit_post_transaction(
    request: Request,
    description: str = Form(""),
    submission_key: str | None = Form(None),
    account_id: list[str] = Form(...),
    entry_type: list[str] = Form(...),
    amount: list[str] = Form(...),
    currency: list[str] = Form(...),
):
    raw_entries = [
        {"account_id": account, "entry_type": kind, "amount": value, "currency": ccy.upper()}
        for account, kind, value, ccy in zip(account_id, entry_type, amount, currency, strict=True)
    ]
    submission_key = submission_key or str(uuid.uuid4())

    async def invalid(message: str):
        context = await _form_context(raw_entries)
        context.update(
            {"error": message, "description": description, "submission_key": submission_key}
        )
        return templates.TemplateResponse(
            request=request, name="post_transaction.html", context=context, status_code=422
        )

    try:
        entries = [EntryInput.model_validate(row) for row in raw_entries]
        assert_balanced(entries)
        if len(entries) < 2:
            raise ValueError("a transaction needs at least two entries")
    except (ValidationError, UnbalancedTransactionError, ValueError) as exc:
        # UnbalancedTransactionError and the bare ValueError already carry a
        # single readable sentence. A raw pydantic ValidationError does not —
        # str() on one is a multi-line dump — so it gets flattened first.
        return await invalid(
            describe_validation_error(exc) if isinstance(exc, ValidationError) else str(exc)
        )
    request_hash = hashlib.sha256(
        json.dumps({"description": description, "entries": raw_entries}, sort_keys=True).encode()
    ).hexdigest()
    # post_transaction validates entries against the accounts they name,
    # which needs a connection — so those failures surface here rather than
    # in the pre-flight block above. The engine.begin() context rolls the
    # whole thing back before the error page is rendered.
    try:
        async with engine.begin() as conn:
            saved = (
                (
                    await conn.execute(
                        select(
                            idempotency_keys.c.request_hash, idempotency_keys.c.response_body
                        ).where(idempotency_keys.c.key == submission_key)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if saved:
                if saved["request_hash"] != request_hash:
                    raise HTTPException(
                        status_code=409, detail="submission key was used for another request"
                    )
                transaction_id = uuid.UUID(saved["response_body"]["transaction_id"])
            else:
                transaction_id = await post_transaction(conn, entries, description or None)
                await conn.execute(
                    insert(idempotency_keys).values(
                        key=submission_key,
                        request_hash=request_hash,
                        response_body={"transaction_id": str(transaction_id)},
                        response_status="302",
                    )
                )
    except EntryAccountError as exc:
        return await invalid(str(exc))
    return RedirectResponse(url=f"/transaction-detail/{transaction_id}", status_code=302)


@app.get("/transaction-detail/{transaction_id}", response_class=HTMLResponse)
async def read_transaction_detail(request: Request, transaction_id: uuid.UUID):
    async with engine.connect() as conn:
        transaction = (
            (await conn.execute(select(transactions).where(transactions.c.id == transaction_id)))
            .mappings()
            .one_or_none()
        )
        if transaction is None:
            raise HTTPException(status_code=404, detail="transaction not found")
        entry_rows = (
            (
                await conn.execute(
                    select(ledger_entries, accounts.c.name, accounts.c.account_type)
                    .join(accounts, accounts.c.id == ledger_entries.c.account_id)
                    .where(ledger_entries.c.transaction_id == transaction_id)
                    .order_by(ledger_entries.c.created_at, ledger_entries.c.id)
                )
            )
            .mappings()
            .all()
        )
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
        },
    )
