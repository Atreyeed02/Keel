"""
`/api/transactions`: post a transaction idempotently, read one back.

Posting reuses the form route's machinery unchanged: `EntryInput`'s
validators, `assert_balanced`, `validate_description`, and
`post_transaction_once` inside one database transaction. What differs is
the edges:

- the idempotency key comes from the `Idempotency-Key` header and is
  required;
- the fingerprint is `entries_fingerprint`, over the request's meaning
  rather than its bytes (ARCHITECTURE.md §4 has the reasoning);
- the answer is 201 for a first post and 200 for a replay, both carrying
  the transaction, with `Idempotent-Replayed` saying which it was.

Everything that can be checked without the database is checked first,
header then body, so a malformed request never opens a connection.
"""

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from app.api.errors import ApiError, read_json_object
from app.api.serialize import transaction_json
from app.config import settings
from app.db.engine import engine
from app.domain.capacity import LedgerFullError
from app.domain.errors import describe_validation_error
from app.domain.idempotency import (
    IdempotencyConflictError,
    entries_fingerprint,
    post_transaction_once,
)
from app.domain.ledger import (
    EntryAccountError,
    EntryInput,
    UnbalancedTransactionError,
    assert_balanced,
    validate_description,
)
from app.domain.reads import transaction_with_entries
from app.observability import (
    log_idempotency_conflict,
    log_ledger_full,
    log_transaction,
    log_transaction_rejected,
)

router = APIRouter(prefix="/api/transactions", tags=["transactions"])

# Visible ASCII, no spaces, and no longer than `idempotency_keys.key`. A
# UUID is the obvious choice, but any opaque token of this shape works.
_IDEMPOTENCY_KEY = re.compile(r"[\x21-\x7e]{1,255}")


class ApiEntryInput(EntryInput):
    """
    `EntryInput` with two JSON-specific rules on top of its own validators.

    The amount must arrive as a string. A JSON number has usually been
    through a binary float somewhere on the client, where 0.1 is not 0.1;
    a string cannot have been. The currency is upper-cased, as the form
    route does.
    """

    model_config = ConfigDict(extra="forbid")

    @field_validator("amount", mode="before")
    @classmethod
    def amount_is_a_string(cls, value):
        if not isinstance(value, str):
            raise ValueError('must be a string such as "100.00", not a JSON number')
        return value

    @field_validator("currency", mode="before")
    @classmethod
    def upper_case_currency(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


class TransactionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    entries: list[ApiEntryInput]


_POST_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["entries"],
                    "additionalProperties": False,
                    "properties": {
                        "description": {"type": ["string", "null"], "maxLength": 512},
                        "entries": {
                            "type": "array",
                            "minItems": 2,
                            "items": {
                                "type": "object",
                                "required": ["account_id", "entry_type", "amount", "currency"],
                                "additionalProperties": False,
                                "properties": {
                                    "account_id": {"type": "string", "format": "uuid"},
                                    "entry_type": {"type": "string", "enum": ["debit", "credit"]},
                                    "amount": {
                                        "type": "string",
                                        "pattern": r"^\d+(\.\d{1,2})?$",
                                        "example": "100.00",
                                    },
                                    "currency": {"type": "string", "example": "USD"},
                                },
                            },
                        },
                    },
                }
            }
        },
    }
}


def _require_idempotency_key(key: str | None) -> str:
    if key is None:
        raise ApiError(400, "missing_idempotency_key", "the Idempotency-Key header is required")
    if not _IDEMPOTENCY_KEY.fullmatch(key):
        raise ApiError(
            400,
            "invalid_idempotency_key",
            "Idempotency-Key must be 1 to 255 visible ASCII characters with no spaces",
        )
    return key


@router.post("", status_code=201, openapi_extra=_POST_BODY)
async def post_transaction_api(
    request: Request,
    # Optional to FastAPI only so that a missing key is our 400, not its 422.
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description="Required. 1-255 visible ASCII characters; a UUID is a good choice.",
        ),
    ] = None,
):
    key = _require_idempotency_key(idempotency_key)
    payload = await read_json_object(request)
    try:
        body = TransactionRequest.model_validate(payload)
    except ValidationError as exc:
        raise ApiError(422, "validation_error", describe_validation_error(exc)) from exc

    entries: list[EntryInput] = list(body.entries)
    description = body.description or None
    try:
        if len(entries) < 2:
            raise ValueError("a transaction needs at least two entries")
        validate_description(description)
        assert_balanced(entries)
    except UnbalancedTransactionError as exc:
        raise ApiError(422, "unbalanced_transaction", str(exc)) from exc
    except ValueError as exc:
        raise ApiError(422, "validation_error", str(exc)) from exc

    # Same shape as the form route: the claim, the posting and the stored
    # response commit together, and any failure rolls the claim back so
    # the key can be retried once the request is fixed.
    try:
        async with engine.begin() as conn:
            transaction_id, replayed = await post_transaction_once(
                conn,
                key,
                entries_fingerprint(description, entries),
                entries,
                description,
                max_transactions=settings.max_transactions,
            )
    except LedgerFullError as exc:
        log_ledger_full(str(exc))
        raise ApiError(409, "ledger_full", str(exc)) from exc
    except EntryAccountError as exc:
        log_transaction_rejected(key, str(exc))
        raise ApiError(422, "invalid_accounts", str(exc)) from exc
    except IdempotencyConflictError as exc:
        log_idempotency_conflict(key)
        raise ApiError(
            409, "idempotency_conflict", "Idempotency-Key was already used for a different request"
        ) from exc
    log_transaction(transaction_id, key, entries, replayed=replayed)

    async with engine.connect() as conn:
        transaction, rows = await transaction_with_entries(conn, transaction_id)
    return JSONResponse(
        transaction_json(transaction, rows),
        status_code=200 if replayed else 201,
        headers={
            "Location": f"/api/transactions/{transaction_id}",
            "Idempotent-Replayed": "true" if replayed else "false",
        },
    )


@router.get("/{transaction_id}")
async def get_transaction(transaction_id: str):
    # Taken as a string so that an id which is not even a UUID is the same
    # 404 as a well-formed one that matches nothing: neither exists.
    try:
        parsed = uuid.UUID(transaction_id)
    except ValueError:
        parsed = None
    if parsed is not None:
        async with engine.connect() as conn:
            transaction, rows = await transaction_with_entries(conn, parsed)
        if transaction is not None:
            return transaction_json(transaction, rows)
    raise ApiError(404, "not_found", f"no transaction with id {transaction_id!r}")
