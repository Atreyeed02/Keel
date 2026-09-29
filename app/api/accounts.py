"""
`/api/accounts`: create an account, list accounts with their balances.

Both go through the same domain functions as the HTML pages:
`validate_account` and `create_account_record` for the write,
`account_balances` for the read.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.errors import ApiError, read_json_object
from app.api.serialize import account_json
from app.config import settings
from app.db.engine import engine
from app.domain.accounts import (
    ACCOUNT_TYPES,
    InvalidAccountError,
    create_account_record,
    validate_account,
)
from app.domain.capacity import LedgerFullError
from app.domain.reads import account_balances
from app.observability import log_account_created, log_ledger_full

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

ACCOUNT_FIELDS = ("name", "account_type", "currency")

_CREATE_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": list(ACCOUNT_FIELDS),
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string", "maxLength": 255},
                        "account_type": {"type": "string", "enum": list(ACCOUNT_TYPES)},
                        "currency": {"type": "string", "example": "USD"},
                    },
                }
            }
        },
    }
}


@router.post("", status_code=201, openapi_extra=_CREATE_BODY)
async def create_account(request: Request):
    payload = await read_json_object(request)
    unexpected = sorted(set(payload) - set(ACCOUNT_FIELDS))
    if unexpected:
        raise ApiError(422, "validation_error", f"unexpected field(s): {', '.join(unexpected)}")
    try:
        account = validate_account(payload)
    except InvalidAccountError as exc:
        raise ApiError(422, "validation_error", str(exc)) from exc

    try:
        async with engine.begin() as conn:
            account_id = await create_account_record(
                conn, account, max_accounts=settings.max_accounts
            )
    except LedgerFullError as exc:
        log_ledger_full(str(exc))
        raise ApiError(409, "ledger_full", str(exc)) from exc
    log_account_created(account_id, account.account_type, account.currency)

    async with engine.connect() as conn:
        (row,) = await account_balances(conn, account_id)
    return JSONResponse(account_json(row), status_code=201)


@router.get("")
async def list_accounts():
    async with engine.connect() as conn:
        rows = await account_balances(conn)
    return {"accounts": [account_json(row) for row in rows]}
