"""
Account creation: input validation, then the write.

Creating an account carries no double-entry invariant, so there is no
idempotency handling here. It does get an event: `create_account_record`
appends `account.created` alongside the `accounts` row, exactly as
`post_transaction` appends `transaction.posted`. Without it the event log
would not contain the accounts every entry points at, and the read model
could never be rebuilt from the log alone — see `app/domain/rebuild.py`.

`account_type` is closed over the five classical types the schema
comments reference; the balance-sign logic in the overview page keys
off it, so an unrecognised value would silently render a wrong balance.
"""

import uuid

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.schema import accounts, events
from app.domain.account_types import ACCOUNT_TYPES  # re-exported for the account form
from app.domain.capacity import assert_room
from app.domain.errors import describe_validation_error
from app.domain.event_versions import CURRENT_VERSION


class InvalidAccountError(ValueError):
    """Raised when submitted account data fails validation."""


class AccountInput(BaseModel):
    name: str = Field(max_length=255)
    account_type: str
    currency: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("is required")
        return v

    @field_validator("account_type")
    @classmethod
    def validate_account_type(cls, v: str) -> str:
        if v not in ACCOUNT_TYPES:
            raise ValueError(f"must be one of: {', '.join(ACCOUNT_TYPES)}")
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        v = v.strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError("must be a 3-letter code")
        return v


def validate_account(data: dict[str, str]) -> AccountInput:
    """Validate raw form data, raising a single readable error on failure."""
    try:
        return AccountInput.model_validate(data)
    except ValidationError as exc:
        raise InvalidAccountError(describe_validation_error(exc)) from exc


async def create_account_record(
    conn: AsyncConnection, account: AccountInput, *, max_accounts: int = 0
) -> uuid.UUID:
    """
    Write a validated account and its `account.created` event.

    Same contract as `post_transaction`: the caller owns the connection's
    transaction boundary. This issues statements but doesn't commit, so the
    row and its event land together or not at all.

    The event's `aggregate_id` is the account's id, which is what lets a
    rebuild recreate the account under the same id — every ledger entry
    that names it keeps pointing at the right row.

    Raises `LedgerFullError` if the ledger already holds `max_accounts`
    accounts. The routes pass `MAX_ACCOUNTS`; the scripts pass nothing, so
    seeding and backfilling are never capped.
    """
    await assert_room(conn, accounts, max_accounts, "account")
    account_id = uuid.uuid4()

    await conn.execute(
        insert(accounts).values(
            id=account_id,
            name=account.name,
            account_type=account.account_type,
            currency=account.currency,
        )
    )
    await conn.execute(
        insert(events).values(
            id=uuid.uuid4(),
            aggregate_type="account",
            aggregate_id=account_id,
            event_type="account.created",
            payload={
                "schema_version": CURRENT_VERSION["account.created"],
                "name": account.name,
                "account_type": account.account_type,
                "currency": account.currency,
            },
        )
    )

    return account_id
